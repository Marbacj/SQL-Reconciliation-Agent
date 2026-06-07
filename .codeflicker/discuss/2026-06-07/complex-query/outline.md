# 讨论：为 SQL Reconciliation Agent 补充复杂查询能力

> 状态：进行中 | 轮次：R2 | 日期：2026-06-07

## 🔵 当前焦点

- **意图路由：新增 `complex_query` Intent vs 扩展现有 `adhoc_query`**
- **Schema Linking：`sql_schema_all` 全量返回 vs `sql_schema_search` 关键词检索**

## ⚪ 待讨论

- [ ] sql_schema_search 返回格式：完整 schema vs 摘要（字段列表）
- [ ] complex_query keywords 具体词表，以及与 adhoc_query 的边界

## ✅ 已确认

- 新增 `COMPLEX_QUERY_INTENT`，职责清晰，Prompt 和 max_steps 独立配置
- Schema Linking 工具使用 `sql_schema_search(keyword)`，关键词匹配相关表
- 不新建 Agent 文件，复用 react_agent，由 Intent 路由注入 Prompt
- max_steps = 8（比 adhoc_query 翻倍，给 JOIN 重试留余量）
- 改动范围：仅 `intent.py` + `sql_tool.py` 两个文件

## ❌ 已否决

（暂无）

## 📁 归档

（暂无）
