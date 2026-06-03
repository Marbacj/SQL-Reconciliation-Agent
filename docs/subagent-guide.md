# 子代理机制指南（Subagent Mechanism）

## 📖 概述

**子代理机制**允许主 Agent 将复杂任务分解为子任务，委派给独立的子 Agent 执行，实现上下文隔离和工具权限控制。

### 核心特性

- ✅ **上下文隔离**：子代理使用独立历史，不污染主 Agent
- ✅ **工具过滤**：限制子代理可用工具（只读、完全访问、自定义）
- ✅ **灵活组合**：所有 Agent 类型都可作为子代理
- ✅ **成本优化**：子任务可用轻量模型（节省 70%）
- ✅ **零配置**：TaskTool 自动注册

---

## 🚀 快速开始

### 1. 零配置使用（推荐）

```python
from recon_core import ReActAgent, HelloAgentsLLM, Config

# 启用子代理机制
config = Config(subagent_enabled=True)
agent = ReActAgent("main", HelloAgentsLLM(), config=config)

# TaskTool 已自动注册，Agent 可以直接使用
agent.run("使用 Task 工具探索项目结构")

# Agent 会自动调用 TaskTool，创建子代理执行任务
```

### 2. 手动调用子代理

```python
from recon_core import ReActAgent, HelloAgentsLLM
from recon_core.tools.tool_filter import ReadOnlyFilter

# 创建主 Agent 和子 Agent
main_agent = ReActAgent("main", llm, tool_registry=registry)
explore_agent = ReActAgent("explorer", llm, tool_registry=registry)

# 手动调用子代理（上下文隔离）
result = explore_agent.run_as_subagent(
    task="探索 recon_core/core/ 目录",
    tool_filter=ReadOnlyFilter(),  # 只读权限
    return_summary=True
)

print(f"子代理结果: {result['summary']}")
print(f"主 Agent 历史长度: {len(main_agent.get_history())}")  # 未被污染
```

---

## 💡 核心概念

### 1. 上下文隔离

**问题：** 主 Agent 和子任务共享历史，导致上下文混乱

```python
# ❌ 不好：共享历史
agent.run("分析项目")
agent.run("生成报告")
agent.run("代码审查")
# 历史混在一起，上下文混乱
```

**解决：** 子代理使用独立历史

```python
# ✅ 好：上下文隔离
main_agent.run("分析项目")  # 主任务

# 子任务 1：探索（独立历史）
explore_agent.run_as_subagent("探索项目结构")

# 子任务 2：分析（独立历史）
analyze_agent.run_as_subagent("分析架构设计")

# 主 Agent 历史保持清晰
```

### 2. 工具过滤

**3 种内置过滤器：**

```python
from recon_core.tools.tool_filter import (
    ReadOnlyFilter,      # 只读工具（探索、分析）
    FullAccessFilter,    # 完全访问（排除危险工具）
    CustomFilter         # 自定义白名单/黑名单
)
```

**ReadOnlyFilter（只读）：**
```python
readonly = ReadOnlyFilter()
allowed = readonly.filter(["Read", "Write", "Bash", "Search"])
# 返回：["Read", "Search"]
# 只允许：Read, Search, Calculator, Memory, RAG, Note
```

**FullAccessFilter（完全访问）：**
```python
full = FullAccessFilter()
allowed = full.filter(["Read", "Write", "Bash", "Terminal"])
# 返回：["Read", "Write"]
# 排除：Bash, Terminal, Execute（危险工具）
```

**CustomFilter（自定义）：**
```python
# 白名单模式
custom = CustomFilter(allowed=["Read", "Search"], mode="whitelist")
allowed = custom.filter(["Read", "Write", "Search"])
# 返回：["Read", "Search"]

# 黑名单模式
custom = CustomFilter(denied=["Write", "Edit"], mode="blacklist")
allowed = custom.filter(["Read", "Write", "Edit"])
# 返回：["Read"]
```

### 3. Agent 工厂

**create_agent() - 统一创建接口：**
```python
from recon_core.agents.factory import create_agent

# 创建不同类型的 Agent
react_agent = create_agent("react", "explorer", llm, registry)
reflection_agent = create_agent("reflection", "thinker", llm, registry)
plan_agent = create_agent("plan", "planner", llm, registry)
simple_agent = create_agent("simple", "assistant", llm, registry)
```

**default_subagent_factory() - 默认工厂：**
```python
from recon_core.agents.factory import default_subagent_factory

subagent = default_subagent_factory(
    agent_type="react",
    llm=llm,
    tool_registry=registry,
    config=Config(subagent_max_steps=10)
)
```

