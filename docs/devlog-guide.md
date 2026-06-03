# 开发日志系统指南（DevLog System）

## 📖 概述

**DevLogTool** 是 HelloAgents 框架的结构化开发日志工具，用于记录 Agent 的开发决策、问题、解决方案等关键信息。

### 核心特性

- ✅ **结构化日志**：category + content + metadata
- ✅ **7 种类别**：decision、progress、issue、solution、refactor、test、performance
- ✅ **持久化存储**：保存到 `memory/devlogs/`
- ✅ **过滤查询**：按类别、标签查询
- ✅ **自动摘要**：生成日志摘要

---

## 🚀 快速开始

### 1. 自动集成（零配置）

```python
from recon_core import ReActAgent, HelloAgentsLLM, Config

# DevLogTool 默认启用
config = Config(devlog_enabled=True)
agent = ReActAgent("assistant", HelloAgentsLLM(), config=config)

# Agent 可以直接使用 DevLog 工具
agent.run("记录开发决策：使用 Redis 作为缓存")
```

### 2. 手动使用

```python
from recon_core.tools.builtin import DevLogTool

tool = DevLogTool(persistence_dir="memory/devlogs")

# 记录决策
response = tool.run({
    "category": "decision",
    "content": "选择 Redis 作为缓存方案",
    "metadata": {
        "reason": "高性能、支持持久化",
        "alternatives": ["Memcached", "本地缓存"]
    }
})

# 记录问题
response = tool.run({
    "category": "issue",
    "content": "数据库连接池耗尽",
    "metadata": {
        "severity": "high",
        "impact": "API 响应超时"
    }
})

# 记录解决方案
response = tool.run({
    "category": "solution",
    "content": "增加连接池大小到 50",
    "metadata": {
        "issue_id": "db-pool-exhausted",
        "result": "问题解决"
    }
})
```

---

## 💡 核心概念

### 7 种日志类别

| 类别          | 用途     | 示例                 |
| ------------- | -------- | -------------------- |
| `decision`    | 技术决策 | 选择数据库、架构设计 |
| `progress`    | 进度更新 | 完成模块、里程碑     |
| `issue`       | 问题记录 | Bug、性能问题、错误  |
| `solution`    | 解决方案 | 问题修复、优化方案   |
| `refactor`    | 重构记录 | 代码重构、架构调整   |
| `test`        | 测试记录 | 测试结果、覆盖率     |
| `performance` | 性能分析 | 性能瓶颈、优化效果   |

### 日志结构

```json
{
  "id": "devlog-20250220-103045",
  "timestamp": "2026-02-21T10:30:45Z",
  "category": "decision",
  "content": "选择 Redis 作为缓存方案",
  "metadata": {
    "reason": "高性能、支持持久化",
    "alternatives": ["Memcached", "本地缓存"],
    "tags": ["cache", "redis"]
  }
}
```

---

## 📝 使用指南

### 1. 记录不同类型的日志

**决策日志：**
```python
tool.run({
    "category": "decision",
    "content": "使用 PostgreSQL 作为主数据库",
    "metadata": {
        "reason": "支持 JSONB、事务完整性",
        "alternatives": ["MySQL", "MongoDB"],
        "tags": ["database", "architecture"]
    }
})
```

**进度日志：**
```python
tool.run({
    "category": "progress",
    "content": "完成用户认证模块",
    "metadata": {
        "milestone": "v1.0",
        "completion": "80%",
        "tags": ["auth", "milestone"]
    }
})
```

**问题日志：**
```python
tool.run({
    "category": "issue",
    "content": "内存泄漏导致服务崩溃",
    "metadata": {
        "severity": "critical",
        "impact": "服务不可用",
        "tags": ["memory", "bug"]
    }
})
```

**解决方案日志：**
```python
tool.run({
    "category": "solution",
    "content": "修复内存泄漏：关闭未使用的连接",
    "metadata": {
        "issue_id": "memory-leak-001",
        "result": "内存使用降低 60%",
        "tags": ["memory", "fix"]
    }
})
```

