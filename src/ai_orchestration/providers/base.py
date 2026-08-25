"""Provider protocol, result envelope, typed failures, and enum surface.

`ToolType` and `StageRole` are the legacy-compatible enum surface: identical
values except the approved `GEMINI` -> `AGY` rename (A12). `Provider` is the
shared runtime interface every transport (HTTP, agy, codex, claude)
satisfies so stages never branch on transport (decision 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from ai_orchestration.errors import ProviderError


class ToolType(str, Enum):
    """Supported provider types. `GEMINI` is retired; use `AGY` (A12)."""

    # CLI-based providers
    AGY = "agy"
    CODEX = "codex"
    CLAUDE = "claude"
    # API-based providers (legacy direct-vendor adapters)
    GEMINI_API = "gemini_api"
    OPENAI_API = "openai_api"
    ANTHROPIC_API = "anthropic_api"


class StageRole(str, Enum):
    """Stage roles in the orchestration pipeline."""

    BRAINSTORMER = "brainstormer"
    REVIEWER = "reviewer"
    PLANNER = "planner"
    EXECUTOR = "executor"
    CODE_REVIEWER = "code_reviewer"
    FIXER = "fixer"


class TransportError(ProviderError):
    """Endpoint-level failure: connection refused, DNS, timeout.

    Skips `fallback_model` (it shares the dead transport) and goes straight
    to `fallback_binary` (S5).
    """


class AuthenticationError(ProviderError):
    """401/403: a credential fault. Never falls back to anything."""


class ModelFaultError(ProviderError):
    """429/5xx/unusable output while the endpoint is healthy.

    Retries via `fallback_model` on the same endpoint if configured (S6);
    otherwise fails the stage.
    """


class CLIProviderError(ProviderError):
    """A CLI subprocess attempt failed. Always terminal: no onward fallback."""


@dataclass(frozen=True)
class ProviderResult:
    """Envelope for a completed provider call."""

    content: str
    provider_name: str
    used_structured_path: bool = False
    raw: Any = field(default=None, repr=False)


@runtime_checkable
class Provider(Protocol):
    """Shared runtime interface for every transport."""

    def complete(self, prompt: str, *, system: str | None = None) -> str: ...

    def complete_structured(
        self, prompt: str, *, schema: type[BaseModel]
    ) -> BaseModel: ...

    def is_available(self) -> bool: ...


def flatten_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline every `$ref`/`$defs` indirection into a flat JSON schema.

    Per decision 3, schemas are emitted flat because `gemini-3.1-pro-low`
    fails on the SDK's `$ref`/`$defs` derived form. Pydantic's
    `model_json_schema()` emits `$defs` for any nested model; this resolves
    every `$ref: "#/$defs/Name"` by substituting the referenced definition
    inline, recursively, and drops the `$defs` key from the result.
    """
    defs = schema.get("$defs", {})

    def _resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                def_name = node["$ref"].removeprefix("#/$defs/")
                resolved = _resolve(defs[def_name])
                # Merge any sibling keys (e.g. a description) over the def.
                merged = dict(resolved)
                for key, value in node.items():
                    if key != "$ref":
                        merged[key] = value
                return merged
            return {key: _resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    flat = _resolve({k: v for k, v in schema.items() if k != "$defs"})
    return flat
