"""对账报告生成工具 — 将对账结果格式化为可读的 Markdown 报告并保存"""

import os
from datetime import datetime
from typing import Dict, Any, List

from ..base import Tool, ToolParameter, tool_action
from ..response import ToolResponse, ToolStatus
from ..errors import ToolErrorCode


class ReportTool(Tool):
    """报告生成工具：组装 Markdown 对账报告并保存到文件。

    使用方式：
        tool = ReportTool(output_dir="reports")
        registry.register_tool(tool)  # 注册 report_generate 子工具
    """

    def __init__(self, output_dir: str = "reports"):
        super().__init__(
            name="ReportTool",
            description="报告生成工具 — 将对账结果格式化为 Markdown 报告并保存",
            expandable=True
        )
        self.output_dir = output_dir

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        return ToolResponse.error(
            code=ToolErrorCode.INVALID_PARAM,
            message="ReportTool 是可展开工具集，请使用子工具 report_generate"
        )

    def get_parameters(self) -> List[ToolParameter]:
        return []

    @tool_action("report_generate", "将对账结果格式化为可读的 Markdown 报告并保存到文件")
    def _generate(
        self,
        title: str,
        diff_result: str,
        conclusion: str
    ) -> str:
        """组装完整 Markdown 报告并保存到文件

        Args:
            title: 报告标题（如 "2026-05-27 直播GMV对账报告"）
            diff_result: diff_compare 工具的输出文本
            conclusion: Agent 分析后的结论（对差异的解释和建议）

        Returns:
            保存成功时返回文件路径，失败时返回错误信息
        """
        try:
            # 确保输出目录存在
            os.makedirs(self.output_dir, exist_ok=True)

            # 生成时间戳
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_title = title.replace(" ", "_").replace("/", "-")[:50]
            filename = f"{timestamp}_{safe_title}.md"
            filepath = os.path.join(self.output_dir, filename)

            # 组装报告
            report = f"# {title}\n\n"
            report += f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            report += "---\n\n"
            report += "## 对账详情\n\n"
            report += diff_result
            report += "\n---\n\n"
            report += "## 分析结论\n\n"
            report += conclusion
            report += "\n\n---\n\n"
            report += "*本报告由 ReconciliationAgent 自动生成*\n"

            # 写入文件
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(report)

            return f"✅ 报告已保存到: {filepath}\n\n---\n{report}"

        except Exception as e:
            return f"❌ 报告生成失败: {str(e)}"
