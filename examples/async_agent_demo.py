"""异步 Agent 使用示例

演示如何使用 Agent 的异步生命周期功能
"""

import asyncio
from recon_core.core.llm import HelloAgentsLLM
from recon_core.core.config import Config
from recon_core.core.lifecycle import AgentEvent, EventType
from recon_core.agents.react_agent import ReActAgent
from recon_core.tools.registry import ToolRegistry
from recon_core.tools.base import Tool, ToolParameter
from recon_core.tools.response import ToolResponse
# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

# ==================== 示例工具 ====================

class SearchTool(Tool):
    """搜索工具示例"""
    
    def __init__(self):
        super().__init__("Search", "搜索互联网信息")
    
    def run(self, parameters: dict) -> ToolResponse:
        query = parameters.get("query", "")
        # 模拟搜索
        return ToolResponse.success(
            text=f"搜索结果：关于 '{query}' 的信息...",
            data={"query": query, "results": 10}
        )
    
    def get_parameters(self):
        return [
            ToolParameter(name="query", type="string", description="搜索关键词", required=True)
        ]


class CalculatorTool(Tool):
    """计算器工具示例"""
    
    def __init__(self):
        super().__init__("Calculator", "执行数学计算")
    
    def run(self, parameters: dict) -> ToolResponse:
        expression = parameters.get("expression", "")
        try:
            result = eval(expression)
            return ToolResponse.success(
                text=f"计算结果：{expression} = {result}",
                data={"expression": expression, "result": result}
            )
        except Exception as e:
            return ToolResponse.error(
                code="CALC_ERROR",
                message=f"计算失败: {str(e)}"
            )
    
    def get_parameters(self):
        return [
            ToolParameter(name="expression", type="string", description="数学表达式", required=True)
        ]


# ==================== 生命周期钩子示例 ====================

async def on_agent_start(event: AgentEvent):
    """Agent 开始执行时触发"""
    print(f"\n🚀 [{event.agent_name}] 开始执行")
    print(f"   输入: {event.data.get('input_text')}")


async def on_step_start(event: AgentEvent):
    """推理步骤开始时触发"""
    step = event.data.get('step', 0)
    print(f"\n📍 步骤 {step} 开始")


async def on_tool_call(event: AgentEvent):
    """工具调用时触发"""
    tool_name = event.data.get('tool_name')
    args = event.data.get('args', {})
    print(f"   🔧 调用工具: {tool_name}({args})")


async def on_agent_finish(event: AgentEvent):
    """Agent 执行完成时触发"""
    result = event.data.get('result')
    total_steps = event.data.get('total_steps', 0)
    total_tokens = event.data.get('total_tokens', 0)
    
    print(f"\n✅ [{event.agent_name}] 执行完成")
    print(f"   总步骤: {total_steps}")
    print(f"   总 Token: {total_tokens}")
    print(f"   结果: {result}")


async def on_error(event: AgentEvent):
    """发生错误时触发"""
    error = event.data.get('error')
    error_type = event.data.get('error_type')
    print(f"\n❌ 错误: {error_type} - {error}")


# ==================== 主函数 ====================

async def main():
    """主函数"""
    
    print("=" * 60)
    print("异步 Agent 生命周期示例")
    print("=" * 60)
    
    # 1. 初始化 LLM
    llm = HelloAgentsLLM()
    
    # 2. 创建工具注册表
    registry = ToolRegistry()
    registry.register_tool(SearchTool())
    registry.register_tool(CalculatorTool())
    
    # 3. 配置
    config = Config(
        max_concurrent_tools=3,  # 最多并行 3 个工具
        hook_timeout_seconds=5.0,  # 钩子超时 5 秒
        trace_enabled=True  # 启用可观测性
    )
    
    # 4. 创建 Agent
    agent = ReActAgent(
        name="AsyncAgent",
        llm=llm,
        tool_registry=registry,
        config=config,
        max_steps=5
    )
    
    # 5. 异步执行（带生命周期钩子）
    try:
        result = await agent.arun(
            "搜索 Python 异步编程的资料，并计算 123 + 456",
            on_start=on_agent_start,
            on_step=on_step_start,
            on_tool_call=on_tool_call,
            on_finish=on_agent_finish,
            on_error=on_error
        )
        
        print("\n" + "=" * 60)
        print("执行成功！")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n执行失败: {e}")


# ==================== 流式执行示例 ====================

async def stream_example():
    """流式执行示例（未来功能）"""
    
    print("\n" + "=" * 60)
    print("流式执行示例")
    print("=" * 60)
    
    # 初始化（同上）
    llm = HelloAgentsLLM(...)
    agent = ReActAgent(...)
    
    # 流式执行
    async for event in agent.arun_stream("你的问题"):
        if event.type == EventType.AGENT_START:
            print(f"🚀 开始: {event.data}")
        elif event.type == EventType.TOOL_CALL:
            print(f"🔧 工具: {event.data['tool_name']}")
        elif event.type == EventType.AGENT_FINISH:
            print(f"✅ 完成: {event.data['result']}")


if __name__ == "__main__":
    # 运行异步主函数
    asyncio.run(main())
    
    # 或运行流式示例
    # asyncio.run(stream_example())

