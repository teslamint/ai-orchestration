"""Legacy direct-vendor API tool adapters, ported verbatim from `api_tools.py`.

These remain available when an existing tool-config file selects a
`*_API` value; the new proxy model slots do not reinterpret them.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import socket
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from pydantic import BaseModel, ValidationError

from ai_orchestration.errors import ConfigError, ProviderError
from ai_orchestration.providers.base import (
    AuthenticationError,
    ModelFaultError,
    ToolType,
    TransportError,
    flatten_json_schema,
)
from ai_orchestration.providers.call_control import (
    AuthorizationGate,
    NoOpAuthorizationGate,
    OutboundPayload,
)
from ai_orchestration.providers.call_events import UsageSink
from ai_orchestration.utils.extract import extract_json_object


@dataclass
class APIResponse:
    """Response from API-based LLM call."""

    content: str
    finish_reason: str
    usage: dict  # {"prompt_tokens": N, "completion_tokens": M}


class APIBasedTool(ABC):
    """Abstract base for API-based LLM tools."""

    model: str = ""
    endpoint: str = ""

    @abstractmethod
    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> APIResponse:
        """Synchronous generation."""

    @abstractmethod
    def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Iterator[str]:
        """Streaming generation yielding content chunks."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if API key is configured."""

    def build_application_payload(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> dict[str, Any]:
        """Build the application-level payload passed to the selected SDK."""
        return {
            "model": self.model,
            "prompt": prompt,
            "system_prompt": system_prompt,
        }

    def generate_application_payload(self, payload: Mapping[str, Any]) -> APIResponse:
        """Generate from the same payload object authorized by the caller."""
        return self.generate(
            str(payload["prompt"]),
            system_prompt=payload.get("system_prompt"),
        )


# Public alias matching the U3 Interfaces contract name.
LegacyAPITool = APIBasedTool


_DIRECT_VENDOR_REQUIREMENTS = {
    ToolType.OPENAI_API: ("OPENAI_API_KEY", "openai"),
    ToolType.ANTHROPIC_API: ("ANTHROPIC_API_KEY", "anthropic"),
    ToolType.GEMINI_API: ("GOOGLE_AI_API_KEY", "google-generativeai"),
}


def validate_direct_vendor(tool_type: ToolType, environ: Mapping[str, str]) -> None:
    """Validate only the selected direct vendor without importing its SDK."""
    requirement = _DIRECT_VENDOR_REQUIREMENTS.get(tool_type)
    if requirement is None:
        raise ConfigError(f"unsupported direct-vendor tool: {tool_type}")
    credential, distribution = requirement
    if not environ.get(credential):
        raise ConfigError(f"{tool_type.value} requires {credential}")
    try:
        importlib.metadata.distribution(distribution)
    except importlib.metadata.PackageNotFoundError:
        raise ConfigError(
            f"{tool_type.value} requires the optional API extra for {distribution}; "
            "install ai-orchestration[api]"
        ) from None


class LegacyProviderAdapter:
    """Expose one legacy SDK tool through the shared Provider contract."""

    def __init__(
        self,
        tool_type: ToolType,
        tool: APIBasedTool,
        *,
        usage_sink: UsageSink | None = None,
        authorization_gate: AuthorizationGate | None = None,
    ) -> None:
        self.tool_type = tool_type
        self.tool = tool
        self.model = tool.model
        self.usage_sink = usage_sink
        self.authorization_gate = authorization_gate or NoOpAuthorizationGate()

    def is_available(self) -> bool:
        return self.tool.is_available()

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        response = self._generate(prompt, system)
        return response.content

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: type[BaseModel],
        system: str | None = None,
    ) -> BaseModel:
        flat_schema = flatten_json_schema(schema.model_json_schema())
        structured_prompt = (
            f"{prompt}\n\nReturn one JSON object matching this schema:\n"
            f"{json.dumps(flat_schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        response = self._generate(structured_prompt, system)
        payload = extract_json_object(response.content)
        if payload is None:
            raise ModelFaultError(
                f"{self.model}: response contained no extractable JSON object"
            )
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            raise ModelFaultError(
                f"{self.model}: extracted JSON failed schema validation ({exc})"
            ) from exc

    def _generate(self, prompt: str, system: str | None) -> APIResponse:
        application_payload = self.tool.build_application_payload(prompt, system)
        self.authorization_gate.authorize(
            OutboundPayload.for_http_json(
                application_payload,
                destination=self.tool.endpoint,
            )
        )
        try:
            response = self.tool.generate_application_payload(application_payload)
        except Exception as exc:
            self._raise_mapped(exc)
        if self.usage_sink is not None:
            self.usage_sink(response.usage or None)
        return response

    def _raise_mapped(self, exc: Exception) -> None:
        status = self._status_code(exc)
        name = type(exc).__name__.lower()
        if status in {401, 403}:
            raise AuthenticationError(f"{self.model}: authentication failed") from exc
        if any(
            marker in name
            for marker in ("authentication", "unauthenticated", "permissiondenied")
        ):
            raise AuthenticationError(f"{self.model}: authentication failed") from exc
        if isinstance(exc, (TimeoutError, ConnectionError, socket.gaierror)) or any(
            marker in name
            for marker in ("timeout", "connection", "connecterror", "deadlineexceeded")
        ):
            raise TransportError(f"{self.model}: vendor transport failed") from exc
        if status == 429 or isinstance(status, int) and status >= 500:
            raise ModelFaultError(f"{self.model}: vendor request failed") from exc
        if any(
            marker in name
            for marker in ("ratelimit", "resourceexhausted", "servererror")
        ):
            raise ModelFaultError(f"{self.model}: vendor request failed") from exc
        raise ProviderError(f"{self.model}: vendor SDK failed") from exc

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is None:
            status = getattr(exc, "code", None)
            if callable(status):
                status = status()
        value = getattr(status, "value", status)
        return value if isinstance(value, int) and not isinstance(value, bool) else None


def create_direct_vendor_provider(
    tool_type: ToolType,
    usage_sink: UsageSink | None = None,
    authorization_gate: AuthorizationGate | None = None,
) -> LegacyProviderAdapter:
    """Validate, construct, and wrap one selected legacy vendor tool."""
    validate_direct_vendor(tool_type, os.environ)
    from ai_orchestration.providers.cli import LLMToolFactory

    tool = LLMToolFactory.create_api_tool(tool_type)
    return LegacyProviderAdapter(
        tool_type,
        tool,
        usage_sink=usage_sink,
        authorization_gate=authorization_gate,
    )


class OpenAITool(APIBasedTool):
    """OpenAI API tool (gpt-4o)."""

    model = "gpt-4o"
    endpoint = "https://api.openai.com/v1"

    def is_available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> APIResponse:
        return self.generate_application_payload(
            self.build_application_payload(prompt, system_prompt)
        )

    def build_application_payload(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> dict[str, Any]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return {"model": self.model, "messages": messages}

    def generate_application_payload(self, payload: Mapping[str, Any]) -> APIResponse:
        from openai import OpenAI

        client = OpenAI(base_url=self.endpoint)
        response = client.chat.completions.create(**dict(payload))
        return APIResponse(
            content=response.choices[0].message.content or "",
            finish_reason=response.choices[0].finish_reason or "stop",
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": (
                    response.usage.completion_tokens if response.usage else 0
                ),
            },
        )

    def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Iterator[str]:
        from openai import OpenAI

        client = OpenAI(base_url=self.endpoint)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        stream = client.chat.completions.create(
            model=self.model, messages=messages, stream=True
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class AnthropicTool(APIBasedTool):
    """Anthropic API tool (Claude Sonnet)."""

    model = "claude-sonnet-4-20250514"
    endpoint = "https://api.anthropic.com"

    def is_available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> APIResponse:
        return self.generate_application_payload(
            self.build_application_payload(prompt, system_prompt)
        )

    def build_application_payload(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt
        return payload

    def generate_application_payload(self, payload: Mapping[str, Any]) -> APIResponse:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(**dict(payload))
        return APIResponse(
            content=response.content[0].text if response.content else "",
            finish_reason=response.stop_reason or "end_turn",
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
            },
        )

    def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Iterator[str]:
        import anthropic

        client = anthropic.Anthropic()
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        with client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text


class GoogleAITool(APIBasedTool):
    """Google AI API tool (Gemini)."""

    model = "gemini-2.0-flash"
    endpoint = "https://generativelanguage.googleapis.com"

    def is_available(self) -> bool:
        return bool(os.environ.get("GOOGLE_AI_API_KEY"))

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> APIResponse:
        return self.generate_application_payload(
            self.build_application_payload(prompt, system_prompt)
        )

    def build_application_payload(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "system_instruction": system_prompt,
            "contents": prompt,
        }

    def generate_application_payload(self, payload: Mapping[str, Any]) -> APIResponse:
        import google.generativeai as genai

        genai.configure(api_key=os.environ.get("GOOGLE_AI_API_KEY"))
        model = genai.GenerativeModel(
            payload["model"], system_instruction=payload.get("system_instruction")
        )
        response = model.generate_content(payload["contents"])
        return APIResponse(
            content=response.text,
            finish_reason="stop",
            usage={
                "prompt_tokens": (
                    response.usage_metadata.prompt_token_count
                    if response.usage_metadata
                    else 0
                ),
                "completion_tokens": (
                    response.usage_metadata.candidates_token_count
                    if response.usage_metadata
                    else 0
                ),
            },
        )

    def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Iterator[str]:
        import google.generativeai as genai

        genai.configure(api_key=os.environ.get("GOOGLE_AI_API_KEY"))
        model = genai.GenerativeModel(self.model, system_instruction=system_prompt)
        response = model.generate_content(prompt, stream=True)
        for chunk in response:
            if chunk.text:
                yield chunk.text