---

## 📝 使用指南

### 1. TaskTool 参数

TaskTool 支持以下参数：

```python
{
    "task": "任务描述",
    "agent_type": "react",           # react / reflection / plan / simple
    "tool_filter": "readonly",       # readonly / full / none
    "max_steps": 15                  # 最大步数（可选）
}
```

**示例：**
```python
# Agent 调用 TaskTool
agent.run("""
使用 Task 工具执行以下任务：
- task: 探索 recon_core/core/ 目录
- agent_type: react
- tool_filter: readonly
""")
```

### 2. 自定义子代理工厂

```python
from recon_core.agents.factory import create_agent, default_subagent_factory
from recon_core.tools.builtin.task_tool import TaskTool

# 主模型（强大但昂贵）
main_llm = HelloAgentsLLM(provider="openai", model="gpt-4")

# 轻量模型（快速且便宜）
light_llm = HelloAgentsLLM(provider="deepseek", model="deepseek-chat")

def my_agent_factory(agent_type: str):
    """根据任务类型选择模型"""
    if agent_type in ["react", "plan"]:
        # 探索和规划用轻量模型
        llm = light_llm
    else:
        # 反思和代码实现用主模型
        llm = main_llm
    
    return default_subagent_factory(
        agent_type=agent_type,
        llm=llm,
        tool_registry=registry,
        config=Config(subagent_max_steps=10)
    )

# 手动注册 TaskTool
task_tool = TaskTool(agent_factory=my_agent_factory, tool_registry=registry)
registry.register_tool(task_tool)
```

### 3. 不同类型的子代理

```python
from recon_core.agents.factory import create_agent

# 创建不同类型的子代理
agents = {
    "react": create_agent("react", "explorer", llm, registry),
    "reflection": create_agent("reflection", "thinker", llm, registry),
    "plan": create_agent("plan", "planner", llm, registry),
    "simple": create_agent("simple", "assistant", llm, registry)
}

# 根据任务选择合适的子代理类型
explore_result = agents["react"].run_as_subagent(
    task="探索项目",
    tool_filter=ReadOnlyFilter()
)

analysis_result = agents["reflection"].run_as_subagent(
    task="深度分析",
    tool_filter=ReadOnlyFilter()
)

plan_result = agents["plan"].run_as_subagent(
    task="制定计划",
    tool_filter=FullAccessFilter()
)
```

---

## 📊 实际案例

### 案例 1：复杂项目分析

**场景：** 分析大型代码库，生成架构报告

```python
# 主 Agent（ReActAgent）
main_agent = ReActAgent("main", main_llm, tool_registry=registry)

# 任务分解
result = main_agent.run("""
分析项目架构，生成报告：

1. 使用 Task 工具探索项目结构（agent_type=react, tool_filter=readonly）
2. 使用 Task 工具分析架构设计（agent_type=reflection, tool_filter=readonly）
3. 使用 Task 工具制定优化计划（agent_type=plan, tool_filter=readonly）
4. 整合结果，生成报告
""")
```

**优势：**
- ✅ 每个子任务上下文隔离，不互相干扰
- ✅ 探索任务只能读取，不会误修改文件
- ✅ 子任务可用轻量模型，节省成本

### 案例 2：多阶段代码审查

**场景：** 代码审查 + 自动修复

```python
main_agent.run("""
代码审查流程：

1. 扫描代码问题（Task 工具，readonly）
2. 分析问题严重性（Task 工具，reflection）
3. 自动修复问题（Task 工具，full access）
4. 生成审查报告
""")
```

**优势：**
- ✅ 扫描阶段只读，避免误修改
- ✅ 修复阶段有写权限，但排除危险工具
- ✅ 每个阶段独立历史，清晰可追溯

### 案例 3：成本优化

**场景：** 长时间运行的数据处理任务

**配置：**
- 主 Agent：GPT-4（$0.03/1K tokens）
- 子 Agent：DeepSeek（$0.001/1K tokens）

**任务分配：**
```python
def cost_optimized_factory(agent_type: str):
    # 探索、规划、简单处理 → DeepSeek
    if agent_type in ["react", "plan", "simple"]:
        return create_agent(agent_type, "sub", light_llm, registry)
    # 复杂决策、代码生成 → GPT-4
    else:
        return create_agent(agent_type, "sub", main_llm, registry)
```

**成本节省：**
```
之前：100% GPT-4 = $30
之后：30% GPT-4 + 70% DeepSeek = $9 + $0.7 = $9.7
节省：68%
```

