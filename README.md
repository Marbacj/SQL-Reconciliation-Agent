# SQL 对账 Agent — 自然语言驱动的自动化数据对账系统

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

用户以自然语言描述对账需求（如"对比昨天直播 GMV 和订单金额差异"），Agent 通过 **ReAct 推理循环** 自动完成：**表结构发现 → SQL 生成与校验 → 执行 → 差异比对 → 报告输出**。

---

## 核心设计

### 推理范式：ReAct（Thought → Action → Observation）

Agent 不依赖预先编排的 DAG，而是通过 ReAct 循环自主推理每一步动作：

```
用户: "昨天直播GMV和订单金额有没有差异？"
  │
  ▼
┌──────────────────────────────────────────┐
│         ReconciliationAgent (ReAct)       │
│                                           │
│  Thought: 需要先看两表结构                 │
│  Action → sql_schema(live_gmv)            │
│  Observation: 6个字段, 26行, 主键live_id   │
│                                           │
│  Thought: 按live_id聚合两表, 生成SQL       │
│  Action → sql_execute(GMV汇总)            │
│  Observation: 返回25行                     │
│                                           │
│  Action → sql_execute(订单汇总)            │
│  Observation: 返回27行                     │
│                                           │
│  Thought: 行数不一致, 需要FULL OUTER JOIN  │
│  Action → diff_compare(左表, 右表)         │
│  Observation: 3处差异 (105/208/312)        │
│                                           │
│  Action → report_generate(报告)            │
│  Finish → "发现3处差异, 报告已保存"         │
└──────────────────────────────────────────┘
```

### 工具设计：Expandable Tool 模式

每个 Tool 是一个可展开的工具集，通过 `@tool_action` 装饰器将多个子工具收敛到一个类中：

```
ToolRegistry
├── SQLTool (expandable)
│   ├── sql_schema    → PRAGMA 读取表结构 + 示例数据
│   ├── sql_execute   → 执行 SELECT（内置 DDL/DML 安全拦截）
│   └── sql_validate  → EXPLAIN 语法校验，不实际执行
├── DiffTool (expandable)
│   └── diff_compare  → 双 SQL FULL OUTER JOIN · 按位跨列比对
└── ReportTool (expandable)
    └── report_generate → Markdown 报告 + 自动归档
```

**跨列名比对算法**：两表列名不同（如 `total_gmv` vs `total_order`）时，按列位置而非列名进行配对比对，输出标注 `total_gmv ⟷ total_order`。

---

## 快速开始

```bash
# 1. 安装
git clone https://github.com/Marbacj/SQL-Reconciliation-Agent.git
cd SQL-Reconciliation-Agent
pip install -e .

# 2. 配置 LLM
echo 'LLM_MODEL_ID=deepseek-chat
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com' > .env

# 3. 生成 Demo 数据（含 3 处故意差异）
python data/generate_mock_data.py

# 4. 运行对账（命令行）
python examples/reconciliation_demo.py

# 5. 或启动 Web UI
streamlit run examples/reconciliation_ui.py
```

---

## Demo 效果

Agent 完整执行 7 步推理，正确识别 3 处故意注入的差异：

| live_id | 问题类型 | GMV    | 订单金额   | 差异       |
| ------- | ---- | ------ | ------ | -------- |
| 105     | 数值差异 | 12,500 | 11,800 | **+700** |
| 208     | 数据缺失 | N/A    | 3,500  | ⚠️ 仅订单表  |
| 312     | 数值差异 | 8,900  | 9,200  | **-300** |

报告自动保存至 `reports/` 目录（Markdown 格式）。

---

## 项目结构

```
├── hello_agents/                  # 核心框架
│   ├── core/                      # Agent 基类 · LLM 抽象 · 流式推理
│   ├── agents/                    # ReActAgent · ReconciliationAgent
│   ├── tools/                     # 工具系统 · 注册表 · 熔断器
│   │   └── builtin/               # SQLTool · DiffTool · ReportTool
│   └── context/                   # 上下文工程 · Token 管理
├── examples/
│   ├── reconciliation_demo.py     # 命令行 Demo
│   └── reconciliation_ui.py       # Streamlit Web UI
├── data/
│   ├── generate_mock_data.py      # 模拟数据生成（26+27 行，3 处差异）
│   └── mock_reconciliation.db     # SQLite 数据库（gitignored）
├── knowledge_base/table_docs/     # 表结构文档（RAG 知识库）
├── docs/
│   └── reconciliation-agent-design.md  # 架构设计文档
└── tests/                         # 单元测试
```

---

## 技术栈

- **推理引擎**: ReAct Agent · Thought→Action→Observation 循环
- **LLM**: DeepSeek / OpenAI / Claude（通过统一适配层切换）
- **工具系统**: Expandable Tool · 类型化参数 · 熔断器 · Tool Registry
- **上下文管理**: Token 计数 · 智能截断 · 会话持久化
- **数据库**: SQLite (Demo) / 可扩展至 Hive / ClickHouse / Trino
- **RAG**: Qdrant 向量库 · 表结构语义检索

---

## License

MIT
