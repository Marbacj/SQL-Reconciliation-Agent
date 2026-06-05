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

        logger.debug("[PLAN] query='%s' intent=%s → mode=%s", query, intent, ctx.mode)

        steps = _PLAN_TEMPLATES.get(intent, _PLAN_TEMPLATES["simple_query"])
        logger.debug("[PLAN] template_steps=%s", steps)

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

                # Episodic few-shot：召回历史成功案例，帮助 LLM 生成更精准的 plan
                episodic_hint = ""
                try:
                    if ctx.memory is not None and hasattr(ctx.memory, "query_episodic"):
                        past_cases = ctx.memory.query_episodic(query, k=3, intent_filter=intent)
                        if past_cases:
                            episodic_hint = "\nHistorical successful cases (for reference):\n"
                            for c in past_cases:
                                past_q = c.get("query", "")
                                past_sql = c.get("sql", "")
                                if past_q and past_sql:
                                    episodic_hint += f"  Q: {past_q}\n  SQL: {past_sql[:300]}\n"
                except Exception as e:
                    logger.debug("[PLAN] episodic recall failed: %s", e)

                msg = (
                    f"Query: {query}\nIntent: {intent}\n"
                    f"{schema_hint}\n"
                    "SQLite date rules: DATE('now'), DATE('now','-N days'), "
                    "strftime('%Y-%m',col). NO INTERVAL/CURDATE/NOW()."
                    f"{rag_hint}"
                    f"{episodic_hint}\n"
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
                    logger.debug("[PLAN] LLM refined steps=%s", lines)

                # 对账意图：优先用 ReconPlanner 生成结构化 tables 数组
                if use_parallel and intent in {"time_window_recon", "numeric_diff", "multi_table_join"}:
                    recon_plan = _build_recon_plan_with_llm(
                        intent=intent,
                        query=query,
                        schema_hint=schema_hint,
                        ctx=ctx,
                    )
                    if recon_plan and recon_plan.get("tables"):
                        parallel_tasks = _recon_plan_to_parallel_steps(recon_plan)
                        if len(parallel_tasks) >= 2:
                            steps = [{"parallel": parallel_tasks, "_recon_plan": recon_plan}]
                            logger.info(
                                "[PLAN] ReconPlanner → %d-table plan: %s",
                                len(parallel_tasks),
                                [t["alias"] for t in parallel_tasks],
                            )
                            ctx.step()
                            return {"plan_steps": steps, "mode": ctx.mode, "step_counter": ctx.step_counter}

                # 旧并行路径（fallback）
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


def _build_recon_plan_with_llm(
    intent: str,
    query: str,
    schema_hint: str,
    ctx,
) -> dict | None:
    """ReconPlanner：让 LLM 输出结构化对账计划（tables 数组），支持 N 张表。

    返回格式：
    {
        "tables": [
            {"alias": "orders",   "table": "order_amount",  "key_cols": ["order_no"], "value_cols": ["total_amount"]},
            {"alias": "payments", "table": "settlements",   "key_cols": ["order_no"], "value_cols": ["settle_amount"]},
            ...  # 可以是 2 张也可以是 N 张
        ],
        "join_keys": ["order_no"],
        "recon_type": "amount_diff",  # amount_diff | existence | time_window
        "tolerance": 0.01
    }
    失败返回 None（触发 fallback 到旧并行模板）。
    """
    import json as _json

    prompt = (
        f"Query: {query}\nIntent: {intent}\n\n"
        f"{schema_hint}\n\n"
        "你是一个数据对账规划器。根据查询意图，从上述表结构中找出语义最匹配的表参与对账。\n"
        "选表原则：\n"
        "  - 「订单金额/支付金额对比」→ 选订单表(order_amount/orders) 和结算/支付流水表(settlements/payments)\n"
        "  - 不要选统计汇总表(gmv/stats/report/summary/count)，除非查询明确提到这些表名\n"
        "  - 每张候选表必须有金额列（amount/fee/price/gmv 等）或业务 ID 列\n\n"
        "返回 JSON（只返回 JSON，不要其他文字）:\n"
        '{\n'
        '  "tables": [\n'
        '    {"alias": "<表别名>", "table": "<实际表名>", "key_cols": ["<join列>"], "value_cols": ["<比较金额列>"]},\n'
        '    ...\n'
        '  ],\n'
        '  "join_keys": ["<主键列名>"],\n'
        '  "recon_type": "amount_diff",\n'
        '  "tolerance": 0.01\n'
        '}\n\n'
        "recon_type 取值: amount_diff(金额对比), existence(存在性检查), time_window(时间窗口对账)\n"
        "如果只需要单表聚合查询（不涉及多表对账），返回空 tables 数组: {\"tables\":[]}"
    )

    try:
        out = ctx.llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a data reconciliation planner. Analyze the query and output a structured "
                        "reconciliation plan with N tables (N>=2 for recon queries). "
                        "Return valid JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            trace_id=ctx.trace_id,
            temperature=0.0,
            max_tokens=500,
        )
        raw = out.content.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start < 0 or end <= start:
            logger.warning("recon_plan: LLM returned non-JSON: %s", raw[:200])
            return None

        plan = _json.loads(raw[start:end])
        tables = plan.get("tables", [])

        if not tables:
            logger.info("recon_plan: LLM decided single-table query, skip recon path")
            return None

        # 验证每个 table 条目
        valid_tables = []
        for t in tables:
            if t.get("table") and t.get("key_cols") and t.get("value_cols"):
                valid_tables.append({
                    "alias": t.get("alias") or t["table"],
                    "table": t["table"],
                    "key_cols": t["key_cols"] if isinstance(t["key_cols"], list) else [t["key_cols"]],
                    "value_cols": t["value_cols"] if isinstance(t["value_cols"], list) else [t["value_cols"]],
                })

        if len(valid_tables) < 2:
            logger.info("recon_plan: only %d valid tables, skip recon path", len(valid_tables))
            return None

        plan["tables"] = valid_tables
        plan.setdefault("join_keys", valid_tables[0]["key_cols"])
        plan.setdefault("recon_type", "amount_diff")
        plan.setdefault("tolerance", 0.01)

        logger.info(
            "recon_plan: LLM planned %d-table recon (%s): %s",
            len(valid_tables),
            plan["recon_type"],
            [t["alias"] for t in valid_tables],
        )

        # ── 用数据库验证并修正 join key（不依赖 LLM 猜测）
        if ctx.db_path:
            plan = _validate_recon_plan_with_db(plan, ctx.db_path)
            if not plan.get("_plan_valid", True):
                logger.warning(
                    "recon_plan: validation failed, returning plan with warnings"
                )
                # 仍然返回 plan（带警告），让 diff engine 展示问题而不是静默失败

        return plan

    except Exception as e:
        logger.warning("recon_plan: LLM failed: %s", e)
        return None


