"""Act node：核心执行节点。

负责：
1. 让 LLM 选工具 + 生成参数（Function Calling）
2. 调用工具
3. 把工具结果作为下一轮 input
4. 失败重试 / 模式切换

降级策略（无 LLM 时）：
- 走"模板生成"：基于 intent / 关键词直接产出 SQL，跑 sql_runner
- 这样即使没装 LLM 也能跑通最基础 case，方便单测 & demo
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState
from recon_v2.rag.schema_indexer import SchemaLinker, get_default_linker
from recon_v2.tools.schema_inspector import SchemaInfo, inspect as inspect_schema

logger = logging.getLogger(__name__)


# ---------------- 模板降级 SQL 生成 ----------------

_TEMPLATE_RULES = [
    # (intent, regex, sql_template)
    ("simple_query", r"昨天.*订单.*总数", "SELECT COUNT(*) AS total FROM orders WHERE DATE(created_at) = DATE('now', '-1 day')"),
    ("simple_query", r"今天.*paid.*订单", "SELECT COUNT(*) AS paid_orders FROM orders WHERE status='paid' AND DATE(created_at)=DATE('now')"),
    ("simple_query", r"最近\s*10\s*笔订单", "SELECT id, user_id, amount, status, created_at FROM orders ORDER BY created_at DESC LIMIT 10"),
    ("simple_query", r"今天.*订单.*总金额", "SELECT SUM(amount) AS total_amount FROM orders WHERE DATE(created_at)=DATE('now')"),
    ("simple_query", r"本周.*退款", "SELECT COUNT(*) AS refund_count FROM refunds WHERE created_at >= DATE('now','weekday 0','-7 days')"),
    ("simple_query", r"用户\s*U(\d+).*下了几单", "SELECT COUNT(*) AS order_count FROM orders WHERE user_id='U{m1}'"),
    ("simple_query", r"最大.*订单.*金额", "SELECT MAX(amount) AS max_amount FROM orders"),
    ("simple_query", r"按渠道.*支付笔数", "SELECT channel, COUNT(*) AS cnt FROM payments GROUP BY channel ORDER BY cnt DESC"),
    ("simple_query", r"过去\s*7\s*天.*日订单数", "SELECT DATE(created_at) AS day, COUNT(*) AS cnt FROM orders WHERE created_at>=DATE('now','-7 days') GROUP BY DATE(created_at) ORDER BY day"),
    ("simple_query", r"支付成功.*平均金额", "SELECT AVG(amount) AS avg_amount FROM payments WHERE status='success'"),
]


def _extract_groups(query: str, pat: str) -> Dict[str, str]:
    m = re.search(pat, query)
    if not m:
        return {}
    return {f"m{i + 1}": g for i, g in enumerate(m.groups())}


def _template_solve(query: str, intent: str) -> Optional[str]:
    """模板降级：返回最匹配的 SQL，找不到返回 None。"""
    q_lower = query.lower()
    for tmpl_intent, pat, sql in _TEMPLATE_RULES:
        if tmpl_intent != intent:
            continue
        if re.search(pat, query, re.IGNORECASE):
            groups = _extract_groups(query, pat)
            try:
                return sql.format(**groups) if groups else sql
            except Exception:
                return sql
    return None


# ---------------- LLM 工具选择 ----------------


_SQLITE_RULES = """
SQLite date/time rules (MUST follow):
- Yesterday: DATE('now', '-1 day')
- Today: DATE('now')
- Last 7 days: created_at >= DATE('now', '-7 days')
- Last 24 hours: created_at >= DATETIME('now', '-1 day')  [for DATETIME columns]
- Last N days: DATE('now', '-N days')  e.g. DATE('now', '-3 days')
- This week: created_at >= DATE('now', 'weekday 0', '-7 days')
- Last week Sunday: DATE('now', 'weekday 0', '-7 days')
- This month: strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
- Last month: strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', '-1 month')
- Hour extraction: CAST(strftime('%H', created_at) AS INTEGER)
FORBIDDEN: INTERVAL, CURDATE(), DATE_SUB(), NOW(), EXTRACT(), DATEDIFF(), STDDEV(), stddev_samp(), stddev_pop()
For standard deviation: use SQRT(AVG((val - (SELECT AVG(val) FROM t)) * (val - (SELECT AVG(val) FROM t))))
"""

# _SCHEMA_DESC 已移除：改为 _get_schema_desc() 实时从 SchemaInspector 获取
_SCHEMA_DESC_FALLBACK = (
    "Database schema (ONLY these 3 tables exist, NO users table):\n"
    "  orders(id TEXT, user_id TEXT, amount REAL, status TEXT, created_at TEXT)\n"
    "    status values: 'paid', 'pending', 'cancelled'\n"
    "  refunds(id TEXT, order_id TEXT, amount REAL, status TEXT, created_at TEXT)\n"
    "    order_id references orders.id\n"
    "  payments(id TEXT, order_id TEXT, amount REAL, channel TEXT, status TEXT, created_at TEXT)\n"
    "    channel values: 'wechat', 'alipay', 'card'\n"
    "    status values: 'success', 'pending', 'failed'\n"
    "    order_id references orders.id\n"
    "JOIN keys: orders.id = refunds.order_id = payments.order_id"
)


def _get_schema_desc(ctx) -> str:
    """实时获取 schema 描述，通过 SchemaLinker 过滤相关表，失败时降级到全量。"""
    # 优先用 plan 节点已缓存的 schema_info
    schema_info: SchemaInfo = getattr(ctx, "_schema_info", None)
    if schema_info is None:
        try:
            adapter = None
            if ctx.tools:
                runner = ctx.tools.get("sql_runner")
                if runner and hasattr(runner, "adapter"):
                    adapter = runner.adapter
            schema_info = inspect_schema(db_path=ctx.db_path, adapter=adapter)
            ctx._schema_info = schema_info
        except Exception as e:
            logger.warning("act: schema_inspector failed, using fallback: %s", e)
            return _SCHEMA_DESC_FALLBACK

    if not schema_info.tables:
        return _SCHEMA_DESC_FALLBACK

    # Schema Linking：从全量表中过滤出与 query 相关的 Top-K 张表
    # 若索引未就绪（冷启动）则 fallback 到全量
    query = getattr(ctx, "query", "") or ""
    relevant_table_names = _link_relevant_tables(query, ctx.db_path)

    if relevant_table_names:
        # 只保留相关表，其余过滤掉
        filtered_tables = [t for t in schema_info.tables if t.name in relevant_table_names]
        # 保持原始顺序（按相关性排序）
        filtered_tables.sort(
            key=lambda t: relevant_table_names.index(t.name)
            if t.name in relevant_table_names else 999
        )
        if filtered_tables:
            from recon_v2.tools.schema_inspector import SchemaInfo as SI
            filtered = SI(tables=filtered_tables, dialect=schema_info.dialect)
            logger.debug(
                "act: schema linking %r → tables: %s",
                query[:50],
                [t.name for t in filtered_tables],
            )
            return filtered.to_prompt_str()

    # 全量 fallback（表少时或 linker 未就绪时）
    return schema_info.to_prompt_str()


def _link_relevant_tables(query: str, db_path: str) -> List[str]:
    """调用 SchemaLinker 返回相关表名，失败返回空列表（触发全量 fallback）。"""
    if not query:
        return []
    try:
        linker = get_default_linker(db_path=db_path, auto_build=True)
        return linker.link(query, k=5)
    except Exception as e:
        logger.debug("SchemaLinker failed: %s", e)
        return []

_SQL_PRINCIPLES = """
CRITICAL SQL GENERATION RULES:
1. Generate MINIMAL SQL that directly answers the query - do NOT add extra filters/conditions not asked
2. Do NOT add status filters unless explicitly asked (e.g., if query says "支付和订单金额不一致", include ALL records)
3. Return ONLY the columns needed for the answer - minimize extra columns
4. For 'all records' queries, do NOT add 'success/paid/cancelled' filters
5. For JOINs: qualify ALL column names with table aliases (e.g., o.created_at, p.amount)
"""


def _llm_pick_tool(ctx, query: str, intent: str, plan: List[str], state: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """让 LLM 决定调用哪个工具 + 参数。返回 {"name": ..., "args": {...}} 或 None。

    若 state 中存在 last_sql_error，则在 prompt 中注入 self-correction 上下文，
    让 LLM 看到上次失败的 SQL 和错误原因，生成修正后的 SQL。
    """
    if ctx.llm is None or ctx.tools is None:
        return None
    try:
        plan_text = "\n".join(f"  - {s}" for s in plan)
        schema_desc = _get_schema_desc(ctx)

        # ---- Self-Correction 上下文 ----
        correction_hint = ""
        if state:
            last_error = state.get("last_sql_error", "")
            last_sql = state.get("last_failed_sql", "")
            retry_count = state.get("retry_count", 0)
            if last_error and last_sql:
                correction_hint = (
                    f"\n[CORRECTION REQUIRED - Attempt {retry_count + 1}]\n"
                    f"Your previous SQL FAILED:\n"
                    f"  SQL: {last_sql}\n"
                    f"  Error: {last_error}\n"
                    f"You MUST fix the SQL. Common fixes:\n"
                    f"  - Table not found: check schema below for correct table names\n"
                    f"  - Column not found: check schema below for correct column names\n"
                    f"  - Syntax error: review SQLite syntax rules\n"
                    f"Generate a DIFFERENT, corrected SQL.\n"
                )

        # ---- RAG 检索：注入知识库中的题目解读/业务规则 ----
        rag_hint = ""
        if ctx.rag is not None:
            try:
                rag_docs = ctx.rag.search(query, k=2)
                if rag_docs:
                    rag_hint = "\nKnowledge base hints (use as reference, NOT as direct answer):\n" + "\n".join(
                        f"  - {d.text[:300]}" for d in rag_docs
                    )
            except Exception:
                pass

        sys_msg = (
            "You are a SQL reconciliation agent. Generate ONE tool call to solve the query.\n"
            "Respond ONLY with JSON (no markdown, no explanation): "
            "{\"name\": \"sql_runner\", \"args\": {\"sql\": \"<SELECT ...>\", \"apply_limit\": true}}\n"
            f"{schema_desc}\n"
            f"{_SQLITE_RULES}\n"
            f"{_SQL_PRINCIPLES}"
            f"{rag_hint}"
            f"{correction_hint}"
        )
        usr = (
            f"Query: {query}\n"
            f"Intent: {intent}\n"
            f"Plan:\n{plan_text}\n\n"
            "Generate the sql_runner call with correct SQLite syntax."
        )
        out = ctx.llm.chat(
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": usr},
            ],
            trace_id=ctx.trace_id,
            temperature=0.0,
            max_tokens=400,
        )
        ctx.budget.add_tokens(out.prompt_tokens + out.completion_tokens)
        # 尝试解析 JSON
        text = out.content.strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        result = json.loads(text)
        # 兼容 LLM 返回 {name: sql_runner, args: {query: ...}} 的情况
        if result.get("name") == "sql_runner":
            args = result.get("args", {})
            if "query" in args and "sql" not in args:
                args["sql"] = args.pop("query")
            result["args"] = args
        return result
    except Exception as e:
        logger.warning("LLM tool pick failed: %s", e, exc_info=True)
        return None


# ---------------- Act node ----------------


REACT_MAX_STEPS = 4


def act_node(state: GraphState) -> dict:
    ctx = get_ctx(state["ctx_id"])
    query = state["query"]
    intent = state.get("intent", "simple_query")
    plan = state.get("plan_steps", [])
    step_counter = state.get("step_counter", 0)

    # ----- Budget guard -----
    if ctx.budget.exceeded():
        return {
            "sql": "",
            "answer": f"budget exceeded: {ctx.budget.reason()}",
            "final_status": "budget_exceeded",
            "step_counter": step_counter + 1,
        }

    # ----- 检测 plan_steps 里是否有 parallel 步骤 -----
    # 若有，直接 dispatch 给 parallel_act_node，跳过 LLM 单步工具调用
    for step in plan:
        if isinstance(step, dict) and "parallel" in step:
            logger.info("act_node: detected parallel step, dispatching to parallel_act_node")
            from recon_v2.orchestration.nodes.parallel_act import parallel_act_node
            result = parallel_act_node(state)
            # 标记 step_counter
            ctx.step()
            result["step_counter"] = ctx.step_counter
            result["mode"] = ctx.mode
            return result

    # ----- 模式切换 -----
    if step_counter > REACT_MAX_STEPS and ctx.mode == "react":
        ctx.mode = "plan_solve"
        with span("mode_switch", attributes={"to": "plan_solve"}):
            pass

    with span("act", attributes={"intent": intent, "step": step_counter}) as s:
        # 1) 优先 LLM 决策（传入 state 以注入 self-correction 上下文）
        tool_call = _llm_pick_tool(ctx, query, intent, plan, state=state)
        logger.debug("[ACT] query='%s' intent=%s _llm_pick_tool → %s", query, intent, tool_call)

        # 2) 降级：模板生成 SQL → 直接调 sql_runner
        if tool_call is None:
            sql = _template_solve(query, intent)
            logger.debug("[ACT] _template_solve → sql='%s'", sql)
            if sql is None:
                # 极端兜底
                sql = f"SELECT * FROM orders WHERE created_at >= DATE('now', '-1 day') LIMIT 10"
                logger.warning("[ACT] ⚠️ FALLBACK SQL: query='%s' intent=%s → generic SQL", query, intent)
            tool_call = {"name": "sql_runner", "args": {"sql": sql, "apply_limit": True}}

        # 3) 执行工具
        tool = ctx.tools.get(tool_call["name"]) if ctx.tools else None
        if tool is None:
            return {
                "answer": f"unknown tool: {tool_call['name']}",
                "final_status": "error",
                "error": f"unknown tool: {tool_call['name']}",
                "step_counter": step_counter + 1,
            }

        out = tool.run(ctx, tool_call.get("args", {}))
        ctx.step()

        # self-correction 重试计数递增（只在失败时 observe 会回来重试）
        new_retry_count = state.get("retry_count", 0)
        if state.get("last_sql_error"):
            # 本次 act 是一次重试，递增计数
            new_retry_count = new_retry_count + 1

        try:
            s.set_attributes({"tool": tool.name, "success": int(bool(out.success))})
        except Exception:
            pass

        # 4) 把 tool_call + observation 入 state
        tool_calls = [{"name": tool.name, "args": tool_call.get("args", {})}]
        obs_dict = out.model_dump() if hasattr(out, "model_dump") else dict(out.__dict__)
        observations = [obs_dict]

        return {
            "tool_calls": tool_calls,
            "observations": observations,
            "step_counter": ctx.step_counter,
            "mode": ctx.mode,
            "retry_count": new_retry_count,
        }
