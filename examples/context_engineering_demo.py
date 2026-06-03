"""上下文工程使用示例

演示如何使用 HistoryManager、ObservationTruncator 和智能摘要：
- 历史消息管理和压缩
- 简单摘要 vs 智能摘要
- Token 计数器（缓存 + 增量计算）
- 工具输出截断
- 会话序列化和反序列化
"""

from recon_core.context.history import HistoryManager
from recon_core.context.truncator import ObservationTruncator
from recon_core.context.token_counter import TokenCounter
from recon_core.core.message import Message
from recon_core import SimpleAgent, HelloAgentsLLM, Config
from pathlib import Path
import tempfile

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()
# 加载环境变量
from dotenv import load_dotenv
load_dotenv()


def demo_token_counter():
    """演示 Token 计数器"""
    print("=" * 60)
    print("示例 1: Token 计数器（缓存 + 增量计算）")
    print("=" * 60)

    # 创建 Token 计数器
    counter = TokenCounter(model="gpt-4")

    # 计算单条消息
    msg1 = Message("Hello, world!", "user")
    tokens1 = counter.count_message(msg1)
    print(f"\n消息 1 Token 数: {tokens1}")

    # 再次计算（使用缓存）
    tokens1_cached = counter.count_message(msg1)
    print(f"消息 1 Token 数（缓存）: {tokens1_cached}")

    # 计算消息列表
    messages = [
        Message("First message", "user"),
        Message("Second message", "assistant"),
        Message("Third message", "user"),
    ]
    total_tokens = counter.count_messages(messages)
    print(f"\n消息列表总 Token 数: {total_tokens}")

    # 缓存统计
    stats = counter.get_cache_stats()
    print(f"\n缓存统计: {stats}")

    print("\n✅ Token 计数器测试完成")


def demo_simple_summary():
    """演示简单摘要（默认）"""
    print("\n" + "=" * 60)
    print("示例 2: 简单摘要（默认，无需额外 API）")
    print("=" * 60)

    # 创建 Agent（默认：简单摘要）
    config = Config(
        enable_smart_compression=False,  # 默认
        min_retain_rounds=3,
        context_window=8000
    )

    llm = HelloAgentsLLM()
    agent = SimpleAgent("简单摘要助手", llm, config=config)

    # 添加多轮对话
    print("\n添加对话历史...")
    for i in range(5):
        agent.add_message(Message(f"用户问题 {i+1}", "user"))
        agent.add_message(Message(f"助手回答 {i+1}", "assistant"))

    print(f"总消息数: {len(agent.get_history())}")
    print(f"Token 计数: {agent._history_token_count}")

    # 生成简单摘要
    history = agent.history_manager.get_history()
    summary = agent._generate_simple_summary(history)

    print(f"\n简单摘要:\n{summary}")
    print("\n✅ 简单摘要测试完成")


def demo_smart_summary():
    """演示智能摘要（可选）"""
    print("\n" + "=" * 60)
    print("示例 3: 智能摘要（可选，需额外 API）")
    print("=" * 60)

    # 创建 Agent（启用智能摘要）
    config = Config(
        enable_smart_compression=True,  # 启用智能摘要
        summary_llm_provider="deepseek",
        summary_llm_model="deepseek-chat",
        summary_max_tokens=800,
        summary_temperature=0.3,
        min_retain_rounds=3,
        context_window=8000
    )

    llm = HelloAgentsLLM()
    agent = SimpleAgent("智能摘要助手", llm, config=config)

    # 添加多轮对话（更复杂的任务）
    print("\n添加对话历史...")
    messages = [
        Message("帮我分析这个项目的架构", "user"),
        Message("好的，我会分析项目架构", "assistant"),
        Message("发现了什么问题？", "user"),
        Message("发现了一些架构问题，需要重构", "assistant"),
        Message("继续分析", "user"),
        Message("正在深入分析中", "assistant"),
    ]

    for msg in messages:
        agent.add_message(msg)

    print(f"总消息数: {len(agent.get_history())}")
    print(f"Token 计数: {agent._history_token_count}")

    # 生成智能摘要
    print("\n生成智能摘要（调用 LLM）...")
    history = agent.history_manager.get_history()
    summary = agent._generate_smart_summary(history)

    print(f"\n智能摘要:\n{summary}")
    print("\n✅ 智能摘要测试完成")


