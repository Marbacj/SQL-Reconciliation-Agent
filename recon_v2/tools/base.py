"""Tool 系统基础抽象。

每个工具继承 `ToolBase`：
- 用 Pydantic 声明输入/输出 schema
- 提供 `run(ctx, inp) -> ToolOutput`
- 通过 `to_openai_function()` 暴露 OpenAI Function Calling 兼容 schema
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, Generic, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class ToolInput(BaseModel):
    """所有 Tool 输入 schema 的基类（约定空）。"""


class ToolOutput(BaseModel):
    """所有 Tool 输出 schema 的基类。"""

    success: bool = True
    error: Optional[str] = None
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = {}


class ToolBase(ABC, Generic[InputT, OutputT]):
    """工具基类。子类需声明 name / description / input_schema / output_schema。"""

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[Type[ToolInput]]
    output_schema: ClassVar[Type[ToolOutput]]

    # 可选：用于 ToolRegistry.filter_by_intent
    intents: ClassVar[tuple] = ()

    @abstractmethod
    def _run(self, ctx: Any, inp: InputT) -> OutputT:
        """子类实现的真实逻辑。ctx 类型在运行期为 AgentContext。"""

    def run(self, ctx: Any, inp_data: Dict[str, Any] | InputT) -> OutputT:
        """统一入口：负责 input validation、计时、错误兜底。"""
        t0 = time.time()

        # 校验输入
        try:
            if isinstance(inp_data, self.input_schema):
                inp = inp_data
            else:
                inp = self.input_schema(**inp_data)
        except ValidationError as ve:
            # 直接抛 — 让上层 Agent 看到 schema 错误重新组织参数
            raise

        try:
            out = self._run(ctx, inp)
            # 补 latency
            try:
                out.latency_ms = (time.time() - t0) * 1000
            except Exception:
                pass
            return out
        except Exception as e:
            # 包装为失败 ToolOutput
            return self.output_schema(  # type: ignore
                success=False,
                error=f"{type(e).__name__}: {e}",
                latency_ms=(time.time() - t0) * 1000,
            )

    # --------- OpenAI Function Calling schema ---------

    @classmethod
    def to_openai_function(cls) -> Dict[str, Any]:
        """转 OpenAI Function Calling 兼容描述。"""
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": cls.input_schema.model_json_schema(),
            },
        }
