# 上下文工程指南（Context Engineering）

## 📖 概述

**上下文工程**是 HelloAgents 框架的核心能力，解决长对话中的上下文爆窗、Token 成本爆炸和缓存失效问题。

### 解决的问题

**之前：**
- ❌ 长对话无限增长，最终爆窗
- ❌ 无压缩机制，Token 成本持续增长
- ❌ 工具输出可能塞满上下文
- ❌ 随意修改历史，破坏 KV Cache

**之后：**
- ✅ 自动历史压缩（summary + 最近 N 轮）
- ✅ 缓存友好设计（只追加，不编辑）
- ✅ 工具输出统一截断
- ✅ 支持会话序列化/反序列化

---

## 🚀 快速开始

### 1. 自动历史压缩（简单摘要）

```python
from recon_core import ReActAgent, HelloAgentsLLM, Config

# 配置历史压缩（默认：简单摘要）
config = Config(
    context_window=128000,           # 上下文窗口大小
    compression_threshold=0.8,       # 压缩阈值（80%）
    min_retain_rounds=10,            # 保留最近 10 轮
    enable_smart_compression=False   # 默认：简单摘要（无需额外 API）
)

agent = ReActAgent("assistant", HelloAgentsLLM(), config=config)

# 长对话自动压缩
for i in range(50):
    agent.run(f"任务 {i}")
    # 当历史达到 80% 窗口时，自动压缩为 summary + 最近 10 轮
```

**简单摘要示例**：
```
此会话包含 40 轮对话：
- 用户消息：40 条
- 助手消息：40 条
- 总消息数：80 条

（历史已压缩，保留最近 10 轮完整对话）
```

### 2. 智能摘要（可选，需额外 API）

```python
# 启用智能摘要（使用轻量 LLM 生成结构化摘要）
config = Config(
    enable_smart_compression=True,      # 启用智能摘要
    summary_llm_provider="deepseek",    # 摘要专用 LLM
    summary_llm_model="deepseek-chat",
    summary_max_tokens=800,
    summary_temperature=0.3,
    min_retain_rounds=10
)

agent = ReActAgent("assistant", HelloAgentsLLM(), config=config)
```

**智能摘要示例**：
```
## 历史摘要（80 条消息）

**任务目标**：分析大型代码库并生成架构报告
**关键决策**：采用模块化分析策略，优先处理核心模块
**已完成工作**：
- 扫描项目结构
- 分析依赖关系
- 识别架构模式
**待处理事项**：生成最终报告，优化建议
**重要发现**：发现循环依赖问题，需要重构

---
（已压缩，保留最近 10 轮完整对话）
```

**成本对比**：
- 简单摘要：0 Token（统计信息）
- 智能摘要：~800 Token/次（DeepSeek: $0.0008/次，不到 1 分钱）

### 2. 工具输出截断

```python
from recon_core import Config

config = Config(
    tool_output_max_lines=2000,      # 最大行数
    tool_output_max_bytes=51200,     # 最大字节数（50KB）
    tool_output_dir="tool-output",   # 完整输出保存目录
    tool_output_truncate_direction="head"  # 截断方向
)

agent = ReActAgent("assistant", llm, config=config)

# 工具输出超过限制时自动截断
agent.run("读取大文件")
# 自动截断 + 保存完整输出到 tool-output/tool_xxx.json
```

---

## 💡 核心组件

### 1. HistoryManager - 历史管理器

**特性：**
- ✅ 只追加，不编辑（缓存友好）
- ✅ 自动压缩历史
- ✅ 精确的轮次边界检测
- ✅ 支持序列化/反序列化
- ✅ 智能摘要生成（可选）

**使用示例：**
```python
from recon_core.context import HistoryManager

manager = HistoryManager(
    min_retain_rounds=10,
    compression_threshold=0.8
)

# 添加消息
manager.append(Message(role="user", content="你好"))
manager.append(Message(role="assistant", content="你好！"))

# 检查是否需要压缩
if manager.should_compress(context_window=128000):
    # 压缩历史
    manager.compress(
        context_window=128000,
        summarize_fn=lambda msgs: "历史摘要..."
    )

# 获取完整历史（summary + 最近轮次）
messages = manager.get_messages()
```

### 2. TokenCounter - Token 计数器（新增）

**特性：**
- ✅ 本地预估 Token 数（无需 API 调用）
- ✅ 缓存机制（避免重复计算）
- ✅ 增量计算（只计算新增消息）
- ✅ 降级方案（tiktoken 不可用时使用字符估算）

