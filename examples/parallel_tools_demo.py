"""工具并行执行性能对比示例

对比同步执行 vs 异步并行执行的性能差异
"""

import asyncio
import time
from recon_core.core.llm import HelloAgentsLLM
from recon_core.core.config import Config
from recon_core.agents.react_agent import ReActAgent
from recon_core.tools.registry import ToolRegistry
from recon_core.tools.base import Tool, ToolParameter
from recon_core.tools.response import ToolResponse


# ==================== 模拟耗时工具 ====================

class SlowTool(Tool):
    """模拟耗时工具"""
    
    def __init__(self, name: str, delay: float):
        super().__init__(name, f"耗时 {delay}s 的工具")
        self.delay = delay
    
    def run(self, parameters: dict) -> ToolResponse:
        time.sleep(self.delay)
        return ToolResponse.success(
            text=f"{self.name} 完成（耗时 {self.delay}s）",
            data={"delay": self.delay}
        )
    
    def get_parameters(self):
        return [
            ToolParameter(name="data", type="string", description="数据")
        ]


# ==================== 性能测试 ====================

async def test_parallel_performance():
    """测试并行执行性能"""
    
    print("=" * 60)
    print("工具并行执行性能测试")
    print("=" * 60)
    
    # 创建 3 个耗时 1 秒的工具
    registry = ToolRegistry()
    registry.register_tool(SlowTool("Tool1", 1.0))
    registry.register_tool(SlowTool("Tool2", 1.0))
    registry.register_tool(SlowTool("Tool3", 1.0))
    
    # 配置
    config = Config(
        max_concurrent_tools=3,  # 允许 3 个工具并行
        trace_enabled=False
    )
    
    # 创建 Agent
    llm = HelloAgentsLLM(...)  # 需要配置真实 LLM
    agent = ReActAgent(
        name="ParallelAgent",
        llm=llm,
        tool_registry=registry,
        config=config
    )
    
    # 测试异步并行执行
    print("\n🚀 测试异步并行执行（3个工具同时运行）")
    start_time = time.time()
    
    # 假设 Agent 会调用这 3 个工具
    # 实际使用中，LLM 会决定调用哪些工具
    result = await agent.arun("请同时调用 Tool1, Tool2, Tool3")
    
    elapsed = time.time() - start_time
    
    print(f"\n⏱️  异步并行执行耗时: {elapsed:.2f}s")
    print(f"   理论最优: ~1.0s（3个工具并行）")
    print(f"   同步执行: ~3.0s（3个工具串行）")
    print(f"   性能提升: {3.0 / elapsed:.2f}x")


# ==================== 并发控制测试 ====================

async def test_concurrency_limit():
    """测试并发数限制"""
    
    print("\n" + "=" * 60)
    print("并发数限制测试")
    print("=" * 60)
    
    # 创建 5 个耗时 1 秒的工具
    registry = ToolRegistry()
    for i in range(1, 6):
        registry.register_tool(SlowTool(f"Tool{i}", 1.0))
    
    # 配置：最多并行 2 个工具
    config = Config(
        max_concurrent_tools=2,  # 限制并发数为 2
        trace_enabled=False
    )
    
    llm = HelloAgentsLLM(...)
    agent = ReActAgent(
        name="LimitedAgent",
        llm=llm,
        tool_registry=registry,
        config=config
    )
    
    print("\n🚀 测试并发限制（5个工具，最多2个并行）")
    start_time = time.time()
    
    result = await agent.arun("请调用所有 5 个工具")
    
    elapsed = time.time() - start_time
    
    print(f"\n⏱️  执行耗时: {elapsed:.2f}s")
    print(f"   理论耗时: ~3.0s（5个工具，每次2个并行：2+2+1）")
    print(f"   无限制: ~1.0s（5个工具全部并行）")
    print(f"   串行执行: ~5.0s（5个工具串行）")


if __name__ == "__main__":
    # 运行性能测试
    asyncio.run(test_parallel_performance())
    
    # 运行并发限制测试
    asyncio.run(test_concurrency_limit())

