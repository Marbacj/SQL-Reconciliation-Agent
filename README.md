# SQL 对账 Agent — 自然语言驱动的自动化数据对账系统

> 基于 [HelloAgents](https://github.com/jjyaoao/HelloAgents) 多 Agent 框架 · ReAct 推理范式 · NL2SQL · 面试作品

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![HelloAgents](https://img.shields.io/badge/framework-HelloAgents-green.svg)](https://github.com/jjyaoao/HelloAgents)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

用户以自然语言描述对账需求（如"对比昨天直播 GMV 和订单金额差异"），Agent 自动完成：**表结构发现 → SQL 生成与校验 → 执行 → 差异比对 → 报告输出**。

---

## 架构

```
用户: "昨天直播GMV和订单金额有没有差异？"
  │
  ▼
┌──────────────────────────────────────────┐
│         ReconciliationAgent (ReAct)       │
│                                           │
│  Thought → sql_schema(两表)               │
│  Thought → sql_validate(SQL)              │
│  Action → sql_execute(SQL) × 2           │
│  Action → diff_compare(左表, 右表)        │
│  Action → report_generate(报告)           │
│  Finish → "发现3处差异..."                │
│                                           │
│  ┌──────────────────────────────────────┐ │
│  │          Tool Registry               │ │
│  │  sql_schema │ sql_execute │ validate │ │
│  │  diff_compare │ report_generate      │ │
│  └──────────────────────────────────────┘ │
└──────────────────────────────────────────┘
           │                │
     ┌─────▼─────┐   ┌─────▼──────┐
     │ live_gmv  │   │order_amount│
     │ (26 行)   │   │  (27 行)   │
     └───────────┘   └────────────┘
           SQLite (可替换为 Hive/ClickHouse)
```

---

## 快速开始

```bash
# 1. 安装依赖
pip install hello-agents python-dotenv

# 2. 配置 LLM（.env 文件）
echo 'LLM_MODEL_ID=deepseek-chat
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com' > .env

# 3. 生成 Demo 数据（含 3 处故意差异）
python data/generate_mock_data.py

# 4. 运行对账
python examples/reconciliation_demo.py
```

---

## Demo 输出

Agent 完整执行 7 步对账流程，正确识别 3 处故意设计的差异：

| live_id | 直播间 | 问题 | GMV | 订单金额 | 差异 |
|---------|--------|------|-----|---------|------|
| 105 | 零食专场 | GMV 虚高 | 12,500 | 11,800 | **+700** |
| 208 | — | 数据缺失 | N/A | 3,500 | ⚠️ 仅订单表 |
| 312 | 虚拟直播间 | 订单虚高 | 8,900 | 9,200 | **-300** |

完整报告保存至 `reports/` 目录。

---

## 项目结构

```
├── hello_agents/
│   ├── tools/builtin/
│   │   ├── sql_tool.py              # SQLTool — schema/execute/validate
│   │   ├── diff_tool.py             # DiffTool — 跨表差异比对
│   │   └── report_tool.py           # ReportTool — Markdown 报告生成
│   └── agents/
│       └── reconciliation_agent.py  # ReconciliationAgent (ReActAgent 子类)
├── examples/
│   └── reconciliation_demo.py       # 完整 Demo
├── data/
│   ├── generate_mock_data.py        # 模拟数据生成脚本
│   └── mock_reconciliation.db       # SQLite 数据库（gitignore）
└── knowledge_base/
    └── table_docs/                  # 表结构文档（RAG 知识库）
```

---

## 面试要点（STAR 法则）

**Situation** — 业务方手工对账效率低、SQL 门槛高、易出错

**Task** — 基于 HelloAgents 框架构建 AI Agent，自然语言驱动自动化对账

**Action**
- 继承 `ReActAgent` 实现对账专用 Agent，内置 Thought→Action→Observation 循环
- 用 `@tool_action` 装饰器 + `ToolRegistry` 构建 5 个可展开工具
- SQL 先 `EXPLAIN` 校验再执行，杜绝 LLM 幻觉
- 支持跨列名比对（`total_gmv` ⟷ `total_order`）

**Result** — 一次典型对账（2 表、50+ 行）5-7 步自动完成

---

## 技术栈

- **Agent 框架**: HelloAgents (ReActAgent / Tool / ToolRegistry)
- **LLM**: DeepSeek (可替换为 OpenAI / Claude / 本地模型)
- **数据库**: SQLite (Demo) / 可扩展至 Hive / ClickHouse / Trino
- **RAG**: Qdrant + DashScope Embedding (表结构语义检索)
- **Python**: 3.10+

---

## License

MIT © 2026
