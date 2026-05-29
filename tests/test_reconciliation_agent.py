"""Tests for ReconciliationAgent — initialization and tool registration.

No LLM calls are made in these tests; we validate structure only.
"""

import os
import pytest

from hello_agents.agents.reconciliation_agent import (
    ReconciliationAgent,
    RECONCILIATION_SYSTEM_PROMPT,
)
from hello_agents.core.llm import HelloAgentsLLM
from hello_agents.core.config import Config


# Dummy LLM instance — constructor doesn't validate credentials,
# and we never call .run() so no API request is made.
def _dummy_llm():
    return HelloAgentsLLM(
        model="test-model",
        api_key="sk-test-noop",
        base_url="http://localhost:9999",
    )


DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mock_reconciliation.db")


class TestReconciliationAgentInit:
    """Test ReconciliationAgent can be initialized and has correct tools."""

    @classmethod
    def setup_class(cls):
        cls.llm = _dummy_llm()

    def test_basic_init(self):
        agent = ReconciliationAgent(
            name="TestReconAgent",
            llm=self.llm,
            db_path=DB_PATH,
        )
        assert agent.name == "TestReconAgent"
        assert agent.llm is self.llm
        assert agent.db_path == DB_PATH
        assert agent.output_dir == "reports"  # default

    def test_custom_output_dir(self):
        agent = ReconciliationAgent(
            name="CustomDirAgent",
            llm=self.llm,
            db_path=DB_PATH,
            output_dir="custom_reports",
        )
        assert agent.output_dir == "custom_reports"

    def test_custom_max_steps(self):
        agent = ReconciliationAgent(
            name="StepsAgent",
            llm=self.llm,
            db_path=DB_PATH,
            max_steps=12,
        )
        assert agent.max_steps == 12

    def test_default_max_steps_is_8(self):
        agent = ReconciliationAgent(
            name="DefaultStepsAgent",
            llm=self.llm,
            db_path=DB_PATH,
        )
        assert agent.max_steps == 8

    # ---------- tool_registry tests ----------

    def test_tool_registry_is_set(self):
        agent = ReconciliationAgent(
            name="ToolCheckAgent",
            llm=self.llm,
            db_path=DB_PATH,
        )
        assert agent.tool_registry is not None

    def test_all_reconciliation_sub_tools_registered(self):
        """Verify that the 5 sub-tools are registered after init."""
        agent = ReconciliationAgent(
            name="FullToolsAgent",
            llm=self.llm,
            db_path=DB_PATH,
        )
        tool_names = agent.tool_registry.list_tools()
        expected_tools = [
            "sql_schema",
            "sql_execute",
            "sql_validate",
            "diff_compare",
            "report_generate",
        ]
        for name in expected_tools:
            assert name in tool_names, f"Expected tool '{name}' not found in {tool_names}"

    def test_all_reconciliation_sub_tools_in_subset(self):
        """Verify the 5 reconciliation sub-tools are present. The agent may also
        register additional built-in tools (Skill, Task, TodoWrite, DevLog)."""
        agent = ReconciliationAgent(
            name="SubsetAgent",
            llm=self.llm,
            db_path=DB_PATH,
        )
        tool_names = agent.tool_registry.list_tools()
        reconciliation_tools = [
            "sql_schema",
            "sql_execute",
            "sql_validate",
            "diff_compare",
            "report_generate",
        ]
        for name in reconciliation_tools:
            assert name in tool_names, f"Expected tool '{name}' not found in {tool_names}"

    def test_parent_tools_not_registered(self):
        """Expandable parent tools (SQLTool, DiffTool, ReportTool) should not
        appear in the registry — only their expanded sub-tools."""
        agent = ReconciliationAgent(
            name="ParentCheckAgent",
            llm=self.llm,
            db_path=DB_PATH,
        )
        tool_names = agent.tool_registry.list_tools()
        assert "SQLTool" not in tool_names
        assert "DiffTool" not in tool_names
        assert "ReportTool" not in tool_names

    # ---------- system_prompt tests ----------

    def test_default_system_prompt(self):
        agent = ReconciliationAgent(
            name="PromptAgent",
            llm=self.llm,
            db_path=DB_PATH,
        )
        assert agent.system_prompt == RECONCILIATION_SYSTEM_PROMPT
        assert "sql_schema" in agent.system_prompt
        assert "diff_compare" in agent.system_prompt
        assert "report_generate" in agent.system_prompt

    def test_custom_system_prompt(self):
        custom = "Custom reconciliation prompt."
        agent = ReconciliationAgent(
            name="CustomPromptAgent",
            llm=self.llm,
            db_path=DB_PATH,
            system_prompt=custom,
        )
        assert agent.system_prompt == custom

    # ---------- subclass identity ----------

    def test_is_react_agent_subclass(self):
        from hello_agents.agents.react_agent import ReActAgent
        assert issubclass(ReconciliationAgent, ReActAgent)

    def test_is_agent_subclass(self):
        from hello_agents.core.agent import Agent
        assert issubclass(ReconciliationAgent, Agent)

    # ---------- config ----------

    def test_config_default(self):
        agent = ReconciliationAgent(
            name="ConfigAgent",
            llm=self.llm,
            db_path=DB_PATH,
        )
        assert agent.config is not None
        assert isinstance(agent.config, Config)
