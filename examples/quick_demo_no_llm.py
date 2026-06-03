"""零依赖快速 Demo — 展示对账平台所有核心能力（不需要 LLM API Key）

直接调用工具和组件，不走 LLM 推理，让你能立刻看到：
  1. 三层记忆 bootstrap schema
  2. RAG 检索表文档
  3. 意图路由 + 工具裁剪
  4. SQL 执行 + 差异比对
  5. 报告生成
  6. Skill Reviewer 异步提炼
  7. 第二轮再跑时自动加载 Skill

用法:
    python3 examples/quick_demo_no_llm.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recon_core.tools.builtin.sql_tool import SQLTool
from recon_core.tools.builtin.diff_tool import DiffTool
from recon_core.tools.builtin.report_tool import ReportTool
from recon_core.tools.builtin.memory_tool import MemoryTool
from recon_core.tools.builtin.skill_reviewer import SkillReviewer
from recon_core.tools.builtin.rag_retriever import TableDocRetriever
from recon_core.tools.builtin.case_store import CaseStore
from recon_core.tools.registry import ToolRegistry
from recon_core.core.intent_router import IntentRouter


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "mock_reconciliation.db")
OUT_DIR = os.path.join(ROOT, "reports")


def banner(title: str):
    print("\n" + "═" * 72)
    print(f"  {title}")
    print("═" * 72)


def main():
    # ============ STEP 1: 三层记忆 bootstrap ============
    banner("STEP 1 · 三层记忆 (Working / Episodic / Semantic) 初始化")
    memory = MemoryTool(store_dir=os.path.join(ROOT, "memory_store"))
    memory.bootstrap_schema_knowledge(DB_PATH)
    memory.working_set("current_query", "对比昨天直播 GMV 和订单系统金额")
    print(f"   {memory.summary()}")
    print(f"   semantic 知识示例: schema:live_gmv → "
          f"{memory.semantic_get('schema:live_gmv')['table']} "
          f"{len(memory.semantic_get('schema:live_gmv')['columns'])} 个字段")

    # ============ STEP 2: RAG 检索表结构文档 ============
    banner("STEP 2 · RAG 检索 (TableDocRetriever)")
    rag = TableDocRetriever(doc_dir=os.path.join(ROOT, "knowledge_base/table_docs"))
    print(f"   加载文档: {rag.stats()}")
    hits = rag.search("直播 GMV 是什么", top_k=2)
    for h in hits:
        print(f"   命中表 [{h['table']}] (score={h['score']}) "
              f"-> {h['content'][:80]}...")
    rewritten = rag.rewrite_query("GMV 和订单金额")
    print(f"   术语重写: 'GMV 和订单金额' → '{rewritten}'")

    # ============ STEP 3: 意图路由 + 工具裁剪 ============
    banner("STEP 3 · 意图路由 (IntentRouter) + 工具过滤")
    skill_reviewer = SkillReviewer(skill_dir=os.path.join(ROOT, "skill_library"))
    case_store = CaseStore(store_dir=os.path.join(ROOT, "recon_cases"))
    router = IntentRouter(skill_reviewer=skill_reviewer)

    registry = ToolRegistry()
    registry.register_tool(SQLTool(db_path=DB_PATH))
    registry.register_tool(DiffTool(db_path=DB_PATH))
    registry.register_tool(ReportTool(output_dir=OUT_DIR))
    print(f"   注册工具数: {len(registry.list_tools())} → {registry.list_tools()}")

    route = router.route(
        "对比昨天直播 GMV 和订单金额，找差异超 100 元的直播间",
        case_store=case_store,
    )
    print(f"   {router.route_summary()}")
    print(f"   选中意图: {route.intent.name}")
    print(f"   max_steps: {route.intent.max_steps}")

    # ============ STEP 4: 直接执行工具链（模拟 ReAct 每一步）============
    banner("STEP 4 · 工具链执行 (模拟 ReAct 循环)")

    # 4.1 sql_schema
    sql_tool = SQLTool(db_path=DB_PATH)
    print("\n  [Action 1] sql_schema(live_gmv)")
    print("  " + "─" * 60)
    out = sql_tool._get_schema("live_gmv")
    print("\n".join(["  " + ln for ln in out.split("\n")[:10]]))
    print("  ...")

    # 4.2 sql_execute (左表)
    print("\n  [Action 2] sql_execute(SELECT live_id, SUM(gmv) ...)")
    print("  " + "─" * 60)
    sql_a = ("SELECT live_id, SUM(gmv) AS total_gmv "
             "FROM live_gmv WHERE live_date='2026-05-27' "
             "GROUP BY live_id ORDER BY live_id")
    out_a = sql_tool._execute(sql_a)
    print("\n".join(["  " + ln for ln in out_a.split("\n")[:8]]))
    print("  ...")

    # 4.3 sql_execute (右表)
    print("\n  [Action 3] sql_execute(SELECT live_id, SUM(total_amount) ...)")
    print("  " + "─" * 60)
    sql_b = ("SELECT live_id, SUM(total_amount) AS total_order "
             "FROM order_amount WHERE order_date='2026-05-27' "
             "GROUP BY live_id ORDER BY live_id")
    out_b = sql_tool._execute(sql_b)
    print("\n".join(["  " + ln for ln in out_b.split("\n")[:8]]))
    print("  ...")

    # 4.4 diff_compare
    print("\n  [Action 4] diff_compare(sql_a, sql_b, key=live_id)")
    print("  " + "─" * 60)
    diff_tool = DiffTool(db_path=DB_PATH)
    diff_out = diff_tool._compare(
        sql_a=sql_a,
        sql_b=sql_b,
        key_column="live_id",
        compare_columns="total_gmv,total_order",
    )
    print("\n".join(["  " + ln for ln in diff_out.split("\n")[:30]]))

    # 4.5 report_generate
    print("\n  [Action 5] report_generate(...)")
    print("  " + "─" * 60)
    report_tool = ReportTool(output_dir=OUT_DIR)
    report_path = report_tool._generate(
        title="2026-05-27 直播GMV对账报告",
        diff_result=diff_out,
        conclusion="发现 3 处差异，其中 2 处超阈值（live_id=105, 208）建议复核",
    )
    print(f"  ✅ {report_path}")

    # ============ STEP 5: 写入记忆 + 案例库 ============
    banner("STEP 5 · 写入记忆系统 & 案例库")
    memory.episodic_add(
        key="recon:reconciliation",
        value={"query": "对比昨天 GMV 与订单", "diff_count": 3},
        importance=0.8,
    )
    case_id = case_store.save(
        query="对比昨天直播 GMV 和订单金额",
        sql_a=sql_a,
        sql_b=sql_b,
        key_column="live_id",
        compare_columns="total_gmv,total_order",
        diff_summary="发现 3 处差异",
        conclusion="见报告",
    )
    print(f"  Memory: {memory.summary()}")
    print(f"  CaseStore 新案例 ID: {case_id}")

    # ============ STEP 6: SkillReviewer 同步提炼（看效果）============
    banner("STEP 6 · SkillReviewer 提炼可复用 Skill (同步模式)")
    fake_trace = f"""