def _auto_discover_join_keys(tables_meta: list, db_path: str) -> dict:
    """纯代码自动发现各表之间可用的 join key。

    算法：
    1. 找所有表的公共列名
    2. 对每个公共列执行 JOIN COUNT 验证匹配率
    3. 找不到完全公共列时，做相似列名匹配（order_no ~ order_id，编辑距离 <= 2）
    4. 返回 {(alias_a, alias_b): {"col_a": "order_no", "col_b": "order_no", "match_count": 123}}

    JOIN COUNT = 0 → 不可关联（表选择可能有误）
    JOIN COUNT > 0 → 有效 join key
    """
    import sqlite3
    import difflib

    if len(tables_meta) < 2:
        return {}

    result: dict = {}  # (alias_a, alias_b) → join key 信息

    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
    except Exception as e:
        logger.warning("auto_discover_join_keys: connect failed: %s", e)
        return {}

    try:
        # 获取每张表的真实列名
        table_cols: dict = {}  # alias → set(col_names)
        for t in tables_meta:
            alias = t["alias"]
            table_name = t["table"]
            try:
                cur = conn.execute(f"PRAGMA table_info(\"{table_name}\")")
                cols = {row[1] for row in cur.fetchall()}
                table_cols[alias] = cols
            except Exception as e:
                logger.warning("auto_discover: PRAGMA failed for %s: %s", table_name, e)
                table_cols[alias] = set()

        # 两两验证
        for i in range(len(tables_meta)):
            for j in range(i + 1, len(tables_meta)):
                ta = tables_meta[i]
                tb = tables_meta[j]
                alias_a, alias_b = ta["alias"], tb["alias"]
                cols_a = table_cols.get(alias_a, set())
                cols_b = table_cols.get(alias_b, set())

                if not cols_a or not cols_b:
                    continue

                # ── Step 1：完全公共列名
                common = cols_a & cols_b
                # 优先含有明确业务 key 语义的列：_no, code, sn > _id > id
                def _key_priority(col: str) -> int:
                    c = col.lower()
                    if any(kw in c for kw in ("_no", "code", "sn", "num")):
                        return 0
                    if "_id" in c and c != "id":
                        return 1
                    if c == "id":
                        return 3  # 低优先级：纯 id 太泛
                    return 2
                # 排除纯 id（高基数但语义弱）和非 key 列
                preferred = sorted(
                    [c for c in common if any(
                        kw in c.lower() for kw in ("_no", "_id", "code", "key", "num", "sn")
                    )],
                    key=_key_priority,
                )
                candidates = preferred or [c for c in common if c.lower() != "id"]

                # ── Step 2：无公共列时做相似名匹配（order_no ~ order_id）
                if not candidates:
                    for ca in cols_a:
                        for cb in cols_b:
                            ratio = difflib.SequenceMatcher(None, ca.lower(), cb.lower()).ratio()
                            if ratio >= 0.7 and ca != cb:
                                candidates.append((ca, cb))  # (col_a, col_b) tuple
                    if not candidates:
                        logger.info(
                            "auto_discover: no candidate keys between %s and %s",
                            alias_a, alias_b,
                        )
                        result[(alias_a, alias_b)] = {
                            "col_a": None, "col_b": None,
                            "match_count": 0,
                            "error": f"{alias_a} 和 {alias_b} 没有可关联的公共列",
                        }
                        continue

                # ── Step 3：JOIN COUNT 验证，选匹配数最大的
                best = None
                best_count = -1
                for cand in candidates:
                    if isinstance(cand, tuple):
                        col_a, col_b = cand
                    else:
                        col_a = col_b = cand
                    try:
                        sql = (
                            f'SELECT COUNT(*) FROM "{ta["table"]}" t1 '
                            f'JOIN "{tb["table"]}" t2 ON t1."{col_a}" = t2."{col_b}"'
                        )
                        cur = conn.execute(sql)
                        count = cur.fetchone()[0]
                        if count > best_count:
                            best_count = count
                            best = {"col_a": col_a, "col_b": col_b, "match_count": count}
                    except Exception as e:
                        logger.debug("join count failed %s.%s-%s.%s: %s", alias_a, col_a, alias_b, col_b, e)

                if best is None:
                    best = {"col_a": None, "col_b": None, "match_count": 0,
                            "error": "JOIN COUNT 验证失败"}

                result[(alias_a, alias_b)] = best
                logger.info(
                    "auto_discover: %s.%s JOIN %s.%s → match_count=%d",
                    alias_a, best["col_a"], alias_b, best["col_b"], best["match_count"],
                )
    finally:
        conn.close()

    return result


