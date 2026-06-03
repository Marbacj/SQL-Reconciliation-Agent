# HelloAgents 自定义工具开发指南

> 本指南帮助你快速创建和注册自己的自定义工具，与框架内置工具无缝集成

---

## 📚 目录

- [快速开始](#快速开始)
- [三种实现方式](#三种实现方式)
- [工具模板](#工具模板)
- [实战示例](#实战示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 🚀 快速开始

### 安装框架

```bash
pip install hello-agents
```

### 最简单的自定义工具

```python
from recon_core.tools import Tool, ToolParameter, ToolResponse
from recon_core.tools.errors import ToolErrorCode

class MyFirstTool(Tool):
    """我的第一个自定义工具"""
    
    def __init__(self):
        super().__init__(
            name="my_first_tool",
            description="这是我的第一个自定义工具，用于演示基本用法"
        )
    
    def run(self, parameters):
        """执行工具逻辑"""
        user_input = parameters.get("input", "")
        
        if not user_input:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="参数 'input' 不能为空"
            )
        
        # 实现你的工具逻辑
        result = f"处理结果: {user_input.upper()}"
        
        return ToolResponse.success(
            text=result,
            data={"original": user_input, "processed": user_input.upper()}
        )
    
    def get_parameters(self):
        """定义工具参数"""
        return [
            ToolParameter(
                name="input",
                type="string",
                description="要处理的输入文本",
                required=True
            )
        ]
```

### 注册和使用

```python
from recon_core import ToolRegistry, ReActAgent, HelloAgentsLLM

# 1. 创建工具注册表
registry = ToolRegistry()

# 2. 注册自定义工具（与内置工具完全一致）
registry.register_tool(MyFirstTool())

# 3. 创建 Agent
llm = HelloAgentsLLM()
agent = ReActAgent("assistant", llm, tool_registry=registry)

# 4. 使用工具
response = agent.run("使用 my_first_tool 处理文本 'hello world'")
print(response)
```

---

## 🎯 三种实现方式

HelloAgents 提供三种渐进式的工具实现方式，适应不同复杂度的需求：

### 方式 1：函数式工具（最简单）

适合简单的一次性工具，无需继承 Tool 类。

```python
from recon_core import ToolRegistry

def simple_calculator(a: int, b: int, operation: str = "add") -> str:
    """简单计算器
    
    Args:
        a: 第一个数字
        b: 第二个数字
        operation: 运算类型 (add/sub/mul/div)
    """
    if operation == "add":
        result = a + b
    elif operation == "sub":
        result = a - b
    elif operation == "mul":
        result = a * b
    elif operation == "div":
        result = a / b if b != 0 else "错误：除数不能为零"
    else:
        return "错误：不支持的运算"
    
    return f"计算结果: {result}"

# 注册函数式工具
registry = ToolRegistry()
registry.register_function(
    func=simple_calculator,
    name="simple_calc",
    description="执行简单的数学运算"
)
```

### 方式 2：标准工具类（推荐）

继承 `Tool` 基类，实现完整的工具功能。

```python
from recon_core.tools import Tool, ToolParameter, ToolResponse
from recon_core.tools.errors import ToolErrorCode

class WeatherTool(Tool):
    """天气查询工具"""
    
    def __init__(self, api_key: str):
        super().__init__(
            name="weather",
            description="查询指定城市的天气信息"
        )
        self.api_key = api_key
    
    def run(self, parameters):
        city = parameters.get("city")
        
        # 调用天气 API（示例）
        weather_data = self._fetch_weather(city)
        
        if weather_data is None:
            return ToolResponse.error(
                code=ToolErrorCode.NOT_FOUND,
                message=f"未找到城市 '{city}' 的天气信息"
            )
        
        return ToolResponse.success(
            text=f"{city} 的天气: {weather_data['description']}, 温度: {weather_data['temp']}°C",
            data=weather_data,
            stats={"api_calls": 1}
        )
    
    def get_parameters(self):
        return [
            ToolParameter(
                name="city",
                type="string",
                description="要查询的城市名称",
                required=True
            )
        ]
    
    def _fetch_weather(self, city):
        """调用天气 API（示例实现）"""
        # 实际实现中调用真实的天气 API
        return {
            "city": city,
            "description": "晴天",
            "temp": 25,
            "humidity": 60
        }
```

### 方式 3：可展开工具（高级）

使用 `@tool_action` 装饰器，将一个工具展开为多个子工具。

```python
from recon_core.tools import Tool, tool_action, ToolResponse

class DatabaseTool(Tool):
    """数据库操作工具（可展开）"""
    
    def __init__(self, connection_string: str):
        super().__init__(
            name="database",
            description="数据库操作工具集",
            expandable=True  # 标记为可展开
        )
        self.connection_string = connection_string
    
    @tool_action("db_query", "执行数据库查询")
    def query(self, sql: str, limit: int = 100) -> ToolResponse:
        """执行 SQL 查询
        
        Args:
            sql: SQL 查询语句
            limit: 返回结果的最大行数
        """
        # 执行查询逻辑
        results = self._execute_query(sql, limit)
        
        return ToolResponse.success(
            text=f"查询成功，返回 {len(results)} 行",
            data={"results": results, "row_count": len(results)}
        )
    
    @tool_action("db_insert", "插入数据")
    def insert(self, table: str, data: dict) -> ToolResponse:
        """插入数据到表
        
        Args:
            table: 表名
            data: 要插入的数据（字典格式）
        """
        # 执行插入逻辑
        row_id = self._execute_insert(table, data)
        
        return ToolResponse.success(
            text=f"数据插入成功，ID: {row_id}",
            data={"inserted_id": row_id}
        )
    
    def run(self, parameters):
        """普通模式下的执行方法（可选）"""
        return ToolResponse.error(
            code="NOT_IMPLEMENTED",
            message="请使用展开后的子工具（db_query, db_insert）"
        )
    
    def get_parameters(self):
        return []
    
    def _execute_query(self, sql, limit):
        # 实际数据库查询实现
        return []
    
    def _execute_insert(self, table, data):
        # 实际数据库插入实现
        return 1
```

注册可展开工具：

```python
registry = ToolRegistry()

# 注册工具（自动展开为 db_query 和 db_insert）
db_tool = DatabaseTool(connection_string="sqlite:///mydb.db")
registry.register_tool(db_tool)

# 框架会自动注册两个子工具：
# - database_query
# - database_insert
```

---

## 📝 工具模板

我们提供了三个开箱即用的模板，位于 `examples/custom_tools/` 目录：

1. **simple_tool_template.py** - 简单工具模板（最小实现）
2. **advanced_tool_template.py** - 高级工具模板（完整特性）
3. **expandable_tool_template.py** - 可展开工具模板（多功能）

---

## 🎓 实战示例

框架提供了 4 个真实场景的示例工具，位于 `examples/custom_tools/` 目录：

### 1. weather_tool.py - 天气查询工具
演示如何调用外部 API 并处理响应。

### 2. database_tool.py - 数据库查询工具
演示如何管理外部资源连接和错误处理。

### 3. code_formatter_tool.py - 代码格式化工具
演示复杂的文本处理逻辑和参数验证。

### 4. multi_function_tool.py - 多功能工具
演示可展开工具的完整实现。

---

## ✅ 最佳实践

### 1. 错误处理

始终使用标准错误码，提供清晰的错误信息：

```python
from recon_core.tools.errors import ToolErrorCode

# ✅ 好的做法
return ToolResponse.error(
    code=ToolErrorCode.INVALID_PARAM,
    message="参数 'city' 不能为空",
    context={"provided_params": parameters}
)

# ❌ 不好的做法
return ToolResponse.error(
    code="ERROR",
    message="出错了"
)
```

### 2. 参数验证

在 `run()` 方法开始时验证所有必需参数：

```python
def run(self, parameters):
    # 验证必需参数
    required = ["city", "date"]
    for param in required:
        if param not in parameters or not parameters[param]:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message=f"缺少必需参数: {param}"
            )

    # 继续执行工具逻辑
    ...
```

### 3. 结构化数据

返回结构化的 `data` 字段，方便后续处理：

```python
return ToolResponse.success(
    text="查询成功，找到 3 条记录",
    data={
        "records": [...],
        "count": 3,
        "query_time_ms": 45
    },
    stats={
        "time_ms": 50,
        "api_calls": 1
    }
)
```

### 4. 添加日志

使用框架的日志系统记录关键操作：

```python
import logging

logger = logging.getLogger(__name__)

def run(self, parameters):
    logger.info(f"执行工具 {self.name}，参数: {parameters}")

    try:
        result = self._do_work(parameters)
        logger.info(f"工具执行成功")
        return ToolResponse.success(text=result)
    except Exception as e:
        logger.error(f"工具执行失败: {e}")
        return ToolResponse.error(
            code=ToolErrorCode.EXECUTION_ERROR,
            message=str(e)
        )
```

### 5. 使用 run_with_timing()

让框架自动添加时间统计：

```python
# 在 Agent 中使用
response = tool.run_with_timing(parameters)
# 自动添加 stats["time_ms"] 和 context["params_input"]
```

### 6. 异步支持

如果工具涉及 I/O 操作，考虑实现异步版本：

```python
async def arun(self, parameters):
    """异步执行工具"""
    # 使用 aiohttp, asyncpg 等异步库
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()

    return ToolResponse.success(text="...", data=data)
```

### 7. 资源管理

使用上下文管理器管理资源：

```python
class DatabaseTool(Tool):
    def __init__(self, connection_string):
        super().__init__(name="db", description="...")
        self.connection_string = connection_string
        self._connection = None

    def __enter__(self):
        self._connection = self._create_connection()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._connection:
            self._connection.close()
```

### 8. 文档字符串

为工具和参数提供清晰的文档：

```python
class MyTool(Tool):
    """我的自定义工具

    这个工具用于...

    使用示例:
        >>> tool = MyTool()
        >>> response = tool.run({"input": "test"})

    注意事项:
        - 参数 'input' 不能为空
        - 需要配置 API_KEY 环境变量
    """
```

---

## ❓ 常见问题

### Q1: 如何在工具中访问 Agent 的上下文？

工具应该是无状态的，不应该直接访问 Agent。如果需要上下文信息，通过参数传递：

```python
# ❌ 不推荐
class MyTool(Tool):
    def __init__(self, agent):
        self.agent = agent  # 不要这样做

# ✅ 推荐
class MyTool(Tool):
    def run(self, parameters):
        context = parameters.get("context", {})
        # 使用传入的上下文
```

### Q2: 如何处理长时间运行的任务？

使用异步执行或返回 PARTIAL 状态：

```python
def run(self, parameters):
    # 启动长时间任务
    task_id = self._start_background_task(parameters)

    return ToolResponse.partial(
        text=f"任务已启动，ID: {task_id}",
        data={"task_id": task_id, "status": "running"}
    )
```

### Q3: 如何在工具之间共享数据？

使用 ToolRegistry 的共享存储：

```python
# 工具 A 保存数据
registry.set_shared_data("key", value)

# 工具 B 读取数据
value = registry.get_shared_data("key")
```

### Q4: 如何测试自定义工具？

编写单元测试：

```python
import pytest
from my_tools import MyCustomTool

def test_my_tool_success():
    tool = MyCustomTool()
    response = tool.run({"input": "test"})

    assert response.status == "success"
    assert "test" in response.text
    assert response.data["processed"] == "TEST"

def test_my_tool_error():
    tool = MyCustomTool()
    response = tool.run({})  # 缺少参数

    assert response.status == "error"
    assert response.error_info["code"] == "INVALID_PARAM"
```

### Q5: 如何调试工具执行？

启用详细日志：

```python
import logging

logging.basicConfig(level=logging.DEBUG)

# 或者只启用工具日志
logging.getLogger("recon_core.tools").setLevel(logging.DEBUG)
```

### Q6: 工具可以调用其他工具吗？

可以，但需要通过 ToolRegistry：

```python
class ComposeTool(Tool):
    def __init__(self, registry):
        super().__init__(name="compose", description="...")
        self.registry = registry

    def run(self, parameters):
        # 调用其他工具
        response1 = self.registry.execute_tool("tool_a", {"input": "..."})
        response2 = self.registry.execute_tool("tool_b", {"data": response1.data})

        return ToolResponse.success(
            text="组合执行完成",
            data={"result": response2.data}
        )
```

---

## 📚 相关文档

- [工具响应协议](./tool-response-protocol.md) - ToolResponse 详细说明
- [文件操作工具](./file_tools.md) - 内置文件工具示例
- [Skills 知识外化](./skills-usage-guide.md) - Skills 系统集成

---

## 🤝 贡献你的工具

如果你开发了通用的工具，欢迎贡献到 HelloAgents 框架：

1. Fork 项目仓库
2. 在 `recon_core/tools/builtin/` 添加你的工具
3. 编写测试和文档
4. 提交 Pull Request

---

## 📞 获取帮助

- GitHub Issues: https://github.com/your-repo/hello-agents/issues
- 文档: https://hello-agents.readthedocs.io
- 社区讨论: https://github.com/your-repo/hello-agents/discussions


