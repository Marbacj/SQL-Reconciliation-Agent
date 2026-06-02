"""Tool package：导出 5 个核心工具 + ToolRegistry。"""

from recon_v2.tools.base import ToolBase, ToolInput, ToolOutput
from recon_v2.tools.case_query import CaseQueryTool
from recon_v2.tools.diff_calculator import DiffCalculatorTool
from recon_v2.tools.rag_searcher import RagSearcherTool
from recon_v2.tools.registry import ToolRegistry
from recon_v2.tools.report_generator import ReportGeneratorTool
from recon_v2.tools.sql_runner import SQLRunnerTool

__all__ = [
    "ToolBase",
    "ToolInput",
    "ToolOutput",
    "ToolRegistry",
    "SQLRunnerTool",
    "DiffCalculatorTool",
    "ReportGeneratorTool",
    "RagSearcherTool",
    "CaseQueryTool",
    "build_default_registry",
]


def build_default_registry(db_path: str) -> ToolRegistry:
    """构造 v2 默认 ToolRegistry。"""
    reg = ToolRegistry()
    reg.register(SQLRunnerTool(db_path=db_path))
    reg.register(DiffCalculatorTool())
    reg.register(ReportGeneratorTool())
    reg.register(RagSearcherTool())
    reg.register(CaseQueryTool())
    return reg
