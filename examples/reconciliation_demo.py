"""SQL 对账 Agent Demo — 基于 HelloAgents + ReconciliationAgent

演示完整对账流程：自然语言输入 → 表结构发现 → SQL 生成 → 执行 → 比对 → 报告

用法:
    python examples/reconciliation_demo.py

前置条件：
    - .env 中配置了 DEEPSEEK_API_KEY（或其他 LLM provider）
    - data/mock_reconciliation.db 已创建
"""

import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from recon_core.core.llm import HelloAgentsLLM
from recon_core.core.config import Config
from recon_core.agents.reconciliation_agent import ReconciliationAgent


def main():
    # ==================== 配置 ====================

    # 数据库路径
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "mock_reconciliation.db"
    )

    # 报告输出目录
    output_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reports"
    )

    # ==================== 初始化 LLM ====================

    # 使用环境变量配置（.env 中的 DEEPSEEK_API_KEY 等）
    llm = HelloAgentsLLM()

    print(f"🤖 LLM Model: {llm.model}")
    print()

    # ==================== 创建对账 Agent ====================

    config = Config(
        skills_enabled=False,    # Demo 不需要 skill
        trace_enabled=True,      # 开启追踪
        subagent_enabled=False,  # Demo 不需要子代理
    )

    agent = ReconciliationAgent(
        name="对账分析师",
        llm=llm,
        db_path=db_path,
        config=config,
        max_steps=8,
        output_dir=output_dir
    )

    # ==================== 执行对账 ====================

    # 测试用例 1：基本对账
    query = "对比昨天(2026-05-27)各直播间的GMV和订单总金额，找出差异超过100元的直播间"

    print(f"\n{'='*60}")
    print(f"📝 对账查询: {query}")
    print(f"{'='*60}\n")

    result = agent.run(query)

    print(f"\n{'='*60}")
    print(f"📄 最终结果:\n{result}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
