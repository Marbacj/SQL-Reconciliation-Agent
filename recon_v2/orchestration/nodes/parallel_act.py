"""ParallelAct — 并发执行多个独立的 SQL 子任务。

设计目标：
    对于需要多张表的复杂对账场景（如"订单金额 vs 支付金额差异"），
    将独立的子查询并行执行，显著减少总耗时。

使用场景（plan 节点会生成 parallel 步骤）：
    - numeric_diff：两张表分别查询后做差值比较
    - multi_table_join：多张表各自预查后合并

Plan Step 格式：
    {
        "parallel": [
            {"action": "sql_runner", "sql": "SELECT ...", "alias": "order_result"},
            {"action": "sql_runner", "sql": "SELECT ...", "alias": "pay_result"},
        ]
    }

并发执行后，每个子任务的结果存入 state["parallel_results"][alias]，
后续 diff_calculator 步骤直接从 parallel_results 取数据。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState

logger = logging.getLogger(__name__)


# ── 单个子任务执行器 ────────────────────────────────────

async def _run_sub_task(
    task: Dict[str, Any],
    ctx,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """异步执行单个子任务，返回 {"alias": str, "success": bool, "rows": list, "error": str}。

    目前支持：
        action="sql_runner"  — 执行 SQL 查询
        action="schema_inspect" — 探查表结构（并发 PRAGMA）
    """
    alias = task.get("alias", f"task_{id(task)}")
    action = task.get("action", "sql_runner")
    t0 = time.time()

    try:
        if action == "sql_runner":
            result = await asyncio.wait_for(
                _exec_sql(task, ctx),
                timeout=timeout,
            )
        elif action == "schema_inspect":
            result = await asyncio.wait_for(
                _exec_schema_inspect(task, ctx),
                timeout=timeout,
            )
        else:
            result = {"success": False, "error": f"unsupported action: {action}"}

        elapsed = (time.time() - t0) * 1000
        logger.debug("parallel_act: sub-task '%s' done in %.1fms", alias, elapsed)
        return {"alias": alias, **result}

    except asyncio.TimeoutError:
        logger.warning("parallel_act: sub-task '%s' timed out after %.1fs", alias, timeout)
        return {"alias": alias, "success": False, "error": "timeout", "rows": []}
    except Exception as e:
        logger.error("parallel_act: sub-task '%s' failed: %s", alias, e)
        return {"alias": alias, "success": False, "error": str(e), "rows": []}


async def _exec_sql(task: Dict[str, Any], ctx) -> Dict[str, Any]:
    """在线程池里执行同步 sql_runner（避免阻塞 event loop）。"""
    sql = task.get("sql", "")
    if not sql:
        return {"success": False, "error": "empty sql", "rows": []}

    loop = asyncio.get_event_loop()

    def _sync_run():
        runner = ctx.tools.get("sql_runner") if ctx.tools else None
        if runner is None:
            # 降级：直接用 sqlite3
            import sqlite3
            conn = sqlite3.connect(ctx.db_path)
            try:
                cur = conn.execute(sql)
                rows = [list(r) for r in cur.fetchall()]
                cols = [d[0] for d in cur.description] if cur.description else []
                return {"success": True, "rows": rows, "columns": cols, "sql": sql}
            except Exception as e:
                return {"success": False, "error": str(e), "rows": [], "sql": sql}
            finally:
                conn.close()
        else:
            out = runner.run(sql=sql)
            return {
                "success": out.success,
                "rows": out.rows if out.rows else [],
                "columns": out.columns if hasattr(out, "columns") else [],
                "error": out.error or "",
                "sql": sql,
            }

    return await loop.run_in_executor(None, _sync_run)


async def _exec_schema_inspect(task: Dict[str, Any], ctx) -> Dict[str, Any]:
    """并发探查指定表的 schema（用于 Schema Linking 的表级并发）。"""
    table_name = task.get("table", "")
    if not table_name:
        return {"success": False, "error": "no table name", "schema": None}

    loop = asyncio.get_event_loop()

    def _sync_inspect():
        from recon_v2.tools.schema_inspector import inspect as inspect_schema
        schema = inspect_schema(db_path=ctx.db_path, adapter=None)
        matched = next((t for t in schema.tables if t.name == table_name), None)
        if matched is None:
            return {"success": False, "error": f"table {table_name} not found", "schema": None}
        return {"success": True, "schema": {
            "table_name": matched.name,
            "columns": [{"name": c.name, "type": c.col_type, "enum_values": c.enum_values}
                        for c in matched.columns],
        }}

    return await loop.run_in_executor(None, _sync_inspect)


# ── 主入口：并发执行一组子任务 ────────────────────────

async def execute_parallel_tasks(
    tasks: List[Dict[str, Any]],
    ctx,
    timeout_per_task: float = 30.0,
) -> Dict[str, Any]:
    """并发执行 tasks 列表，返回 {alias: result_dict} 映射。

    tasks 格式（plan 节点输出）：
        [
            {"action": "sql_runner", "sql": "...", "alias": "order_result"},
            {"action": "sql_runner", "sql": "...", "alias": "pay_result"},
        ]
    """
    if not tasks:
        return {}

    logger.info("parallel_act: launching %d sub-tasks concurrently", len(tasks))
    t0 = time.time()

    coros = [_run_sub_task(task, ctx, timeout=timeout_per_task) for task in tasks]
    results_list = await asyncio.gather(*coros, return_exceptions=False)

    elapsed = (time.time() - t0) * 1000
    success_count = sum(1 for r in results_list if r.get("success"))
    logger.info(
        "parallel_act: %d/%d tasks succeeded in %.1fms",
        success_count, len(tasks), elapsed,
    )

    return {r["alias"]: r for r in results_list}


# ── LangGraph Node 入口 ───────────────────────────────

def parallel_act_node(state: GraphState) -> GraphState:
    """LangGraph 节点：识别 plan_steps 中的 parallel 步骤并并发执行。

    plan_steps 中第一个含 "parallel" key 的步骤会被取出并发执行，
    结果写入 state["parallel_results"]。
    """
    ctx_id = state.get("ctx_id", "")
    ctx = get_ctx(ctx_id) if ctx_id else None
    if ctx is None:
        logger.error("parallel_act_node: ctx not found for id=%s", ctx_id)
        return state

    plan_steps = state.get("plan_steps", [])
    parallel_tasks = []

    # 从 plan_steps 中提取 parallel 步骤
    for step in plan_steps:
        if isinstance(step, dict) and "parallel" in step:
            parallel_tasks = step["parallel"]
            break
        # 也支持字符串步骤里标记 __parallel__ 的情况（legacy 兼容）
        if isinstance(step, str) and step.startswith("__parallel__"):
            break

    if not parallel_tasks:
        logger.debug("parallel_act_node: no parallel tasks found in plan_steps")
        return {**state, "parallel_results": state.get("parallel_results", {})}

    with span("parallel_act"):
        try:
            # 在同步 LangGraph 上下文里运行异步代码
            loop = asyncio.new_event_loop()
            results = loop.run_until_complete(
                execute_parallel_tasks(parallel_tasks, ctx)
            )
            loop.close()
        except Exception as e:
            logger.error("parallel_act_node: execution failed: %s", e)
            results = {}

    # 合并到已有的 parallel_results
    existing = state.get("parallel_results") or {}
    merged = {**existing, **results}

    # 记录 observations（让 observe 节点知道子任务结果）
    obs_list = [
        {
            "source": "parallel_act",
            "alias": alias,
            "success": r.get("success", False),
            "sql": r.get("sql", ""),
            "row_count": len(r.get("rows", [])),
            "error": r.get("error", ""),
        }
        for alias, r in results.items()
    ]

    logger.info(
        "parallel_act_node: stored %d results: %s",
        len(merged),
        list(merged.keys()),
    )

    return {
        **state,
        "parallel_results": merged,
        "observations": state.get("observations", []) + obs_list,
    }
