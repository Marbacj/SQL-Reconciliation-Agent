"""自进化对账 Demo — 双层 Agent + 三层记忆 + Skill 自进化闭环

演示简历亮点：
  1. 双层 Agent: Plan & Solve（宏观编排）+ ReAct（微观执行）
  2. Tool Registry + RAG: 业务术语 → 字段映射
  3. 三层记忆: Working / Episodic / Semantic
  4. 自进化闭环: SkillReviewer 异步提炼 → 下次会话自动加载

用法:
    python examples/self_evolving_recon_demo.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from hello_agents.core.llm import HelloAgentsLLM
from hello_agents.agents.reconciliation_agent import ReconciliationAgent
from hello_agents.agents.plan_solve_recon_agent import ReconciliationPlanAndSolveAgent
from hello_agents.tools.builtin.memory_tool import MemoryTool
from hello_agents.tools.builtin.skill_reviewer import SkillReviewer
from hello_agents.tools.builtin.case_store import CaseStore
from hello_agents.tools.builtin.rag_retriever import TableDocRetriever
from hello_agents.core.intent_router import IntentRouter


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "mock_reconciliation.db")
OUT_DIR = os.path.join(ROOT, "reports")


def banner(title: str):
    print("\n" + "═" * 72)
    print(f"  {title}")
    print("═" * 72)


def main():
    llm = HelloAgentsLLM()
    print(f"🤖 LLM: {llm.model}")

    # 共享组件 — 跨会话保持记忆和 Skill
    memory = MemoryTool(store_dir=os.path.join(ROOT, "memory_store"))
    skill_reviewer = SkillReviewer(skill_dir=os.path.join(ROOT, "skill_library"))
    case_store = CaseStore(store_dir=os.path.join(ROOT, "recon_cases"))
    rag = TableDocRetriever(doc_dir=os.path.join(ROOT, "knowledge_base/table_docs"))
    intent_router = IntentRouter(skill_reviewer=skill_reviewer)

    # ========== 第一轮：ReAct Agent + 全栈能力 ==========
    banner("Round 1 — ReAct Agent (含 RAG + 三层记忆 + 自进化)")
    agent_v1 = ReconciliationAgent(
        name="ReconAgent-v1",
        llm=llm,
        db_path=DB_PATH,
        output_dir=OUT_DIR,
        memory=memory,
        skill_reviewer=skill_reviewer,
        case_store=case_store,
        rag_retriever=rag,
        intent_router=intent_router,
    )
    q1 = "对比昨天直播 GMV 和订单系统金额，找出差异超过 100 元的直播间"
    agent_v1.run(q1)

    # 等待异步 Skill Reviewer 完成（守护线程）
    time.sleep(1.0)

    print("\n" + "─" * 72)
    print(f"📚 Skill 库快照: {skill_reviewer.stats()}")
    print(f"💾 Memory: {memory.summary()}")

    # ========== 第二轮：Plan & Solve 双层 Agent，自动加载 Skill ==========
    banner("Round 2 — Plan & Solve 双层 Agent (Skill 自进化加载)")
    agent_v2 = ReconciliationPlanAndSolveAgent(
        name="ReconAgent-v2",
        llm=llm,
        db_path=DB_PATH,
        output_dir=OUT_DIR,
        case_store=case_store,
        intent_router=intent_router,  # 复用同一路由器，已注入 SkillReviewer
    )
    q2 = "再核对一次直播间的 GMV 与订单金额，重点关注差异超 5% 的"
    agent_v2.run(q2)

    # ========== 总结 ==========
    banner("自进化效果对比")
    print(f"📚 Skill 库累计: {skill_reviewer.stats()}")
    print(f"💾 三层记忆: {memory.summary()}")
    print(f"📂 案例库案例数: {len(case_store._index)}")
    print("\n说明:")
    print("  • Round 1 ReAct 执行后，SkillReviewer 异步提取 SQL 模板/差异规则/术语映射")
    print("  • Round 2 Plan&Solve 启动时，IntentRouter 自动从 Skill 库加载 few-shot")
    print("  • 三层记忆跨会话保留：semantic 存表结构、episodic 存对账历史、working 是当前态")


if __name__ == "__main__":
    main()
