"""LLM Tool Abstractions for AI Orchestration.

This module provides configurable LLM tool support, allowing each stage
of the orchestration pipeline to use different LLM providers.
"""

import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class ToolType(str, Enum):
    """Supported LLM tool types."""

    GEMINI = "gemini"
    CODEX = "codex"
    CLAUDE = "claude"


class StageRole(str, Enum):
    """Stage roles in the orchestration pipeline."""

    BRAINSTORMER = "brainstormer"
    REVIEWER = "reviewer"
    PLANNER = "planner"
    EXECUTOR = "executor"
    CODE_REVIEWER = "code_reviewer"
    FIXER = "fixer"


@dataclass
class LLMToolConfig:
    """Configuration for LLM tools used in each stage."""

    brainstormer: ToolType = ToolType.GEMINI
    reviewer: ToolType = ToolType.CODEX
    planner: ToolType = ToolType.CODEX
    executor: ToolType = ToolType.CLAUDE
    code_reviewer: ToolType = ToolType.CODEX
    fixer: ToolType = ToolType.CLAUDE

    def get_tool_for_stage(self, stage: StageRole) -> ToolType:
        """Get the configured tool type for a stage."""
        return getattr(self, stage.value)


class BaseLLMTool(ABC):
    """Abstract base class for LLM tools."""

    @abstractmethod
    def get_binary_path(self) -> str:
        """Return the path to the tool's binary."""
        pass

    @abstractmethod
    def build_command(self, prompt: str, debug: bool = False) -> list[str]:
        """Build the command list for executing the tool."""
        pass

    def is_available(self) -> bool:
        """Check if the tool binary is available in PATH."""
        return shutil.which(self.get_binary_path()) is not None


class GeminiTool(BaseLLMTool):
    """Gemini CLI tool implementation."""

    def get_binary_path(self) -> str:
        return shutil.which("gemini") or "gemini"

    def build_command(self, prompt: str, debug: bool = False) -> list[str]:
        return [self.get_binary_path(), prompt]


class CodexTool(BaseLLMTool):
    """Codex (ChatGPT) CLI tool implementation."""

    def get_binary_path(self) -> str:
        return shutil.which("codex") or "codex"

    def build_command(self, prompt: str, debug: bool = False) -> list[str]:
        return [self.get_binary_path(), "exec", prompt]


class ClaudeTool(BaseLLMTool):
    """Claude CLI tool implementation."""

    def get_binary_path(self) -> str:
        return shutil.which("claude") or "claude"

    def build_command(self, prompt: str, debug: bool = False) -> list[str]:
        cmd = [
            self.get_binary_path(),
            prompt,
            "--print",
            "--tools",
            "",
            "--disable-slash-commands",
            "--permission-mode",
            "dontAsk",
        ]
        if debug:
            cmd.extend(["--output-format", "stream-json"])
        return cmd


class LLMToolFactory:
    """Factory for creating LLM tool instances."""

    _tools: dict[ToolType, type[BaseLLMTool]] = {
        ToolType.GEMINI: GeminiTool,
        ToolType.CODEX: CodexTool,
        ToolType.CLAUDE: ClaudeTool,
    }

    @classmethod
    def create(cls, tool_type: ToolType) -> BaseLLMTool:
        """Create an LLM tool instance for the given type."""
        tool_class = cls._tools.get(tool_type)
        if tool_class is None:
            raise ValueError(f"Unknown tool type: {tool_type}")
        return tool_class()

    @classmethod
    def get_tool_for_stage(cls, config: LLMToolConfig, stage: StageRole) -> BaseLLMTool:
        """Get the configured tool instance for a stage."""
        tool_type = config.get_tool_for_stage(stage)
        return cls.create(tool_type)


def load_tool_config(
    config_file: Optional[Path] = None,
    brainstormer: Optional[str] = None,
    reviewer: Optional[str] = None,
    planner: Optional[str] = None,
    executor: Optional[str] = None,
    code_reviewer: Optional[str] = None,
    fixer: Optional[str] = None,
) -> LLMToolConfig:
    """Load tool configuration from file or CLI options.

    Priority: CLI options > config file > defaults
    """
    config = LLMToolConfig()

    if config_file and config_file.exists():
        with open(config_file) as f:
            data = json.load(f)
        config = LLMToolConfig(
            brainstormer=ToolType(data.get("brainstormer", "gemini")),
            reviewer=ToolType(data.get("reviewer", "codex")),
            planner=ToolType(data.get("planner", "codex")),
            executor=ToolType(data.get("executor", "claude")),
            code_reviewer=ToolType(data.get("code_reviewer", "codex")),
            fixer=ToolType(data.get("fixer", "claude")),
        )

    if brainstormer:
        config.brainstormer = ToolType(brainstormer)
    if reviewer:
        config.reviewer = ToolType(reviewer)
    if planner:
        config.planner = ToolType(planner)
    if executor:
        config.executor = ToolType(executor)
    if code_reviewer:
        config.code_reviewer = ToolType(code_reviewer)
    if fixer:
        config.fixer = ToolType(fixer)

    return config


def validate_tool_config(config: LLMToolConfig) -> list[str]:
    """Validate tool configuration and return warnings.

    Returns a list of warning messages for tools not found in PATH.
    """
    warnings = []
    used_tools = {
        config.brainstormer,
        config.reviewer,
        config.planner,
        config.executor,
        config.code_reviewer,
        config.fixer,
    }

    for tool_type in used_tools:
        tool = LLMToolFactory.create(tool_type)
        if not tool.is_available():
            warnings.append(
                f"Tool '{tool_type.value}' not found in PATH. "
                f"Ensure it is installed before running."
            )

    return warnings