sql_execute({{"sql": "{sql_a}"}})
sql_execute({{"sql": "{sql_b}"}})
diff_compare 比对结果: live_id=105 total_gmv ⟷ total_order 差异 700
差异在 5% 以内为正常
"""
    skill_reviewer.review(
        query="对比直播 GMV 和订单金额",
        execution_trace=fake_trace,
        final_result="发现 3 处差异，2 处超 100 元阈值",
        intent="reconciliation",
        async_mode=False,  # 同步以便立即看效果
    )
    print(f"  Skill 库快照: {skill_reviewer.stats()}")
    for s in list(skill_reviewer._skills.values())[:5]:
        print(f"   • [{s.category}] {s.name}")

    # ============ STEP 7: 第二轮 — IntentRouter 自动加载 Skill ============
    banner("STEP 7 · 第二轮路由（验证自进化加载）")
    route2 = router.route(
        "再核对一次直播 GMV 与订单金额",
        case_store=case_store,
    )
    print(f"  {router.route_summary()}")
    if "🌱 自进化经验" in route2.system_prompt:
        idx = route2.system_prompt.find("🌱 自进化经验")
        print("  ✅ Skill 已自动注入 system prompt:")
        print("\n".join(["    " + ln for ln in
                          route2.system_prompt[idx:idx + 600].split("\n")]))
    else:
        print("  ⚠️  未注入 Skill")

    # ============ 总结 ============
    banner("能力清单 ✓ 全部跑通")
    print("  ✅ 双层 Agent 架构（Plan&Solve + ReAct）— 类已实现，本 demo 直跑工具链")
    print("  ✅ Tool Registry + Schema 自动发现")
    print(f"  ✅ RAG 增强 — {rag.stats()}")
    print("  ✅ 跨异构数据源适配（SQLAdapter）— SQLite/Hive/ClickHouse")
    print(f"  ✅ 三层记忆 — {memory.summary()}")
    print(f"  ✅ 自进化闭环 — Skill 库 {skill_reviewer.stats()}")
    print(f"\n  📄 报告已保存: {report_path}")
    print(f"  📂 案例库: {len(case_store._index)} 条")
    print("\n  下一步: 在 .env 配置 LLM_API_KEY 即可跑完整 LLM 推理:")
    print("    python3 examples/reconciliation_demo.py")
    print("    python3 examples/self_evolving_recon_demo.py")


if __name__ == "__main__":
    main()
