# 讨论：SQL-Recon-Agent 进化成 Data Agent 的 Checklist

> 状态：进行中 | 轮次：R1 | 日期：2026-06-11

## 🔵 当前焦点

- **梳理完整的 Data Agent Checklist（按优先级分层）**

---

## ✅ 已确认（项目现有能力）

| 能力 | 所在模块 | 完成度 |
|------|---------|--------|
| 意图路由（对账/趋势/TopN/同环比） | `orchestration/rules/intent_rules.py` | ✅ 完整 |
| 多步骤编排（并行子任务） | `orchestration/nodes/plan.py` | ✅ 完整 |
| Schema Linking（RAG + PRAGMA 校验） | `rag/schema_indexer.py` + `tools/schema_inspector.py` | ✅ 完整 |
| SQL 错误自修复（最大重试 3 次） | `orchestration/nodes/observe.py` + `error_diagnosis.py` | ✅ 完整 |
| SQL 安全拦截（AST 黑名单 + 只读） | `infra/sql_safety.py` | ✅ 完整 |
| 三层记忆（episodic/semantic/working） | `memory/store.py` | ✅ 完整 |
| 多轮澄清系统（chips UI） | `orchestration/nodes/clarify.py` | ✅ 完整 |
| Range Guard（业务合理性检查） | `orchestration/nodes/observe.py` | ✅ 完整 |
| 多数据库适配（SQLite/MySQL/PG） | `adapters/` | ✅ 完整 |
| Golden Set 离线评测 | `tests/eval/` | ✅ 完整 |

---

## ⚪ 待讨论 / 待实现

### P0：数据源管理层（解锁「任意数据库接入」）

- [ ] **动态数据源注册 API**（POST /datasources，用户粘贴连接串即可用）
- [ ] **Schema 自动发现与增量向量化**（接入新库自动爬表结构 → 写入 RAG）
- [ ] **数据源连通性健康探针**（接入时实时检测 + 友好错误提示）
- [ ] **前端「接入数据源」入口**（datasources.json 现在是空的）
- [ ] **多租户数据源隔离**（A 用户的库不暴露给 B 用户的 query）

### P0：分析增强层（从「查数字」到「给洞察」）

- [ ] **InsightNode 节点**（把 SQL 结果集 + 业务上下文 → LLM 生成自然语言结论）
- [ ] **指标归因引擎**（"GMV 跌了" → 自动拆维度找根因：大区/品类/时段）
- [ ] **异常检测触发**（同比/环比突变自动触发告警 + 解释，复用 GrowthRateCalculatorTool）
- [ ] **结论置信度标注**（让用户知道这个 Insight 的依据有多充分）

### P1：结果消费层（让结果可用、可传播）

- [ ] **图表渲染**（把 DataFrame 结果转为 Echarts/Plotly JSON，前端渲染）
- [ ] **Excel / CSV 导出**（openpyxl，reports/ 目录现在只有 Markdown）
- [ ] **定时订阅推送**（cron + Webhook，"每天 9 点给我跑上月对账"）
- [ ] **报告分享链接**（报告 URL 可分享，不用截图）

### P1：Agent 治理层（生产化稳定性）

- [ ] **工具动态注册**（tools/registry.py 现在是静态的，支持热加载新工具）
- [ ] **Cost 预算强控制**（core/budget.py 存在但未接入主流程强制截断）
- [ ] **用户级 Schema 上下文隔离**（多用户并发时防止 schema 污染）
- [ ] **Query 限速 + 熔断**（防止单用户打爆 token quota）

### P2：生态扩展层（Data Agent 护城河）

- [ ] **非结构化数据源接入**（CSV 上传 / Excel 上传 → 自动建临时表）
- [ ] **BI 工具对接**（Metabase / Superset API，让 Agent 可以写回看板）
- [ ] **自然语言报警规则定义**（"当退款率 > 5% 时发我消息"）
- [ ] **Agent 技能市场**（skill_library 现在有但未暴露给用户自定义）

---

## 📁 归档

*（暂无）*
