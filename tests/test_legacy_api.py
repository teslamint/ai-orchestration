"""Legacy API-based tool compatibility tests, ported from tests/test_api_tools.py (U3).

APIResponse/OpenAITool/AnthropicTool/GoogleAITool preserve their legacy
public surface (`generate`, `generate_stream`, `is_available`, `APIResponse`
fields) unchanged, along with the three `*_API` ToolType values and the
`LLMToolFactory.is_api_tool` / `create_api_tool` compatibility paths.
"""

import os
from unittest.mock import patch

import pytest

from ai_orchestration.providers.base import ToolType
from ai_orchestration.providers.cli import LLMToolFactory
from ai_orchestration.providers.legacy_api import (
    AnthropicTool,
    APIResponse,
    GoogleAITool,
    OpenAITool,
)


def test_api_response_creation():
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
    def test_openai_tool_model_name(self):
        tool = OpenAITool()
        assert tool.model == "gpt-4o"

    def test_openai_tool_is_available_with_env(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"}):
            tool = OpenAITool()
            assert tool.is_available() is True

    def test_openai_tool_is_available_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENAI_API_KEY", None)
            tool = OpenAITool()
            assert tool.is_available() is False


class TestAnthropicTool:
    def test_anthropic_tool_model_name(self):
        tool = AnthropicTool()
        assert tool.model == "claude-sonnet-4-20250514"

    def test_anthropic_tool_is_available_with_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test123"}):
            tool = AnthropicTool()
            assert tool.is_available() is True

    def test_anthropic_tool_is_available_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            tool = AnthropicTool()
            assert tool.is_available() is False


class TestGoogleAITool:
    def test_google_tool_model_name(self):
        tool = GoogleAITool()
        assert tool.model == "gemini-2.0-flash"

    def test_google_tool_is_available_with_env(self):
        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": "AItest123"}):
            tool = GoogleAITool()
            assert tool.is_available() is True

    def test_google_tool_is_available_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GOOGLE_AI_API_KEY", None)
            tool = GoogleAITool()
            assert tool.is_available() is False


class TestLLMToolFactoryAPITools:
    def test_is_api_tool_gemini_api(self):
        assert LLMToolFactory.is_api_tool(ToolType.GEMINI_API) is True

    def test_is_api_tool_openai_api(self):
        assert LLMToolFactory.is_api_tool(ToolType.OPENAI_API) is True

    def test_is_api_tool_anthropic_api(self):
        assert LLMToolFactory.is_api_tool(ToolType.ANTHROPIC_API) is True

    def test_is_api_tool_cli_tools(self):
        assert LLMToolFactory.is_api_tool(ToolType.AGY) is False
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
    def test_api_tool_type_values(self):
        assert ToolType.GEMINI_API.value == "gemini_api"
        assert ToolType.OPENAI_API.value == "openai_api"
        assert ToolType.ANTHROPIC_API.value == "anthropic_api"

    def test_api_tool_type_from_string(self):
        assert ToolType("gemini_api") == ToolType.GEMINI_API
        assert ToolType("openai_api") == ToolType.OPENAI_API
        assert ToolType("anthropic_api") == ToolType.ANTHROPIC_API


# --- Missing-key path: no live network calls, deliberate error types -------


def test_openai_tool_generate_raises_when_key_missing(monkeypatch):
    from openai import OpenAIError

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    tool = OpenAITool()
    assert tool.is_available() is False
    # The `openai` SDK raises this before opening any socket -- construction
    # fails fast on missing credentials, so this never reaches the network.
    with pytest.raises(OpenAIError, match="OPENAI_API_KEY"):
        tool.generate("hello")


def test_anthropic_tool_generate_raises_when_key_missing(monkeypatch):
    pytest.importorskip("anthropic")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    tool = AnthropicTool()
    assert tool.is_available() is False
    # The `anthropic` SDK validates headers before sending the request, so
    # this never reaches the network; it raises a bare TypeError, not a
    # subclass of AnthropicError, which is why the assertion also matches
    # the message rather than relying on a narrower exception class alone.
    with pytest.raises(TypeError, match="authentication"):
        tool.generate("hello")


def test_google_tool_generate_raises_when_key_missing(monkeypatch):
    pytest.importorskip("google.generativeai")
    import google.auth
    import google.auth.exceptions

    # Without a stub, `google.generativeai` falls back to ambient GCP
    # Application Default Credentials (if any are configured on the host)
    # and performs a REAL network call to the Generative Language API. Stub
    # out credential discovery so the missing-key path is deterministic and
    # never touches the network, regardless of the host's ADC state.
    def _no_ambient_credentials(*_args, **_kwargs):
        raise google.auth.exceptions.DefaultCredentialsError(
            "no application default credentials (test isolation stub)"
        )

    monkeypatch.setattr(google.auth, "default", _no_ambient_credentials)
    monkeypatch.delenv("GOOGLE_AI_API_KEY", raising=False)
    tool = GoogleAITool()
    assert tool.is_available() is False
    with pytest.raises(google.auth.exceptions.DefaultCredentialsError):
        tool.generate("hello")
