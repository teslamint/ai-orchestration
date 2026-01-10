"""Tests for llm_tools module."""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from llm_tools import (
    ClaudeTool,
    CodexTool,
    GeminiTool,
    LLMToolConfig,
    LLMToolFactory,
    StageRole,
    ToolType,
    load_tool_config,
    validate_tool_config,
)


class TestToolType:
    """Tests for ToolType enum."""

    def test_tool_type_values(self):
        assert ToolType.GEMINI.value == "gemini"
        assert ToolType.CODEX.value == "codex"
        assert ToolType.CLAUDE.value == "claude"

    def test_tool_type_from_string(self):
        assert ToolType("gemini") == ToolType.GEMINI
        assert ToolType("codex") == ToolType.CODEX
        assert ToolType("claude") == ToolType.CLAUDE

    def test_tool_type_invalid(self):
        with pytest.raises(ValueError):
            ToolType("invalid")


class TestStageRole:
    """Tests for StageRole enum."""

    def test_stage_role_values(self):
        assert StageRole.BRAINSTORMER.value == "brainstormer"
        assert StageRole.REVIEWER.value == "reviewer"
        assert StageRole.PLANNER.value == "planner"
        assert StageRole.EXECUTOR.value == "executor"
        assert StageRole.CODE_REVIEWER.value == "code_reviewer"
        assert StageRole.FIXER.value == "fixer"


class TestLLMToolConfig:
    """Tests for LLMToolConfig dataclass."""

    def test_default_config(self):
        config = LLMToolConfig()
        assert config.brainstormer == ToolType.GEMINI
        assert config.reviewer == ToolType.CODEX
        assert config.planner == ToolType.CODEX
        assert config.executor == ToolType.CLAUDE
        assert config.code_reviewer == ToolType.CODEX
        assert config.fixer == ToolType.CLAUDE

    def test_custom_config(self):
        config = LLMToolConfig(
            brainstormer=ToolType.CLAUDE,
            reviewer=ToolType.CLAUDE,
            planner=ToolType.GEMINI,
            executor=ToolType.GEMINI,
            code_reviewer=ToolType.CLAUDE,
            fixer=ToolType.GEMINI,
        )
        assert config.brainstormer == ToolType.CLAUDE
        assert config.planner == ToolType.GEMINI

    def test_get_tool_for_stage(self):
        config = LLMToolConfig()
        assert config.get_tool_for_stage(StageRole.BRAINSTORMER) == ToolType.GEMINI
        assert config.get_tool_for_stage(StageRole.EXECUTOR) == ToolType.CLAUDE


class TestGeminiTool:
    """Tests for GeminiTool."""

    def test_build_command(self):
        tool = GeminiTool()
        cmd = tool.build_command("test prompt")
        assert len(cmd) == 2
        assert cmd[1] == "test prompt"

    def test_build_command_with_debug(self):
        tool = GeminiTool()
        cmd = tool.build_command("test prompt", debug=True)
        assert cmd[1] == "test prompt"


class TestCodexTool:
    """Tests for CodexTool."""

    def test_build_command(self):
        tool = CodexTool()
        cmd = tool.build_command("test prompt")
        assert len(cmd) == 3
        assert cmd[1] == "exec"
        assert cmd[2] == "test prompt"

    def test_build_command_with_debug(self):
        tool = CodexTool()
        cmd = tool.build_command("test prompt", debug=True)
        assert "exec" in cmd
        assert cmd[-1] == "test prompt"


class TestClaudeTool:
    """Tests for ClaudeTool."""

    def test_build_command(self):
        tool = ClaudeTool()
        cmd = tool.build_command("test prompt")
        assert "--print" in cmd
        assert "--tools" in cmd
        assert "--disable-slash-commands" in cmd
        assert "--permission-mode" in cmd
        assert "dontAsk" in cmd
        assert "test prompt" in cmd

    def test_build_command_with_debug(self):
        tool = ClaudeTool()
        cmd = tool.build_command("test prompt", debug=True)
        assert "--output-format" in cmd
        assert "stream-json" in cmd


class TestLLMToolFactory:
    """Tests for LLMToolFactory."""

    def test_create_gemini(self):
        tool = LLMToolFactory.create(ToolType.GEMINI)
        assert isinstance(tool, GeminiTool)

    def test_create_codex(self):
        tool = LLMToolFactory.create(ToolType.CODEX)
        assert isinstance(tool, CodexTool)

    def test_create_claude(self):
        tool = LLMToolFactory.create(ToolType.CLAUDE)
        assert isinstance(tool, ClaudeTool)

    def test_get_tool_for_stage(self):
        config = LLMToolConfig()
        tool = LLMToolFactory.get_tool_for_stage(config, StageRole.BRAINSTORMER)
        assert isinstance(tool, GeminiTool)

        tool = LLMToolFactory.get_tool_for_stage(config, StageRole.EXECUTOR)
        assert isinstance(tool, ClaudeTool)


class TestLoadToolConfig:
    """Tests for load_tool_config function."""

    def test_load_default(self):
        config = load_tool_config()
        assert config.brainstormer == ToolType.GEMINI
        assert config.reviewer == ToolType.CODEX

    def test_load_with_cli_options(self):
        config = load_tool_config(
            brainstormer="claude",
            planner="gemini",
        )
        assert config.brainstormer == ToolType.CLAUDE
        assert config.planner == ToolType.GEMINI
        assert config.reviewer == ToolType.CODEX

    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "brainstormer": "claude",
                    "reviewer": "gemini",
                    "planner": "claude",
                    "executor": "gemini",
                    "code_reviewer": "claude",
                    "fixer": "gemini",
                },
                f,
            )
            f.flush()
            config_path = Path(f.name)

        config = load_tool_config(config_file=config_path)
        assert config.brainstormer == ToolType.CLAUDE
        assert config.reviewer == ToolType.GEMINI
        assert config.planner == ToolType.CLAUDE
        assert config.executor == ToolType.GEMINI

        config_path.unlink()

    def test_cli_options_override_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"brainstormer": "codex"}, f)
            f.flush()
            config_path = Path(f.name)

        config = load_tool_config(
            config_file=config_path,
            brainstormer="claude",
        )
        assert config.brainstormer == ToolType.CLAUDE

        config_path.unlink()


class TestValidateToolConfig:
    """Tests for validate_tool_config function."""

    def test_validate_returns_warnings_list(self):
        config = LLMToolConfig()
        warnings = validate_tool_config(config)
        assert isinstance(warnings, list)
