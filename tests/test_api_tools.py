"""Tests for api_tools module."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from api_tools import (
    AnthropicTool,
    APIResponse,
    GoogleAITool,
    OpenAITool,
)
from llm_tools import LLMToolFactory, ToolType


class TestAPIResponse:
    """Tests for APIResponse dataclass."""

    def test_api_response_creation(self):
        response = APIResponse(
            content="Hello, world!",
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        assert response.content == "Hello, world!"
        assert response.finish_reason == "stop"
        assert response.usage["prompt_tokens"] == 10
        assert response.usage["completion_tokens"] == 5


class TestOpenAITool:
    """Tests for OpenAITool."""

    def test_model_name(self):
        tool = OpenAITool()
        assert tool.model == "gpt-4o"

    def test_is_available_with_env(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"}):
            tool = OpenAITool()
            assert tool.is_available() is True

    def test_is_available_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            # Ensure OPENAI_API_KEY is not set
            os.environ.pop("OPENAI_API_KEY", None)
            tool = OpenAITool()
            assert tool.is_available() is False


class TestAnthropicTool:
    """Tests for AnthropicTool."""

    def test_model_name(self):
        tool = AnthropicTool()
        assert tool.model == "claude-sonnet-4-20250514"

    def test_is_available_with_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test123"}):
            tool = AnthropicTool()
            assert tool.is_available() is True

    def test_is_available_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            tool = AnthropicTool()
            assert tool.is_available() is False


class TestGoogleAITool:
    """Tests for GoogleAITool."""

    def test_model_name(self):
        tool = GoogleAITool()
        assert tool.model == "gemini-2.0-flash"

    def test_is_available_with_env(self):
        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": "AItest123"}):
            tool = GoogleAITool()
            assert tool.is_available() is True

    def test_is_available_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GOOGLE_AI_API_KEY", None)
            tool = GoogleAITool()
            assert tool.is_available() is False


class TestLLMToolFactoryAPITools:
    """Tests for LLMToolFactory API tool methods."""

    def test_is_api_tool_gemini_api(self):
        assert LLMToolFactory.is_api_tool(ToolType.GEMINI_API) is True

    def test_is_api_tool_openai_api(self):
        assert LLMToolFactory.is_api_tool(ToolType.OPENAI_API) is True

    def test_is_api_tool_anthropic_api(self):
        assert LLMToolFactory.is_api_tool(ToolType.ANTHROPIC_API) is True

    def test_is_api_tool_cli_tools(self):
        assert LLMToolFactory.is_api_tool(ToolType.GEMINI) is False
        assert LLMToolFactory.is_api_tool(ToolType.CODEX) is False
        assert LLMToolFactory.is_api_tool(ToolType.CLAUDE) is False

    def test_create_api_tool_openai(self):
        tool = LLMToolFactory.create_api_tool(ToolType.OPENAI_API)
        assert isinstance(tool, OpenAITool)

    def test_create_api_tool_anthropic(self):
        tool = LLMToolFactory.create_api_tool(ToolType.ANTHROPIC_API)
        assert isinstance(tool, AnthropicTool)

    def test_create_api_tool_gemini(self):
        tool = LLMToolFactory.create_api_tool(ToolType.GEMINI_API)
        assert isinstance(tool, GoogleAITool)

    def test_create_api_tool_invalid(self):
        with pytest.raises(ValueError, match="Unknown API tool type"):
            LLMToolFactory.create_api_tool(ToolType.CLAUDE)


class TestToolTypeAPIValues:
    """Tests for API ToolType enum values."""

    def test_api_tool_type_values(self):
        assert ToolType.GEMINI_API.value == "gemini_api"
        assert ToolType.OPENAI_API.value == "openai_api"
        assert ToolType.ANTHROPIC_API.value == "anthropic_api"

    def test_api_tool_type_from_string(self):
        assert ToolType("gemini_api") == ToolType.GEMINI_API
        assert ToolType("openai_api") == ToolType.OPENAI_API
        assert ToolType("anthropic_api") == ToolType.ANTHROPIC_API
