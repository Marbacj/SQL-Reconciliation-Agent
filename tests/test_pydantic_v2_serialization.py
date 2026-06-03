"""Pydantic v2 serialization compatibility tests."""

import warnings

from pydantic.warnings import PydanticDeprecatedSince20

from recon_core.core.config import Config
from recon_core.tools.base import Tool, ToolParameter
from recon_core.tools.response import ToolResponse


class DemoTool(Tool):
    def __init__(self):
        super().__init__("demo", "Demo tool")

    def run(self, parameters):
        return ToolResponse.success(text="ok")

    def get_parameters(self):
        return [
            ToolParameter(
                name="query",
                type="string",
                description="Search query",
            )
        ]


def test_config_to_dict_uses_pydantic_v2_serialization():
    with warnings.catch_warnings():
        warnings.simplefilter("error", PydanticDeprecatedSince20)
        data = Config().to_dict()

    assert data["default_model"] == "gpt-3.5-turbo"


def test_tool_to_dict_uses_pydantic_v2_serialization():
    with warnings.catch_warnings():
        warnings.simplefilter("error", PydanticDeprecatedSince20)
        data = DemoTool().to_dict()

    assert data["parameters"] == [
        {
            "name": "query",
            "type": "string",
            "description": "Search query",
            "required": True,
            "default": None,
        }
    ]