---

## 🎯 最佳实践

### 1. 合理选择工具过滤器

```python
# ❌ 不好：探索任务给完全访问权限
explore_agent.run_as_subagent(
    task="探索项目",
    tool_filter=FullAccessFilter()  # 可能误修改文件
)

# ✅ 好：探索任务只给只读权限
explore_agent.run_as_subagent(
    task="探索项目",
    tool_filter=ReadOnlyFilter()  # 安全
)
```

### 2. 根据任务选择 Agent 类型

```python
# 探索任务 → ReActAgent（快速迭代）
create_agent("react", "explorer", llm, registry)

# 深度分析 → ReflectionAgent（反思优化）
create_agent("reflection", "analyzer", llm, registry)

# 规划任务 → PlanAgent（先规划后执行）
create_agent("plan", "planner", llm, registry)

# 简单对话 → SimpleAgent（无需复杂推理）
create_agent("simple", "assistant", llm, registry)
```

### 3. 限制子代理步数

```python
# ✅ 好：限制子代理步数，避免无限循环
result = agent.run_as_subagent(
    task="探索项目",
    max_steps_override=10  # 最多 10 步
)
```

---

## 🔧 高级用法

### 1. 获取子代理元数据

```python
result = agent.run_as_subagent(task="探索项目")

# 查看元数据
print(result["metadata"])
# {
#     "steps": 5,
#     "duration_seconds": 12.3,
#     "tool_calls": {"Read": 3, "Search": 2},
#     "total_tokens": 1500
# }
```

### 2. 自定义摘要生成

```python
# 子代理返回完整结果（不生成摘要）
result = agent.run_as_subagent(
    task="探索项目",
    return_summary=False
)

# 手动生成摘要
summary = my_custom_summarize(result["result"])
```

### 3. 嵌套子代理

```python
# 主 Agent
main_agent = ReActAgent("main", llm, tool_registry=registry)

# 子 Agent 1
sub1_agent = ReActAgent("sub1", llm, tool_registry=registry)

# 子 Agent 2（嵌套）
sub2_agent = ReActAgent("sub2", llm, tool_registry=registry)

# 主 Agent 调用子 Agent 1
result1 = sub1_agent.run_as_subagent(task="任务 1")

# 子 Agent 1 调用子 Agent 2（嵌套）
result2 = sub2_agent.run_as_subagent(task="任务 2")
```

---

## 🔗 相关文档

- [工具过滤器](./tool-response-protocol.md) - ToolFilter 详细说明
- [会话持久化](./session-persistence-guide.md) - 保存子代理会话
- [可观测性](./observability-guide.md) - 追踪子代理执行

---

## ❓ 常见问题

**Q: 子代理会污染主 Agent 的历史吗？**

A: 不会。子代理使用独立历史，执行后自动恢复主 Agent 状态。

**Q: 如何禁用子代理机制？**

A: 设置 `subagent_enabled=False`：
```python
config = Config(subagent_enabled=False)
```

**Q: TaskTool 和手动调用 run_as_subagent() 的区别？**

A:
- **TaskTool**: Agent 自动调用，零配置
- **run_as_subagent()**: 手动调用，更灵活

**Q: 子代理可以访问主 Agent 的工具吗？**

A: 可以，但受工具过滤器限制：
- `ReadOnlyFilter`: 只能访问只读工具
- `FullAccessFilter`: 可以访问大部分工具（排除危险工具）
- `CustomFilter`: 自定义白名单/黑名单

**Q: 子代理的成本如何计算？**

A: 子代理独立计费：
```python
# 主 Agent Token: 10,000
# 子 Agent 1 Token: 2,000
# 子 Agent 2 Token: 1,500
# 总计: 13,500 tokens
```

---

## 📈 性能指标

### 上下文隔离效果

| 场景         | 无隔离（共享历史） | 有隔离（子代理）  |
| ------------ | ------------------ | ----------------- |
| 历史长度     | 100+ 条消息        | 主 20 + 子 10     |
| 上下文清晰度 | 混乱               | 清晰              |
| Token 消耗   | 50,000             | 15,000（节省70%） |

### 成本优化效果

| 模型组合               | 成本（1M tokens） | 节省比例 |
| ---------------------- | ----------------- | -------- |
| 全部 GPT-4             | $30               | 0%       |
| 主 GPT-4 + 子 GPT-3.5  | $12               | 60%      |
| 主 GPT-4 + 子 DeepSeek | $9.7              | 68%      |

---

**最后更新**: 2026-02-21


