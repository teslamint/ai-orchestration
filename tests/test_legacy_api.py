"""Legacy API-based tool compatibility tests, ported from tests/test_api_tools.py (U3).

APIResponse/OpenAITool/AnthropicTool/GoogleAITool preserve their legacy
public surface (`generate`, `generate_stream`, `is_available`, `APIResponse`
fields) unchanged, along with the three `*_API` ToolType values and the
`LLMToolFactory.is_api_tool` / `create_api_tool` compatibility paths.
"""

import importlib.metadata
import json
import os
import socket
import sys
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_orchestration.errors import ConfigError, ProviderError
from ai_orchestration.providers import legacy_api
from ai_orchestration.providers.base import (
    AuthenticationError,
    ModelFaultError,
    Provider,
    ToolType,
    TransportError,
)
from ai_orchestration.providers.cli import LLMToolFactory
from ai_orchestration.providers.legacy_api import (
    AnthropicTool,
    APIBasedTool,
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

    def test_openai_tool_pins_official_endpoint_despite_environment_override(
        self, monkeypatch
    ):
        constructed = {}

        class Client:
            class chat:
                class completions:
                    @staticmethod
                    def create(**_kwargs):
                        return SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    message=SimpleNamespace(content="ok"),
                                    finish_reason="stop",
                                )
                            ],
                            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                        )

        def create_client(**kwargs):
            constructed.update(kwargs)
            return Client()

        monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.invalid/v1")
        monkeypatch.setitem(
            sys.modules, "openai", SimpleNamespace(OpenAI=create_client)
        )

        OpenAITool().generate_application_payload({"model": "gpt-4o", "messages": []})

        assert constructed["base_url"] == "https://api.openai.com/v1"


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


# --- Shared direct-vendor adapter contract (U3 Red matrix) -----------------


_DIRECT_VENDOR_CASES = (
    (ToolType.OPENAI_API, "OPENAI_API_KEY", "openai", "gpt-test"),
    (ToolType.ANTHROPIC_API, "ANTHROPIC_API_KEY", "anthropic", "claude-test"),
    (
        ToolType.GEMINI_API,
        "GOOGLE_AI_API_KEY",
        "google-generativeai",
        "gemini-test",
    ),
)


class _FakeVendorTool(APIBasedTool):
    def __init__(self, model: str, result=None, error: Exception | None = None):
        self.model = model
        self.result = result or APIResponse(
            content="vendor text",
            finish_reason="stop",
            usage={"prompt_tokens": 7, "completion_tokens": 3},
        )
        self.error = error
        self.calls = []
        self.generated_payloads = []

    def generate(self, prompt: str, system_prompt: str | None = None) -> APIResponse:
        self.calls.append((prompt, system_prompt))
        if self.error is not None:
            raise self.error
        return self.result

    def generate_stream(self, prompt: str, system_prompt: str | None = None):
        del prompt, system_prompt
        yield self.result.content

    def is_available(self) -> bool:
        return True

    def build_application_payload(self, prompt, system_prompt=None):
        self.built_call = (prompt, system_prompt)
        self.built_payload = _expected_application_payload(
            self.tool_type, self.model, prompt, system_prompt
        )
        return self.built_payload

    def generate_application_payload(self, payload):
        self.generated_payloads.append(payload)
        return self.generate(*self.built_call)


class _RecordingGate:
    def __init__(self):
        self.payloads = []

    def authorize(self, payload):
        self.payloads.append(payload)


class _FakeSDKError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _create_adapter(monkeypatch, tool_type, tool, **kwargs):
    tool.tool_type = tool_type
    monkeypatch.setattr(
        legacy_api,
        "validate_direct_vendor",
        lambda selected, environ: None,
        raising=False,
    )
    monkeypatch.setattr(
        LLMToolFactory,
        "create_api_tool",
        staticmethod(lambda selected: tool),
    )
    factory = getattr(legacy_api, "create_direct_vendor_provider")
    return factory(tool_type, **kwargs)


