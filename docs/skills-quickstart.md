# Skills 快速开始

> 3 分钟上手 Skills 知识外化系统

---

## 什么是 Skills？

Skills 让 Agent 按需加载领域知识，无需修改代码，节省 85% Token。

---

## 快速开始

### 1. 创建技能目录

```bash
mkdir skills
```

### 2. 创建技能文件

创建 `skills/pdf/SKILL.md`：

```markdown
---
name: pdf
description: Process PDF files. Use when reading, creating, or merging PDFs.
---

# PDF Processing Skill

## Reading PDFs
Use pdftotext: `pdftotext input.pdf -`

## Creating PDFs
Use pandoc: `pandoc input.md -o output.pdf`

$ARGUMENTS
```

### 3. 使用 Agent

```python
from recon_core import ReActAgent, HelloAgentsLLM
from recon_core.tools import ToolRegistry

# 创建 Agent（自动检测 skills/ 目录）
agent = ReActAgent(
    name="assistant",
    llm=HelloAgentsLLM(provider="openai", model="gpt-4"),
    tool_registry=ToolRegistry()
)

# Agent 会自动加载 pdf 技能
result = agent.run("帮我提取 report.pdf 的文本内容")
```

**完成！** 🎉

---

## 核心优势

- ✅ **零配置**：创建 `skills/` 目录即可
- ✅ **按需加载**：节省 85% Token
- ✅ **人类可编辑**：纯文本 Markdown
- ✅ **团队协作**：Git 友好

---

## 目录结构

```
your-project/
├── skills/              # ← 创建这个目录
│   ├── pdf/
│   │   └── SKILL.md    # ← 技能定义
│   ├── code-review/
│   │   └── SKILL.md
│   └── mcp-builder/
│       └── SKILL.md
└── main.py
```

---

## SKILL.md 格式

```markdown
---
name: 技能名称
description: 简短描述（< 100 字符）
---

# 技能标题

详细内容...

$ARGUMENTS
```

**必需字段**：
- `name`：技能名称
- `description`：简短描述

---

## 配置选项

```python
from recon_core import Config

config = Config(
    skills_enabled=True,           # 是否启用（默认 True）
    skills_dir="skills",           # 技能目录（默认 "skills"）
    skills_auto_register=True      # 自动注册（默认 True）
)
```

---

## 检查激活状态

```bash
python examples/check_skills_activation.py
```

---

## 更多信息

查看完整文档：[Skills 使用指南](./skills-usage-guide.md)

