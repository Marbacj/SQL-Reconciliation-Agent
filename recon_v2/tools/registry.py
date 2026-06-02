"""ToolRegistry：注册 / 查询 / 按 intent 过滤工具。"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Type

from recon_v2.tools.base import ToolBase

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolBase] = {}

    def register(self, tool: ToolBase) -> None:
        if tool.name in self._tools:
            logger.warning("Tool %s already registered, override", tool.name)
        self._tools[tool.name] = tool

    def register_cls(self, tool_cls: Type[ToolBase], **init_kwargs) -> None:
        self.register(tool_cls(**init_kwargs))

    def get(self, name: str) -> Optional[ToolBase]:
        return self._tools.get(name)

    def all(self) -> List[ToolBase]:
        return list(self._tools.values())

    def filter_by_intent(self, intent: Optional[str]) -> List[ToolBase]:
        """按 intent 过滤；如工具的 intents 为空集合则视为通用工具，总是返回。"""
        if not intent:
            return self.all()
        out: List[ToolBase] = []
        for t in self._tools.values():
            if not t.intents or intent in t.intents:
                out.append(t)
        return out

    def to_openai_functions(self, intent: Optional[str] = None) -> List[dict]:
        return [t.__class__.to_openai_function() for t in self.filter_by_intent(intent)]