def _expected_application_payload(tool_type, model, prompt, system):
    if tool_type is ToolType.OPENAI_API:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return {"model": model, "messages": messages}
    if tool_type is ToolType.ANTHROPIC_API:
        payload = {
            "model": model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        return payload
    return {
        "model": model,
        "system_instruction": system,
        "contents": prompt,
    }


@pytest.mark.parametrize(
    ("tool_type", "_key", "_module", "model"), _DIRECT_VENDOR_CASES
)
def test_direct_vendor_adapter_preserves_text_system_usage_and_exact_authorization(
    monkeypatch, tool_type, _key, _module, model
):
    tool = _FakeVendorTool(model)
    usage = []
    gate = _RecordingGate()
    adapter = _create_adapter(
        monkeypatch,
        tool_type,
        tool,
        usage_sink=usage.append,
        authorization_gate=gate,
    )

    assert adapter.complete("user prompt", system="system prompt") == "vendor text"
    assert tool.calls == [("user prompt", "system prompt")]
    assert tool.generated_payloads == [tool.built_payload]
    assert tool.generated_payloads[0] is tool.built_payload
    assert usage == [{"prompt_tokens": 7, "completion_tokens": 3}]
    assert len(gate.payloads) == 1
    assert gate.payloads[0].transport == "http_json"
    assert json.loads(gate.payloads[0].render()) == _expected_application_payload(
        tool_type, model, "user prompt", "system prompt"
    )


@pytest.mark.parametrize(
    ("tool_type", "_key", "_module", "model"), _DIRECT_VENDOR_CASES
)
def test_direct_vendor_factory_validates_then_wraps_selected_tool(
    monkeypatch, tool_type, _key, _module, model
):
    calls = []
    tool = _FakeVendorTool(model)

    def validate(selected, environ):
        calls.append(("validate", selected, environ))

    def create(selected):
        calls.append(("create", selected))
        return tool

    monkeypatch.setattr(legacy_api, "validate_direct_vendor", validate, raising=False)
    monkeypatch.setattr(LLMToolFactory, "create_api_tool", staticmethod(create))

    factory = getattr(legacy_api, "create_direct_vendor_provider")
    adapter = factory(tool_type)

    assert isinstance(adapter, getattr(legacy_api, "LegacyProviderAdapter"))
    assert isinstance(adapter, Provider)
    assert calls[0][0:2] == ("validate", tool_type)
    assert calls[1] == ("create", tool_type)


@pytest.mark.parametrize(
    ("tool_type", "key", "_module", "_model"), _DIRECT_VENDOR_CASES
)
def test_validate_direct_vendor_rejects_missing_credential_before_socket_creation(
    monkeypatch, tool_type, key, _module, _model
):
    def deny_socket(*_args, **_kwargs):
        raise AssertionError("preflight opened a socket")

    monkeypatch.setattr(socket, "socket", deny_socket)
    validator = getattr(legacy_api, "validate_direct_vendor")

    with pytest.raises(ConfigError, match=key):
        validator(tool_type, {})


@pytest.mark.parametrize(("tool_type", "key", "module", "_model"), _DIRECT_VENDOR_CASES)
def test_validate_direct_vendor_rejects_only_selected_missing_extra(
    monkeypatch, tool_type, key, module, _model
):
    probes = []

    def missing_selected(name):
        probes.append(name)
        if name == module:
            raise importlib.metadata.PackageNotFoundError(name)
        raise AssertionError(f"unselected SDK was probed: {name}")

    monkeypatch.setattr(importlib.metadata, "distribution", missing_selected)
    validator = getattr(legacy_api, "validate_direct_vendor")

    with pytest.raises(ConfigError, match="optional|extra|install"):
        validator(tool_type, {key: "configured"})

    assert probes
    assert probes == [module]


@pytest.mark.parametrize(
    ("tool_type", "_key", "_module", "model"), _DIRECT_VENDOR_CASES
)
@pytest.mark.parametrize("status", [401, 403])
def test_direct_vendor_authentication_errors_are_typed_and_caused(
    monkeypatch, tool_type, _key, _module, model, status
):
    cause = _FakeSDKError("denied", status_code=status)
    adapter = _create_adapter(
        monkeypatch, tool_type, _FakeVendorTool(model, error=cause)
    )

    with pytest.raises(AuthenticationError) as exc_info:
        adapter.complete("prompt")

    assert exc_info.value.__cause__ is cause


@pytest.mark.parametrize(
    ("tool_type", "_key", "_module", "model"), _DIRECT_VENDOR_CASES
)
def test_direct_vendor_timeout_errors_are_typed_and_caused(
    monkeypatch, tool_type, _key, _module, model
):
    cause = TimeoutError("timed out")
    adapter = _create_adapter(
        monkeypatch, tool_type, _FakeVendorTool(model, error=cause)
    )

    with pytest.raises(TransportError) as exc_info:
        adapter.complete("prompt")

    assert exc_info.value.__cause__ is cause


@pytest.mark.parametrize(
    ("tool_type", "_key", "_module", "model"), _DIRECT_VENDOR_CASES
)
@pytest.mark.parametrize("status", [429, 500, 503])
def test_direct_vendor_rate_and_server_errors_are_typed_and_caused(
    monkeypatch, tool_type, _key, _module, model, status
):
    cause = _FakeSDKError("vendor fault", status_code=status)
    adapter = _create_adapter(
        monkeypatch, tool_type, _FakeVendorTool(model, error=cause)
    )

    with pytest.raises(ModelFaultError) as exc_info:
        adapter.complete("prompt")

    assert exc_info.value.__cause__ is cause


@pytest.mark.parametrize(
    ("tool_type", "_key", "_module", "model"), _DIRECT_VENDOR_CASES
)
def test_direct_vendor_unknown_sdk_errors_are_provider_errors_with_cause(
    monkeypatch, tool_type, _key, _module, model
):
    cause = RuntimeError("unexpected SDK failure")
    adapter = _create_adapter(
        monkeypatch, tool_type, _FakeVendorTool(model, error=cause)
    )

    with pytest.raises(ProviderError) as exc_info:
        adapter.complete("prompt")

    assert type(exc_info.value) is ProviderError
    assert exc_info.value.__cause__ is cause


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (HTTPStatus.UNAUTHORIZED, AuthenticationError),
        (HTTPStatus.FORBIDDEN, AuthenticationError),
        (HTTPStatus.TOO_MANY_REQUESTS, ModelFaultError),
        (HTTPStatus.SERVICE_UNAVAILABLE, ModelFaultError),
    ],
)
def test_google_style_code_status_is_mapped(monkeypatch, status, expected):
    class GoogleStyleError(Exception):
        code = status

    cause = GoogleStyleError("google failure")
    adapter = _create_adapter(
        monkeypatch,
        ToolType.GEMINI_API,
        _FakeVendorTool("gemini-test", error=cause),
    )

    with pytest.raises(expected) as exc_info:
        adapter.complete("prompt")

    assert exc_info.value.__cause__ is cause


@pytest.mark.parametrize(
    "cause",
    [
        socket.gaierror("DNS failed"),
        type(
            "DeadlineExceeded",
            (Exception,),
            {"code": HTTPStatus.GATEWAY_TIMEOUT},
        )(),
    ],
)
def test_google_transport_shapes_are_typed_and_caused(monkeypatch, cause):
    adapter = _create_adapter(
        monkeypatch,
        ToolType.GEMINI_API,
        _FakeVendorTool("gemini-test", error=cause),
    )

    with pytest.raises(TransportError) as exc_info:
        adapter.complete("prompt")

    assert exc_info.value.__cause__ is cause