**重构日志：**
```python
tool.run({
    "category": "refactor",
    "content": "重构工具注册机制",
    "metadata": {
        "reason": "提高可扩展性",
        "impact": "代码减少 30%",
        "tags": ["refactor", "tools"]
    }
})
```

**测试日志：**
```python
tool.run({
    "category": "test",
    "content": "单元测试覆盖率达到 85%",
    "metadata": {
        "passed": 120,
        "failed": 5,
        "coverage": "85%",
        "tags": ["test", "coverage"]
    }
})
```

**性能日志：**
```python
tool.run({
    "category": "performance",
    "content": "API 响应时间优化",
    "metadata": {
        "before": "500ms",
        "after": "150ms",
        "improvement": "70%",
        "tags": ["performance", "api"]
    }
})
```

### 2. 查询日志

```python
# 查询所有日志
response = tool.run({"action": "list"})

# 按类别查询
response = tool.run({
    "action": "list",
    "category": "issue"
})

# 按标签查询
response = tool.run({
    "action": "list",
    "tags": ["memory", "bug"]
})

# 生成摘要
response = tool.run({"action": "summary"})
```

### 3. 清空日志

```python
# 清空所有日志
response = tool.run({"action": "clear"})
```

---

## 📊 实际案例

### 案例 1：问题追踪

**场景：** 记录和解决性能问题

```python
# 1. 记录问题
tool.run({
    "category": "issue",
    "content": "数据库查询慢，响应时间 > 2s",
    "metadata": {
        "severity": "high",
        "query": "SELECT * FROM users WHERE ...",
        "tags": ["performance", "database"]
    }
})

# 2. 记录分析
tool.run({
    "category": "performance",
    "content": "缺少索引导致全表扫描",
    "metadata": {
        "table": "users",
        "missing_index": "email",
        "tags": ["performance", "database"]
    }
})

# 3. 记录解决方案
tool.run({
    "category": "solution",
    "content": "添加 email 字段索引",
    "metadata": {
        "before": "2.3s",
        "after": "0.05s",
        "improvement": "97.8%",
        "tags": ["performance", "database"]
    }
})
```

### 案例 2：架构演进

**场景：** 记录架构决策和重构

```python
# 1. 记录决策
tool.run({
    "category": "decision",
    "content": "引入微服务架构",
    "metadata": {
        "reason": "提高可扩展性和独立部署能力",
        "services": ["auth", "order", "payment"],
        "tags": ["architecture", "microservices"]
    }
})

# 2. 记录重构
tool.run({
    "category": "refactor",
    "content": "拆分单体应用为 3 个微服务",
    "metadata": {
        "duration": "2 weeks",
        "impact": "部署时间减少 80%",
        "tags": ["architecture", "refactor"]
    }
})

# 3. 记录进度
tool.run({
    "category": "progress",
    "content": "微服务迁移完成 100%",
    "metadata": {
        "milestone": "v2.0",
        "services_migrated": 3,
        "tags": ["architecture", "milestone"]
    }
})
```

### 案例 3：测试驱动开发

**场景：** 记录测试和质量改进

```python
# 1. 记录测试
tool.run({
    "category": "test",
    "content": "添加集成测试",
    "metadata": {
        "tests_added": 25,
        "coverage_increase": "15%",
        "tags": ["test", "integration"]
    }
})

# 2. 记录问题
tool.run({
    "category": "issue",
    "content": "发现边界条件 Bug",
    "metadata": {
        "test": "test_user_registration",
        "condition": "email 为空",
        "tags": ["test", "bug"]
    }
})

# 3. 记录修复
tool.run({
    "category": "solution",
    "content": "添加 email 验证",
    "metadata": {
        "validation": "非空 + 格式检查",
        "tests_passed": "100%",
        "tags": ["test", "fix"]
    }
})
```

