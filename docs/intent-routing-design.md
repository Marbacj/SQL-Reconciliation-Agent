# 意图路由 — 架构设计文档

> 版本：v1.0 | 2026-05-30

## 一、问题定义

当前 ReconciliationAgent 只有一个硬编码的 System Prompt，所有输入都走同一条 5 步对账流水线。当用户问"看一下 live_gmv 表有哪些字段"时，Agent 仍然走完整的对账流程（查表→生成两表 SQL→执行→比对→报告），浪费 Token 和时间。

**核心矛盾**：单一 Prompt 无法应对多意图场景。

## 二、设计方案：两段式 LLM-native 路由

### 2.1 整体架构

```
用户输入
  │
  ▼
┌──────────────────────────────────┐
│  Phase 1: 意图分类（轻量 LLM 调用） │
│  - 输入：用户原始 query             │
│  - 输出：IntentLabel + confidence  │
│  - 成本：~200 tokens               │
└──────────────┬───────────────────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌──────┐  ┌──────┐  ┌──────────┐
│对账  │  │即席  │  │Schema    │  ...
│Intent│  │查询  │  │查询      │
│      │  │Intent│  │Intent    │
└──┬───┘  └──┬───┘  └────┬─────┘
   │         │            │
   ▼         ▼            ▼
┌──────────────────────────────────┐
│  Phase 2: 路由执行                │
│  - 加载该 Intent 的 System Prompt │
│  - 过滤可用工具（只暴露相关的）      │
│  - 设置对应的 max_steps           │
│  - 注入该 Intent 专属的 few-shot   │
└──────────────────────────────────┘
```

### 2.2 为什么用两段式而非一段式

**一段式**（当前）：把所有工具和完整 Prompt 塞给 LLM，让它自己判断。简单场景（5 个工具）可以工作，但：
- Token 浪费：即席查询不需要 diff_compare 和 report_generate 的 schema
- 行为不可控：LLM 可能跳过关键步骤或走错流程
- 调试困难：不知道 LLM "为什么选了 A 而不是 B"

**两段式**（新设计）：
- 第一段分类极轻量（不需要 tool calling，纯文本分类）
- 第二段只需要该 Intent 对应的工具和 Prompt，Token 更省、行为更可控
- 每一步有明确的决策日志

### 2.3 分类策略：关键词 + LLM 兜底（混合分类器）

纯 LLM 分类每次要调 API，延迟高。混合策略：

```
输入 query
  │
  ▼
关键词快速匹配（零 LLM 调用）
  │ 匹配到？ → 返回 IntentLabel（延迟 0ms）
  │
  ▼ 没匹配到
LLM 分类（一次轻量调用）
  │ 输出：{intent: "reconciliation", confidence: 0.92}
  │
  ▼ confidence < 阈值？
澄清反问 → 让用户选择意图
```

关键词匹配规则（写在 Intent 定义中）：

| Intent | 触发词 |
|--------|--------|
| `reconciliation` | 对账、差异、对比、一致、核对、比一比 |
| `adhoc_query` | 查询、统计、汇总、多少、有哪些、SUM、COUNT |
| `schema_lookup` | 表结构、字段、schema、有哪些列、表名 |

## 三、核心组件

### 3.1 Intent（意图定义）

```python
@dataclass
class Intent:
    name: str                    # "reconciliation"
    description: str             # 人类可读描述
    keywords: List[str]          # 触发词
    system_prompt: str           # 该意图的专属 System Prompt
    required_tools: List[str]    # 需要的工具名列表
    max_steps: int               # 最大推理步数
    few_shot_tag: str            # CaseStore 过滤标签
```

### 3.2 IntentRegistry（意图注册表）

- 存储所有已注册的 Intent
- 提供关键词快速匹配
- 提供 LLM 分类用的 prompt（列出所有 intent 描述）

### 3.3 IntentRouter（路由器）

核心路由逻辑：
1. 接收用户 query
2. 调用 `IntentRegistry.classify(query)` → `IntentLabel`
3. 根据 IntentLabel 加载对应的 System Prompt + Tool 过滤 + few-shot
4. 注入到 Agent，执行

### 3.4 工具过滤机制

并非所有 Intent 都需要全部 5 个工具：

| Intent | sql_schema | sql_execute | sql_validate | diff_compare | report_generate |
|--------|:--:|:--:|:--:|:--:|:--:|
| reconciliation | ✅ | ✅ | ✅ | ✅ | ✅ |
| adhoc_query | ✅ | ✅ | ✅ | ❌ | ❌ |
| schema_lookup | ✅ | ❌ | ❌ | ❌ | ❌ |

工具过滤通过 `ToolRegistry.filter(names)` 实现，在路由后只注册该 Intent 需要的工具。

## 四、数据流（一次典型路由）

```
用户: "live_gmv 表有哪些字段？"
  │
  ▼
IntentRouter.route("live_gmv 表有哪些字段？")
  │
  ├─ ① IntentRegistry.classify()
  │     关键词匹配: "字段" → schema_lookup
  │     IntentLabel(name="schema_lookup", confidence=1.0, method="keyword")
  │
  ├─ ② 加载 schema_lookup Intent
  │     system_prompt: "你是数据库 Schema 查询助手..."
  │     required_tools: ["sql_schema"]
  │     max_steps: 2
  │
  ├─ ③ 过滤 ToolRegistry
  │     全部 5 个工具 → 只保留 sql_schema
  │
  ├─ ④ 注入 CaseStore（按 few_shot_tag="schema" 过滤）
  │
  └─ ⑤ 执行 ReAct Loop
       Thought → sql_schema("live_gmv") → Observation → Finish
       
       总成本: 1 次 LLM 调用（直接执行，关键词命中跳过了分类 LLM）
       当前不加路由: ~7 次 LLM 调用（走完整个对账流程）
```

## 五、实现清单

| 文件 | 内容 |
|------|------|
| `recon_core/core/intent.py` | Intent dataclass + IntentLabel |
| `recon_core/core/intent_registry.py` | IntentRegistry（注册 + 关键词匹配 + LLM 分类） |
| `recon_core/core/intent_router.py` | IntentRouter（分类 → 加载 → 过滤 → 注入） |
| `recon_core/agents/reconciliation_agent.py` | 集成 IntentRouter，替换硬编码 Prompt |
| `examples/reconciliation_ui.py` | 侧边栏展示路由决策（命中哪个 Intent + 置信度） |

## 六、扩展性

添加新意图只需 3 步：

```python
# 1. 定义
Intent(
    name="data_quality_check",
    keywords=["数据质量", "空值", "重复", "异常"],
    system_prompt="你是数据质量检查专家...",
    required_tools=["sql_schema", "sql_execute", "sql_validate"],
    max_steps=5,
)

# 2. 注册
registry.register(intent)

# 3. CaseStore 加标签
# few_shot_tag="quality"
```

不需要改任何路由代码。

## 七、与面试的对应

| 面试问 | 回答要点 |
|--------|---------|
| "为什么用两段式？" | 一段式把所有工具塞给 LLM，Token 浪费、行为不可控。两段式先分类再执行，每段职责单一 |
| "分类怎么做？" | 混合策略：关键词 O(1) 命中 → 零 LLM 调用；未命中 → LLM 分类 → 低置信度则反问 |
| "怎么保证不路由错？" | 关键词优先（确定性规则）+ LLM 兜底 + 低置信度反问 + 决策日志全量记录 |
| "怎么扩展新场景？" | 注册一个 Intent 对象即可，零路由代码改动 |
