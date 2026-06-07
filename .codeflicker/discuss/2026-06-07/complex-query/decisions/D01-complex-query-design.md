# 复杂查询能力补充方案

**决策时间**：#R3
**状态**：✅ 已确认
**关联大纲**：[返回大纲](../outline.md)

---

## 📋 背景

### 问题/需求
项目当前仅支持对账场景（两表 diff_compare）和简单单表查询（adhoc_query），无法处理多表 JOIN 类复杂查询。典型失败案例：用户问"销量最高的十个订单的最近变动记录"，被误判为对账场景，生成了两条独立 SQL 而非带 JOIN 的复杂查询。

### 约束条件
- 不能破坏现有对账流程
- 改动面尽量小，只动必要的文件
- 不新建 Agent 文件，复用 react_agent

---

## 🎯 目标

在已有架构上新增一条"复杂查询"执行路径，支持多表 JOIN、子查询、聚合+排序等场景。

---

## 📊 方案对比

| 方案 | 描述 | 优势 | 劣势 | 决策 |
|------|------|------|------|------|
| 扩展 adhoc_query | 把多表查询塞进现有 adhoc_query Intent | 不增加 Intent 数量 | Prompt 臃肿，max_steps 难以兼顾 | ❌ |
| 新增 complex_query Intent | 独立 Intent，独立 Prompt 和 max_steps | 职责清晰，可独立调优 | Intent 数量 +1，需维护分类边界 | ✅ |
| sql_schema_all 工具 | 一次返回所有表 schema | 实现简单 | context 占用大，干扰 LLM 推理 | ❌ |
| sql_schema_search 工具 | 关键词匹配返回相关表摘要 | 精准，context 可控 | 实现稍复杂 | ✅ |

---

## ✅ 最终决策

### 改动文件

**`recon_core/core/intent.py`** — 新增 COMPLEX_QUERY_INTENT

```python
COMPLEX_QUERY_INTENT = Intent(
    name="complex_query",
    description="复杂查询：多表 JOIN、子查询、聚合+排序等跨表场景",
    keywords=["JOIN", "关联", "跟", "及其", "对应的", "变动记录",
              "日志", "最高的", "历史记录", "最近", "带有", "包含"],
    max_steps=8,
    # Prompt: sql_schema_search → 理解表关系 → JOIN SQL → validate → execute → 0行则重写
)
```

**`recon_core/tools/builtin/sql_tool.py`** — 新增 sql_schema_search 子工具

- 遍历所有表名 + 字段名，模糊匹配 keyword
- 返回摘要格式（字段列表为主）
- 命中分最高的表额外补充示例数据（前3行）

### 预期效果
- "变动记录"、"日志"、"关联"等词触发 complex_query
- LLM 通过 sql_schema_search 快速找到相关表，无需多轮 sql_schema 调用
- JOIN SQL 生成后经 validate → execute 双重保障