def demo_history_management():
    """演示历史管理"""
    print("\n" + "=" * 60)
    print("示例 4: 历史消息管理")
    print("=" * 60)

    # 创建历史管理器
    manager = HistoryManager(min_retain_rounds=3)

    # 模拟多轮对话
    print("\n添加对话历史...")
    for i in range(5):
        manager.append(Message(f"用户问题 {i+1}", "user"))
        manager.append(Message(f"助手回答 {i+1}", "assistant"))

    print(f"总消息数: {len(manager.get_history())}")
    print(f"完整轮次数: {manager.estimate_rounds()}")

    # 压缩历史
    print("\n执行历史压缩...")
    manager.compress("前面讨论了一些基础问题")

    compressed_history = manager.get_history()
    print(f"压缩后消息数: {len(compressed_history)}")
    print(f"第一条消息角色: {compressed_history[0].role}")
    print(f"摘要内容: {compressed_history[0].content[:50]}...")

    print("\n✅ 历史管理测试完成")


def demo_observation_truncator():
    """演示工具输出截断"""
    print("\n" + "=" * 60)
    print("示例 5: 工具输出截断")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # 创建截断器
        truncator = ObservationTruncator(
            max_lines=10,
            max_bytes=500,
            truncate_direction="head",
            output_dir=temp_dir
        )
        
        # 生成长输出
        long_output = "\n".join([f"Line {i+1}: Some content here" for i in range(100)])
        
        print(f"\n原始输出: {len(long_output)} 字节, {len(long_output.splitlines())} 行")
        
        # 截断输出
        result = truncator.truncate("search_tool", long_output)
        
        print(f"\n截断状态: {result['truncated']}")
        print(f"预览长度: {len(result['preview'])} 字节")
        print(f"保存路径: {result.get('full_output_path', 'N/A')}")
        print(f"\n预览内容:\n{result['preview'][:200]}...")
        
        # 验证完整输出已保存
        if result.get('full_output_path'):
            saved_path = Path(result['full_output_path'])
            assert saved_path.exists()
            print(f"\n✅ 完整输出已保存到: {saved_path.name}")


def demo_session_serialization():
    """演示会话序列化"""
    print("\n" + "=" * 60)
    print("示例 6: 会话序列化/反序列化")
    print("=" * 60)
    
    # 创建历史管理器
    manager = HistoryManager(min_retain_rounds=5)
    
    # 添加消息
    manager.append(Message("你好", "user"))
    manager.append(Message("你好！有什么可以帮助你的？", "assistant"))
    manager.append(Message("介绍一下你自己", "user"))
    manager.append(Message("我是 AI 助手", "assistant"))
    
    print(f"\n原始历史: {len(manager.get_history())} 条消息")

    # 序列化
    serialized = manager.to_dict()
    print(f"序列化数据: {len(serialized['history'])} 条消息")
    
    # 创建新管理器并反序列化
    new_manager = HistoryManager()
    new_manager.load_from_dict(serialized)
    
    print(f"恢复后历史: {len(new_manager.get_history())} 条消息")
    
    # 验证内容一致
    original = manager.get_history()
    restored = new_manager.get_history()
    
    assert len(original) == len(restored)
    assert original[0].content == restored[0].content
    
    print("\n✅ 会话序列化测试完成")


def demo_round_boundaries():
    """演示轮次边界检测"""
    print("\n" + "=" * 60)
    print("示例 7: 轮次边界检测")
    print("=" * 60)
    
    manager = HistoryManager()
    
    # 添加复杂对话（包含工具调用）
    manager.append(Message("计算 2+3", "user"))
    manager.append(Message("我需要使用计算器", "assistant"))
    manager.append(Message("5", "tool"))
    manager.append(Message("结果是 5", "assistant"))
    
    manager.append(Message("再算 10*2", "user"))
    manager.append(Message("使用计算器", "assistant"))
    manager.append(Message("20", "tool"))
    manager.append(Message("结果是 20", "assistant"))
    
    # 检测轮次边界
    boundaries = manager.find_round_boundaries()
    rounds = manager.estimate_rounds()
    
    print(f"\n总消息数: {len(manager.get_history())}")
    print(f"轮次边界: {boundaries}")
    print(f"完整轮次数: {rounds}")
    
    print("\n✅ 轮次边界检测完成")


if __name__ == "__main__":
    print("\n🚀 上下文工程示例演示\n")

    # 运行所有示例
    demo_token_counter()
    demo_simple_summary()
    demo_smart_summary()
    demo_history_management()
    demo_observation_truncator()
    demo_session_serialization()
    demo_round_boundaries()

    print("\n" + "=" * 60)
    print("✅ 所有示例运行完成！")
    print("=" * 60)