**使用示例：**
```python
from recon_core.context import TokenCounter

counter = TokenCounter(model="gpt-4")

# 计算单条消息
tokens = counter.count_message(message)

# 计算消息列表
total = counter.count_messages(messages)

# 缓存统计
stats = counter.get_cache_stats()
# {"cached_messages": 50, "total_cached_tokens": 12500}
```

**性能优化：**
- **压缩判断**：从 O(n) 优化到 O(1)
- **Token 计算**：缓存 + 增量，避免重复计算
- **内存优化**：只缓存必要信息

**压缩效果示例：**
```python
# 之前：每次判断需要遍历整个历史（O(n)）
def _should_compress(self):
    history = self.history_manager.get_history()
    tokens = sum(estimate_tokens(msg) for msg in history)  # O(n)
    return tokens > threshold

# 之后：使用缓存的 Token 数（O(1)）
def _should_compress(self):
    return self._history_token_count > threshold  # O(1)
```

**压缩效果：**
```
压缩前：
- 50 轮对话 = 100 条消息 = 50,000 tokens

压缩后：
- 1 条 summary = 500 tokens
- 最近 10 轮 = 20 条消息 = 10,000 tokens
- 总计：10,500 tokens（节省 79%）
```

### 2. ObservationTruncator - 输出截断器

**特性：**
- ✅ 统一截断规则
- ✅ 多方向截断（head/tail/head_tail）
- ✅ 自动保存完整输出
- ✅ 返回结构化截断信息

**使用示例：**
```python
from recon_core.context import ObservationTruncator

truncator = ObservationTruncator(
    max_lines=2000,
    max_bytes=51200,
    truncate_direction="head",
    output_dir="tool-output"
)

# 截断长输出
result = truncator.truncate("search_tool", long_output)

# 返回结构化信息
{
    "truncated": True,
    "preview": "...",  # 截断后的预览
    "full_output_path": "tool-output/tool_xxx.json",
    "stats": {
        "original_lines": 5000,
        "truncated_lines": 2000,
        "original_bytes": 150000,
        "truncated_bytes": 51200
    }
}
```

**截断方向：**
- `head`: 保留开头（适合日志、错误信息）
- `tail`: 保留结尾（适合实时输出）
- `head_tail`: 保留开头和结尾（适合长文件）

### 3. Message 类增强

**新增功能：**
```python
from recon_core.core import Message

# 支持 summary role
msg = Message(role="summary", content="历史摘要...")

# 增强的序列化
data = msg.to_dict()
# {
#     "role": "summary",
#     "content": "...",
#     "timestamp": "2026-02-21T10:30:00",
#     "metadata": {...}
# }

# 反序列化
msg = Message.from_dict(data)

# 转换为文本（用于上下文构建）
text = msg.to_text()
```

---

## 📝 配置选项

### Config 类扩展

```python
from recon_core import Config

config = Config(
    # 上下文工程配置
    context_window=128000,              # 上下文窗口大小
    compression_threshold=0.8,          # 压缩阈值（80%）
    min_retain_rounds=10,               # 保留最小轮次数
    enable_smart_compression=False,     # 智能摘要（需额外 LLM 调用）
    
    # 工具输出截断配置
    tool_output_max_lines=2000,         # 最大行数
    tool_output_max_bytes=51200,        # 最大字节数
    tool_output_dir="tool-output",      # 输出目录
    tool_output_truncate_direction="head"  # 截断方向
)
```

---

## 📊 实际案例

### 案例 1：长对话压缩

**场景：** 50 轮对话，每轮 1000 tokens

**之前：**
```
总 Token: 50 × 1000 = 50,000 tokens
成本: 50,000 × $0.03/1K = $1.50
```

**之后（压缩）：**
```
Summary: 500 tokens
最近 10 轮: 10 × 1000 = 10,000 tokens
总 Token: 10,500 tokens
成本: 10,500 × $0.03/1K = $0.315
节省: 79%
```

### 案例 2：工具输出截断

**场景：** 读取 10MB 日志文件

**之前：**
```
完整输出: 10MB = 2,500,000 tokens
上下文爆窗 ❌
```

**之后（截断）：**
```
截断输出: 50KB = 12,500 tokens
完整输出保存到: tool-output/tool_xxx.json
Agent 可以继续工作 ✅
```

### 案例 3：缓存友好设计

**之前（修改历史）：**
```python
# 修改历史中的消息
history[5].content = "修改后的内容"
# ❌ 破坏 KV Cache，需要重新计算
```