---

## 🎯 最佳实践

### 1. 使用标签组织日志

```python
# ✅ 好：使用标签便于查询
tool.run({
    "category": "issue",
    "content": "内存泄漏",
    "metadata": {
        "tags": ["memory", "bug", "critical"]
    }
})

# 查询时可以按标签过滤
tool.run({
    "action": "list",
    "tags": ["critical"]
})
```

### 2. 记录关键元数据

```python
# ✅ 好：记录详细元数据
tool.run({
    "category": "performance",
    "content": "API 优化",
    "metadata": {
        "endpoint": "/api/users",
        "before": "500ms",
        "after": "150ms",
        "method": "添加缓存",
        "tags": ["performance", "api"]
    }
})
```

### 3. 关联相关日志

```python
# 记录问题时生成 ID
issue_response = tool.run({
    "category": "issue",
    "content": "数据库连接池耗尽",
    "metadata": {"issue_id": "db-pool-001"}
})

# 解决方案引用问题 ID
tool.run({
    "category": "solution",
    "content": "增加连接池大小",
    "metadata": {
        "issue_id": "db-pool-001",
        "result": "问题解决"
    }
})
```

---

## 🔧 高级用法

### 1. 自定义持久化目录

```python
tool = DevLogTool(persistence_dir="custom/logs")
```

### 2. 批量查询

```python
# 查询所有问题和解决方案
response = tool.run({
    "action": "list",
    "category": ["issue", "solution"]
})
```

### 3. 生成项目摘要

```python
# 生成完整摘要
response = tool.run({"action": "summary"})

# 摘要包含：
# - 总日志数
# - 各类别统计
# - 关键决策
# - 未解决问题
```

---

## 🔗 相关文档

- [日志系统](./logging-system-guide.md) - 四种日志范式对比
- [可观测性](./observability-guide.md) - TraceLogger 使用
- [TodoWrite](./todowrite-usage-guide.md) - 任务进度管理

---

## ❓ 常见问题

**Q: DevLogTool 和 TraceLogger 的区别？**

A:
- **DevLogTool**: 记录开发决策、问题、解决方案（结构化）
- **TraceLogger**: 记录执行轨迹、工具调用、LLM 请求（审计）

**Q: 如何禁用 DevLogTool？**

A: 设置 `devlog_enabled=False`：
```python
config = Config(devlog_enabled=False)
```

**Q: 日志文件在哪里？**

A: 默认保存在 `memory/devlogs/` 目录：
```
memory/devlogs/
├── devlog-20250220-103045.json
├── devlog-20250220-143022.json
└── devlog-20250220-183033.json
```

**Q: 如何导出日志？**

A: 日志以 JSON 格式保存，可以直接读取：
```python
import json

with open("memory/devlogs/devlog-xxx.json") as f:
    logs = json.load(f)

# 导出为 CSV
import csv
with open("logs.csv", "w") as f:
    writer = csv.DictWriter(f, fieldnames=["timestamp", "category", "content"])
    writer.writeheader()
    for log in logs:
        writer.writerow(log)
```

---

## 📈 使用统计

### 日志类别分布（典型项目）

| 类别          | 占比 | 示例数量 |
| ------------- | ---- | -------- |
| `progress`    | 30%  | 45       |
| `decision`    | 20%  | 30       |
| `issue`       | 15%  | 22       |
| `solution`    | 15%  | 22       |
| `refactor`    | 10%  | 15       |
| `test`        | 5%   | 8        |
| `performance` | 5%   | 8        |

### 价值体现

| 场景     | 价值                       |
| -------- | -------------------------- |
| 问题复盘 | 快速定位问题和解决方案     |
| 知识传承 | 记录技术决策和架构演进     |
| 团队协作 | 共享开发日志，避免重复工作 |
| 项目总结 | 自动生成项目报告和里程碑   |

---

**最后更新**: 2026-02-21


