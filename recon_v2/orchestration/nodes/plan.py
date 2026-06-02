"""Plan node：根据 intent 决定走 ReAct (单步) 还是 Plan-Solve (多步)。

策略：
- simple_query → ReAct 单步
- 其他复杂意图 → Plan-Solve 输出 step list

Schema 信息来源：
- 实时 SchemaInspector.inspect() 查询 PRAGMA/DESC，不再依赖 RAG schema chunk
- RAG 仅保留业务规则/阈值/方言提示文档检索
"""

from __future__ import annotations

import logging
from typing import List

from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState
from recon_v2.tools.schema_inspector import SchemaInfo, inspect as inspect_schema

logger = logging.getLogger(__name__)


_PLAN_TEMPLATES = {
    "simple_query": ["1) 调 sql_runner 直接查询"],
    "multi_table_join": [
        "1) 调 sql_runner 执行 JOIN 查询",
        "2) 必要时调 report_generator 渲染",
    ],
    "time_window_recon": [
        "1) 调 sql_runner 拉左表（如 orders）",
        "2) 调 sql_runner 拉右表（如 payments）",
        "3) 调 diff_calculator 比较",
        "4) 调 report_generator 渲染",
    ],
    "numeric_diff": [
        "1) 调 sql_runner 拉左右两个数值列",
        "2) 调 diff_calculator 容差比较",
        "3) 调 report_generator 渲染差额列表",
    ],
}

# 需要走并行路径的意图集合（plan 节点据此决定是否调 _build_parallel_steps_with_llm）
_PARALLEL_INTENTS = {"numeric_diff", "time_window_recon", "multi_table_join", "boundary_edge"}


def _get_schema_info(ctx) -> SchemaInfo:
    """实时获取数据库 schema，优先使用 ctx 上已缓存的结果。"""
    # 缓存在 ctx 上，同一个 trace 内复用，避免多次 PRAGMA
    if hasattr(ctx, "_schema_info") and ctx._schema_info is not None:
        return ctx._schema_info

    try:
        adapter = None
        if ctx.tools:
            runner = ctx.tools.get("sql_runner")
            if runner and hasattr(runner, "adapter"):
                adapter = runner.adapter

        schema = inspect_schema(db_path=ctx.db_path, adapter=adapter)
        ctx._schema_info = schema
        return schema
    except Exception as e:
        logger.warning("SchemaInspector failed, schema hint will be empty: %s", e)
        return SchemaInfo()


def plan_node(state: GraphState) -> dict:
    ctx = get_ctx(state["ctx_id"])
    intent = state.get("intent", "simple_query")
    query = state["query"]

    with span("plan", attributes={"intent": intent}):
        # 决定模式
        if intent == "simple_query":
            ctx.mode = "react"
        else:
            ctx.mode = "plan_solve"

        steps = _PLAN_TEMPLATES.get(intent, _PLAN_TEMPLATES["simple_query"])

        # 对 numeric_diff / time_window_recon 等多表意图：LLM 有能力时生成并行步骤的具体 SQL
        # 先判断是否应该走并行路径
        use_parallel = intent in _PARALLEL_INTENTS and ctx.mode == "plan_solve"

        # 如有 LLM：由 LLM 重写更精细的 plan，schema 来自实时查询
        if ctx.llm is not None and ctx.mode == "plan_solve":
            try:
                schema_info = _get_schema_info(ctx)
                schema_hint = schema_info.to_prompt_str() if schema_info.tables else (
                    "Database: orders(id,user_id,amount,status,created_at), "
                    "refunds(id,order_id,amount,status,created_at), "
                    "payments(id,order_id,amount,channel,status,created_at)."
                )

                # RAG 仍用于业务规则/方言提示（不含 schema chunk）
                rag_hint = ""
                if ctx.rag is not None:
                    try:
                        rag_docs = ctx.rag.search(query, k=2)
                        if rag_docs:
                            rag_hint = "\nBusiness rules:\n" + "\n".join(
                                f"  - {d.text[:200]}" for d in rag_docs
                            )
                    except Exception:
                        pass

                msg = (
                    f"Query: {query}\nIntent: {intent}\n"
                    f"{schema_hint}\n"
                    "SQLite date rules: DATE('now'), DATE('now','-N days'), "
                    "strftime('%Y-%m',col). NO INTERVAL/CURDATE/NOW()."
                    f"{rag_hint}\n"
                    "Output 2-5 concise step descriptions to solve this SQL reconciliation task. "
                    "Each step on a new line, no numbering needed."
                )
                out = ctx.llm.chat(
                    messages=[
                        {"role": "system", "content": "You decompose a SQL reconciliation task into steps."},
                        {"role": "user", "content": msg},
                    ],
                    trace_id=ctx.trace_id,
                    temperature=0.0,
                    max_tokens=180,
                )
                lines = [l.strip() for l in out.content.split("\n") if l.strip()][:5]
                if lines:
                    steps = lines

                # 并行路径：让 LLM 为 parallel 步骤填充具体 SQL
                if use_parallel:
                    parallel_steps = _build_parallel_steps_with_llm(
                        intent=intent,
                        query=query,
                        schema_hint=schema_hint,
                        ctx=ctx,
                    )
                    if parallel_steps:
                        steps = parallel_steps
            except Exception as e:
                logger.warning("Plan LLM refine failed, use template: %s", e)

        ctx.step()
        return {
            "plan_steps": steps,
            "mode": ctx.mode,
            "step_counter": ctx.step_counter,
        }