def _validate_recon_plan_with_db(recon_plan: dict, db_path: str) -> dict:
    """用数据库验证 LLM 规划的 recon_plan，修正 join key 并附加可信度标注。

    返回修正后的 recon_plan，新增字段：
    - "_join_validation": {(alias_a, alias_b): {col_a, col_b, match_count, valid}}
    - "_plan_valid": True/False（是否所有表对都能关联）
    - "_warning": 警告信息（选表可能有误时填充）

    逻辑：
    1. 用 _auto_discover_join_keys 发现实际可用的 join key
    2. 对比 LLM 规划的 key_cols 是否与发现结果一致
    3. 若 LLM 的 key 无效（match_count=0）但发现了更好的 key → 自动修正
    4. 若所有候选 key 都 match_count=0 → 标记 _plan_valid=False + 附加警告
    """
    tables = recon_plan.get("tables", [])
    if len(tables) < 2:
        return recon_plan

    join_validation = _auto_discover_join_keys(tables, db_path)
    recon_plan["_join_validation"] = {
        f"{k[0]}-{k[1]}": v for k, v in join_validation.items()
    }

    all_valid = True
    warnings = []

    # 更新每张表的 key_cols（基于发现结果）
    # 构建 alias → validated_key_col 映射
    alias_key_map: dict = {}  # alias → 最终 join key 列名（本表侧）
    for (alias_a, alias_b), vinfo in join_validation.items():
        if vinfo["match_count"] == 0:
            all_valid = False
            err = vinfo.get("error", "")
            warnings.append(
                f"⚠️ {alias_a} 和 {alias_b} 之间没有匹配数据（match_count=0），"
                f"可能选错了表。{err}"
            )
        else:
            # 记录每个 alias 使用的 key 列名
            alias_key_map[alias_a] = vinfo["col_a"]
            alias_key_map[alias_b] = vinfo["col_b"]

    # 用验证结果覆盖 LLM 的 key_cols（只在 key 有效时覆盖）
    for t in tables:
        alias = t["alias"]
        if alias in alias_key_map and alias_key_map[alias]:
            llm_key = t["key_cols"][0] if t["key_cols"] else None
            discovered_key = alias_key_map[alias]
            if llm_key != discovered_key:
                logger.info(
                    "validate_recon_plan: correcting %s key_cols: %s → %s",
                    alias, llm_key, discovered_key,
                )
                t["key_cols"] = [discovered_key]

    # 更新全局 join_keys（取第一对的公共 key 或 col_a）
    first_pair = next(iter(join_validation.values()), None)
    if first_pair and first_pair["match_count"] > 0:
        col_a = first_pair["col_a"]
        col_b = first_pair["col_b"]
        # 如果 col_a == col_b（同名列），join_keys 用统一名称
        recon_plan["join_keys"] = [col_a] if col_a == col_b else [col_a]

    recon_plan["_plan_valid"] = all_valid
    if warnings:
        recon_plan["_warning"] = "\n".join(warnings)
        logger.warning("validate_recon_plan warnings: %s", recon_plan["_warning"])

    return recon_plan


def _recon_plan_to_parallel_steps(recon_plan: dict) -> list:
    """将 ReconPlanner 输出的 tables 数组转换为 parallel_act 可执行的子任务列表。"""
    tables = recon_plan.get("tables", [])
    join_keys = recon_plan.get("join_keys", [])
    tasks = []
    for t in tables:
        # 生成每张表的 SELECT SQL（查 key_cols + value_cols）
        cols = list(dict.fromkeys(t["key_cols"] + t["value_cols"]))  # 去重保序
        col_list = ", ".join(f'"{c}"' for c in cols)
        sql = f'SELECT {col_list} FROM "{t["table"]}"'
        tasks.append({
            "action": "sql_runner",
            "alias": t["alias"],
            "sql": sql,
            "description": f"查询 {t['table']} 的 {', '.join(t['value_cols'])}",
            # 携带 meta 给 diff engine 使用
            "_recon_meta": {
                "key_cols": t["key_cols"],
                "value_cols": t["value_cols"],
                "table": t["table"],
            },
        })
    return tasks


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
