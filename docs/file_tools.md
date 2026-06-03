# HelloAgents 文件操作工具使用指南

> 提供标准的文件读写编辑能力，内置乐观锁机制，确保多进程/多 Agent 协作时的数据安全

---

## 📚 目录

- [快速开始](#快速开始)
- [工具介绍](#工具介绍)
- [乐观锁机制](#乐观锁机制)
- [使用示例](#使用示例)
- [API 参考](#api-参考)
- [最佳实践](#最佳实践)

---

## 快速开始

### 安装

文件工具已内置在 HelloAgents 框架中，无需额外安装。

### 基本使用

```python
from recon_core import ToolRegistry, ReActAgent, HelloAgentsLLM
from recon_core.tools.builtin import ReadTool, WriteTool, EditTool

# 1. 创建工具注册表
registry = ToolRegistry()

# 2. 注册文件工具
registry.register_tool(ReadTool(project_root="./"))
registry.register_tool(WriteTool(project_root="./"))
registry.register_tool(EditTool(project_root="./"))

# 3. 创建 Agent
llm = HelloAgentsLLM()
agent = ReActAgent("assistant", llm, tool_registry=registry)

# 4. Agent 自动使用文件工具
result = agent.run("读取 config.py，然后修改 API_KEY 为 'new_key_123'")
```

---

## 工具介绍

HelloAgents 提供 4 个专业的文件操作工具：

### 1. ReadTool - 文件读取

**功能**：
- 读取文件内容
- 支持行号范围（offset/limit）
- 自动获取文件元数据（mtime, size）
- 缓存元数据到 ToolRegistry（用于乐观锁）

**参数**：
- `path` (必需): 文件路径（相对于 project_root）
- `offset` (可选): 起始行号，默认 0
- `limit` (可选): 最大行数，默认 2000

**返回**：
```json
{
  "status": "success",
  "data": {
    "content": "文件内容...",
    "lines": 100,
    "total_lines": 150,
    "file_mtime_ms": 1738245123456,
    "file_size_bytes": 4217
  },
  "text": "读取 100 行（共 150 行，4217 字节）"
}
```

### 2. WriteTool - 文件写入

**功能**：
- 创建或覆盖文件
- 乐观锁冲突检测（如果文件已存在）
- 原子写入（临时文件 + rename）
- 自动备份原文件

**参数**：
- `path` (必需): 文件路径
- `content` (必需): 文件内容
- `file_mtime_ms` (可选): 缓存的 mtime（用于冲突检测）

**返回**：
```json
{
  "status": "success",
  "data": {
    "written": true,
    "size_bytes": 1024,
    "backup_path": ".backups/config.py.20250119_143022.bak"
  },
  "text": "成功写入 config.py (1024 字节)"
}
```

### 3. EditTool - 精确替换

**功能**：
- 精确替换文件内容（old_string 必须唯一匹配）
- 乐观锁冲突检测
- 自动备份原文件

**参数**：
- `path` (必需): 文件路径
- `old_string` (必需): 要替换的内容
- `new_string` (必需): 替换后的内容
- `file_mtime_ms` (可选): 缓存的 mtime

**返回**：
```json
{
  "status": "success",
  "data": {
    "modified": true,
    "changed_bytes": 10,
    "backup_path": ".backups/config.py.20250119_143022.bak"
  },
  "text": "成功编辑 config.py (变化 +10 字节)"
}
```

### 4. MultiEditTool - 批量替换

**功能**：
- 批量执行多个替换操作
- 原子性保证（要么全部成功，要么全部失败）
- 乐观锁冲突检测（所有替换前检查一次）

**参数**：
- `path` (必需): 文件路径
- `edits` (必需): 替换列表 `[{"old_string": "...", "new_string": "..."}]`
- `file_mtime_ms` (可选): 缓存的 mtime

**返回**：
```json
{
  "status": "success",
  "data": {
    "modified": true,
    "num_edits": 3,
    "changed_bytes": 25,
    "backup_path": ".backups/config.py.20250119_143022.bak"
  },
  "text": "成功执行 3 个替换操作 (变化 +25 字节)"
}
```

---

## 乐观锁机制

### 什么是乐观锁？

乐观锁是一种并发控制机制，通过检测文件是否在读取后被修改，来避免意外覆盖。

### 工作原理

```
┌─────────────────────────────────────────────────┐
│           乐观锁机制流程                      │
└─────────────────────────────────────────────────┘

1. Read("config.py")
   ├─ 读取文件内容
   ├─ 获取元数据（mtime=123456, size=4217）
   └─ 缓存到 ToolRegistry

2. [外部修改 config.py]
   └─ mtime 变为 123789

3. Edit("config.py", file_mtime_ms=123456)
   ├─ 检查当前 mtime (123789) vs 缓存 mtime (123456)
   ├─ 不一致 → 返回 CONFLICT 错误
   └─ Agent 看到冲突，重新 Read
```

### 为什么需要乐观锁？

**场景 1：外部修改**
```python
# 时间线
00:00 - Agent Read config.py
00:01 - 用户手动修改 config.py
00:02 - Agent Edit config.py
        → 没有乐观锁：静默覆盖用户修改 ❌
        → 有乐观锁：检测到冲突，拒绝修改 ✅
```

**场景 2：多 Agent 协作**
```python
# Agent A 和 Agent B 同时操作同一文件
Agent A: Read → 准备修改
Agent B: Read → Edit 成功
Agent A: Edit → 检测到冲突 ✅
```

---

## 使用示例

### 示例 1：基本文件操作

```python
from recon_core.tools.builtin import ReadTool, WriteTool, EditTool
from recon_core.tools.registry import ToolRegistry

# 创建工具
registry = ToolRegistry()
read_tool = ReadTool(project_root="./", registry=registry)
write_tool = WriteTool(project_root="./", registry=registry)
edit_tool = EditTool(project_root="./", registry=registry)

# 1. 写入文件
response = write_tool.run({
    "path": "config.py",
    "content": 'API_KEY = "test_key"\nDEBUG = False\n'
})
print(response.text)  # 成功写入 config.py (XX 字节)

# 2. 读取文件
response = read_tool.run({"path": "config.py"})
print(response.data["content"])

# 3. 编辑文件
response = edit_tool.run({
    "path": "config.py",
    "old_string": "DEBUG = False",
    "new_string": "DEBUG = True"
})
print(response.text)  # 成功编辑 config.py
```

### 示例 2：乐观锁冲突检测

```python
import time
from pathlib import Path

# 创建测试文件
test_file = Path("data.txt")
test_file.write_text("Original content")

# 1. Agent 读取文件（缓存元数据）
response = read_tool.run({"path": "data.txt"})
print(f"缓存的 mtime: {response.data['file_mtime_ms']}")

# 2. 模拟外部修改
time.sleep(0.1)
test_file.write_text("Modified by external process")

# 3. Agent 尝试编辑（使用缓存的 mtime）
cached_metadata = registry.get_read_metadata("data.txt")
response = edit_tool.run({
    "path": "data.txt",
    "old_string": "Original content",
    "new_string": "My changes",
    "file_mtime_ms": cached_metadata["file_mtime_ms"]
})

# 检测到冲突！
if response.status.value == "error":
    print(f"✅ 冲突检测成功: {response.error_info['message']}")
    # 输出: 文件自上次读取后被修改。当前 mtime=XXX, 缓存 mtime=YYY
```

### 示例 3：批量编辑

```python
from recon_core.tools.builtin import MultiEditTool

multiedit_tool = MultiEditTool(project_root="./")

response = multiedit_tool.run({
    "path": "settings.py",
    "edits": [
        {"old_string": 'API_KEY = "old"', "new_string": 'API_KEY = "new"'},
        {"old_string": "DEBUG = False", "new_string": "DEBUG = True"},
        {"old_string": "PORT = 8000", "new_string": "PORT = 9000"}
    ]
})

print(response.text)  # 成功执行 3 个替换操作
```

### 示例 4：在 Agent 中使用

```python
from recon_core import ReActAgent, HelloAgentsLLM, ToolRegistry
from recon_core.tools.builtin import ReadTool, WriteTool, EditTool

# 创建 Agent
llm = HelloAgentsLLM()
registry = ToolRegistry()

registry.register_tool(ReadTool(project_root="./", registry=registry))
registry.register_tool(WriteTool(project_root="./", registry=registry))
registry.register_tool(EditTool(project_root="./", registry=registry))

agent = ReActAgent("assistant", llm, tool_registry=registry)

# Agent 自动使用乐观锁
result = agent.run("""
请执行以下任务：
1. 读取 config.py 文件
2. 将 API_KEY 修改为 'new_key_456'
3. 将 DEBUG 修改为 True
""")

print(result)
```

---

## API 参考

### ReadTool

```python
class ReadTool(Tool):
    def __init__(
        self,
        project_root: str = ".",
        working_dir: Optional[str] = None,
        registry: Optional[ToolRegistry] = None
    )
```

**参数**：
- `project_root`: 项目根目录，默认当前目录
- `working_dir`: 工作目录，默认等于 project_root
- `registry`: ToolRegistry 实例（用于元数据缓存）

### WriteTool

```python
class WriteTool(Tool):
    def __init__(
        self,
        project_root: str = ".",
        working_dir: Optional[str] = None,
        registry: Optional[ToolRegistry] = None
    )
```

### EditTool

```python
class EditTool(Tool):
    def __init__(
        self,
        project_root: str = ".",
        working_dir: Optional[str] = None,
        registry: Optional[ToolRegistry] = None
    )
```

### MultiEditTool

```python
class MultiEditTool(Tool):
    def __init__(
        self,
        project_root: str = ".",
        working_dir: Optional[str] = None,
        registry: Optional[ToolRegistry] = None
    )
```

---

## 最佳实践

### 1. 始终传递 registry

```python
# ✅ 推荐：传递 registry，启用乐观锁
registry = ToolRegistry()
read_tool = ReadTool(project_root="./", registry=registry)
edit_tool = EditTool(project_root="./", registry=registry)

# ❌ 不推荐：不传递 registry，无法使用乐观锁
read_tool = ReadTool(project_root="./")
edit_tool = EditTool(project_root="./")
```

### 2. Read 后再 Edit

```python
# ✅ 推荐：先 Read，缓存元数据
read_tool.run({"path": "config.py"})
cached = registry.get_read_metadata("config.py")
edit_tool.run({
    "path": "config.py",
    "old_string": "old",
    "new_string": "new",
    "file_mtime_ms": cached["file_mtime_ms"]
})

# ❌ 不推荐：直接 Edit，无冲突检测
edit_tool.run({
    "path": "config.py",
    "old_string": "old",
    "new_string": "new"
})
```

### 3. 处理冲突错误

```python
response = edit_tool.run({...})

if response.status.value == "error":
    if response.error_info["code"] == "CONFLICT":
        # 冲突：重新读取文件
        read_tool.run({"path": "config.py"})
        # 然后重试编辑
    else:
        # 其他错误
        print(f"错误: {response.error_info['message']}")
```

### 4. 使用 MultiEdit 提高效率

```python
# ✅ 推荐：批量编辑（原子性）
multiedit_tool.run({
    "path": "config.py",
    "edits": [
        {"old_string": "A", "new_string": "A'"},
        {"old_string": "B", "new_string": "B'"},
        {"old_string": "C", "new_string": "C'"}
    ]
})

# ❌ 不推荐：多次单独编辑（效率低，无原子性）
edit_tool.run({"path": "config.py", "old_string": "A", "new_string": "A'"})
edit_tool.run({"path": "config.py", "old_string": "B", "new_string": "B'"})
edit_tool.run({"path": "config.py", "old_string": "C", "new_string": "C'"})
```

### 5. 备份文件管理

```python
# 备份文件自动保存在 .backups/ 目录
# 建议定期清理旧备份

import shutil
from pathlib import Path

backup_dir = Path(".backups")
if backup_dir.exists():
    # 保留最近 10 个备份
    backups = sorted(backup_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime)
    for old_backup in backups[:-10]:
        old_backup.unlink()
```

---

## 常见问题

### Q1: 为什么 Edit 返回 "old_string 必须唯一匹配" 错误？

**原因**：EditTool 要求 `old_string` 在文件中只出现一次，以确保替换的精确性。

**解决方案**：
```python
# 方案 1：使用更具体的 old_string
edit_tool.run({
    "path": "config.py",
    "old_string": 'API_KEY = "old_key"',  # 包含更多上下文
    "new_string": 'API_KEY = "new_key"'
})

# 方案 2：使用 MultiEdit 指定多个替换
multiedit_tool.run({
    "path": "config.py",
    "edits": [
        {"old_string": "第一处的内容", "new_string": "新内容1"},
        {"old_string": "第二处的内容", "new_string": "新内容2"}
    ]
})
```

### Q2: 如何禁用乐观锁？

**方法**：不传递 `file_mtime_ms` 参数即可

```python
# 不使用乐观锁
edit_tool.run({
    "path": "config.py",
    "old_string": "old",
    "new_string": "new"
    # 不传递 file_mtime_ms
})
```

### Q3: 跨平台兼容性如何？

**答**：完全兼容 Windows、Linux、macOS

- 使用 `pathlib.Path` 统一路径处理
- 使用毫秒级时间戳确保精度
- 自动处理不同文件系统的差异

---

## 相关文档

- [工具响应协议](../refine/01-tool-response-protocol.md)
- [乐观锁机制设计](../refine/08-optimistic-locking.md)
- [ToolRegistry API](../api/tools/registry.md)

---

**最后更新**：2025-01-19  
**维护者**：HelloAgents 开发团队