def _build_parallel_steps_with_llm(
    intent: str,
    query: str,
    schema_hint: str,
    ctx,
) -> list:
    """让 LLM 动态决定需要查几张表，生成任意数量的并行子任务。

    LLM 自由分析 query 需要独立查哪些表（可以是 2 张也可以是 5 张），
    每张表生成一条独立 SQL，并发执行后再合并。

    若 LLM 调用失败或返回无效 JSON，返回空列表（触发 fallback 到串行模板）。
    """
    import json as _json

    # 获取 post_steps（diff/report 等后续步骤），根据 intent 决定
    _POST_STEPS = {
        "numeric_diff": [
            "diff_calculator: 对所有 parallel_results 做容差比较，找出差异行",
            "report_generator: 渲染差额列表",
        ],
        "time_window_recon": [
            "diff_calculator: 比较 parallel_results 中各表在时间窗口的数据",
            "report_generator: 渲染对账报告",
        ],
        "multi_table_join": [
            "diff_calculator: 合并 parallel_results，分析多表差异",
            "report_generator: 渲染对账报告",
        ],
        "boundary_edge": [
            "diff_calculator: 分析 parallel_results 中的边界异常",
            "report_generator: 渲染异常报告",
        ],
    }
    post_steps = _POST_STEPS.get(intent, [
        "diff_calculator: 合并 parallel_results 分析差异",
        "report_generator: 渲染报告",
    ])

    prompt = (
        f"Query: {query}\nIntent: {intent}\n\n"
        f"{schema_hint}\n\n"
        "SQLite rules: strftime('%Y-%m',col), DATE('now','-N days'), no CURDATE/INTERVAL.\n\n"
        "分析这个对账/查询任务需要从哪几张表独立查询数据（每张表的查询互相独立，可并发执行）。\n"
        "表的数量由你决定，可以是 2 张也可以是多张。\n"
        "为每张表生成独立的 SQL 查询语句，alias 用表名命名（如 order_result, payment_result）。\n\n"
        "返回 JSON 数组（只返回 JSON，不要其他文字）:\n"
        '[{"alias":"<表名_result>","sql":"<SELECT ...>","description":"<简要说明>"},...]'
    )

    try:
        out = ctx.llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You analyze a reconciliation query and generate independent SQL for each table. "
                        "Return a JSON array only, no explanation. "
                        "The number of tables is determined by the query complexity."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            trace_id=ctx.trace_id,
            temperature=0.0,
            max_tokens=600,
        )
        raw = out.content.strip()

        # 提取 JSON 数组（LLM 可能在 JSON 前后加了文字）
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start < 0 or end <= start:
            logger.warning("parallel plan: LLM returned non-JSON: %s", raw[:200])
            return []

        sql_list = _json.loads(raw[start:end])

        # 验证并构建子任务列表
        filled_tasks = []
        for item in sql_list:
            alias = item.get("alias", "").strip()
            sql = item.get("sql", "").strip()
            desc = item.get("description", alias)

            if not alias or not sql:
                logger.warning("parallel plan: skipping item with missing alias/sql: %s", item)
                continue

            # 基础 SQL 合法性检查（必须是 SELECT）
            if not sql.upper().lstrip().startswith("SELECT"):
                logger.warning("parallel plan: non-SELECT sql for alias '%s', skipped", alias)
                continue

            filled_tasks.append({
                "action": "sql_runner",
                "alias": alias,
                "sql": sql,
                "description": desc,
            })

        if len(filled_tasks) < 2:
            # 少于 2 个任务，并行无意义，fallback 到串行
            logger.info(
                "parallel plan: only %d valid tasks, fallback to serial", len(filled_tasks)
            )
            return []

        parallel_step = {"parallel": filled_tasks}
        logger.info(
            "parallel plan: LLM decided %d parallel sub-tasks for intent=%s: %s",
            len(filled_tasks),
            intent,
            [t["alias"] for t in filled_tasks],
        )
        return [parallel_step] + post_steps

    except Exception as e:
        logger.warning("parallel plan: LLM failed to generate SQL, fallback to serial: %s", e)
        return []