**之后（只追加）：**
```python
# 只追加新消息
manager.append(Message(role="summary", content="摘要"))
manager.append(Message(role="user", content="新问题"))
# ✅ 保持缓存有效，节省计算
```

---

## 🎯 最佳实践

### 1. 合理设置压缩阈值

```python
# ❌ 不好：阈值太低，频繁压缩
config = Config(compression_threshold=0.3)  # 30% 就压缩

# ✅ 好：阈值适中，平衡性能和成本
config = Config(compression_threshold=0.8)  # 80% 时压缩
```

### 2. 保留足够的历史轮次

```python
# ❌ 不好：保留太少，丢失上下文
config = Config(min_retain_rounds=3)

# ✅ 好：保留足够轮次，维持对话连贯性
config = Config(min_retain_rounds=10)
```

### 3. 根据场景选择截断方向

```python
# 日志分析：保留开头（错误通常在开头）
config = Config(tool_output_truncate_direction="head")

# 实时输出：保留结尾（最新信息在结尾）
config = Config(tool_output_truncate_direction="tail")

# 长文件：保留开头和结尾
config = Config(tool_output_truncate_direction="head_tail")
```

---

## 🔧 高级用法

### 1. 智能摘要（可选）

```python
from recon_core import Config

# 启用智能摘要（需要额外 LLM 调用）
config = Config(
    enable_smart_compression=True,
    compression_threshold=0.8
)

agent = ReActAgent("assistant", llm, config=config)

# 压缩时会调用 LLM 生成智能摘要
# 摘要质量更高，但会消耗额外 Token
```

**智能摘要 vs 简单摘要：**

| 类型     | 质量 | Token 消耗 | 适用场景           |
| -------- | ---- | ---------- | ------------------ |
| 简单摘要 | 中等 | 0          | 一般对话           |
| 智能摘要 | 高   | 500-1000   | 复杂任务、长期记忆 |

### 2. 手动压缩历史

```python
# 获取 HistoryManager
manager = agent.history_manager

# 手动触发压缩
if manager.should_compress(context_window=128000):
    manager.compress(
        context_window=128000,
        summarize_fn=lambda msgs: "自定义摘要逻辑"
    )
```

### 3. 序列化历史

```python
# 导出历史
history_data = manager.to_dict()
# {
#     "messages": [...],
#     "summary": "...",
#     "compressed": True
# }

# 导入历史
manager.from_dict(history_data)
```

---

## 🔗 相关文档

- [会话持久化](./session-persistence-guide.md) - 保存和恢复会话
- [可观测性](./observability-guide.md) - 追踪上下文使用情况
- [工具响应协议](./tool-response-protocol.md) - 工具输出标准化

---

## ❓ 常见问题

**Q: 压缩会丢失信息吗？**

A: 会丢失部分细节，但保留关键信息：
- 保留：任务目标、重要决策、最近对话
- 丢失：中间步骤的详细过程

**Q: 如何禁用自动压缩？**

A: 设置阈值为 1.0（永不压缩）：
```python
config = Config(compression_threshold=1.0)
```

**Q: 工具输出被截断后如何查看完整内容？**

A: 完整输出保存在 `tool-output/` 目录：
```python
# 查看截断信息
result = truncator.truncate("tool_name", output)
print(result["full_output_path"])
# tool-output/tool_20250220_103045.json

# 读取完整输出
import json
with open(result["full_output_path"]) as f:
    full_output = json.load(f)
```

**Q: 缓存友好设计的实际效果？**

A: 根据 OpenAI 的缓存机制：
- 修改历史前缀：缓存失效，重新计算（慢）
- 只追加消息：缓存有效，增量计算（快）
- 节省时间：50-90%（取决于历史长度）

---

## 📈 性能指标

### Token 节省效果

| 对话轮次 | 无压缩 Token | 压缩后 Token | 节省比例 |
| -------- | ------------ | ------------ | -------- |
| 10 轮    | 10,000       | 10,000       | 0%       |
| 20 轮    | 20,000       | 11,000       | 45%      |
| 50 轮    | 50,000       | 10,500       | 79%      |
| 100 轮   | 100,000      | 10,500       | 89.5%    |

### 缓存命中率

| 操作类型   | 缓存命中率 | 响应时间 |
| ---------- | ---------- | -------- |
| 修改历史   | 0%         | 2-5 秒   |
| 只追加消息 | 80-95%     | 0.5-1 秒 |

---

**最后更新**: 2026-02-21


