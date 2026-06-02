# 讨论：SQL Reconciliation Agent 从零重构方案

> 状态：已就绪进入实施 | 轮次：R2 | 日期：2026-06-01

## 🔵 当前焦点

- **进入实施计划阶段**：四个关键约束已拍板，可生成 plan.md

## ⚪ 待讨论

（无 - 关键决策均已确认，剩余细节进入 plan 模式后逐项落实）

## ✅ 已确认

- D01 目标定位：**面试简历升级**（讲故事第一，工程完整度第二）→ [D01](./decisions/D01-rebuild-strategy.md) (#R2)
- D02 时间窗口：**6 周以上完整重构**（Stage 0-5 全跑通）→ [D01](./decisions/D01-rebuild-strategy.md) (#R2)
- D03 编排框架：**切换到 LangGraph**，放弃 HelloAgents 自研壳子 → [D01](./decisions/D01-rebuild-strategy.md) (#R2)
- D04 基础设施：**允许引入 Qdrant / SQLite / Redis 等外部依赖** → [D01](./decisions/D01-rebuild-strategy.md) (#R2)

## ❌ 已否决

- 自研 Agent 编排框架方案（原因：边际收益递减，不利于简历可信度）
- 纯单文件零依赖方案（原因：与"工程现代化"目标冲突）

---

## 📐 v2 架构（已确认方向）

详见 [D01-rebuild-strategy.md](./decisions/D01-rebuild-strategy.md)

### 五阶段 Roadmap

| Stage | 内容 | 工时 | 风险 |
|-------|------|------|------|
| Stage 0 | 评测先行（50 条 Golden Set + 4 个 metric） | 0.5 周 | 低 |
| Stage 1 | 核心抽象（Pydantic Tool / sqlglot / LLM Gateway / OTel） | 1 周 | 中 |
| Stage 2 | LangGraph 编排重写（AgentContext + 模式切换） | 1.5 周 | 高 |
| Stage 3 | Hybrid RAG（BM25 + Dense + Rerank + RAG-as-Tool） | 1 周 | 中 |
| Stage 4 | Memory v2 + Self-Evolution Sandbox | 1.5 周 | 高 |
| Stage 5 | 生产化（FastAPI + Docker + Metrics + 文档） | 1 周 | 低 |
| **合计** | — | **~6.5 周** | — |

---

## 📁 归档

| 问题 | 结论 | 详情 |
|------|------|------|
| 整体方案 | v2 五阶段，LangGraph + 外部依赖 + 简历优先 | [→ D01](./decisions/D01-rebuild-strategy.md) |
