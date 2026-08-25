"""Provider layer: HTTP (CLIProxyAPI), CLI subprocess, and legacy API adapters."""

from ai_orchestration.providers.base import (
    AuthenticationError,
    CLIProviderError,
    ModelFaultError,
    Provider,
    ProviderResult,
    StageRole,
    ToolType,
    TransportError,
    flatten_json_schema,
)
from ai_orchestration.providers.cli import (
    AgyProvider,
    ClaudeProvider,
    CodexProvider,
    LLMToolConfig,
    LLMToolFactory,
    load_tool_config,
    validate_tool_config,
)
from ai_orchestration.providers.http import HttpProvider, probe_catalog
from ai_orchestration.providers.legacy_api import (
    AnthropicTool,
    APIResponse,
    GoogleAITool,
    LegacyAPITool,
    OpenAITool,
)
from ai_orchestration.providers.routing import (
    complete_structured_with_fallback,
    complete_with_fallback,
    resolve_provider_chain,
)

__all__ = [
    "AgyProvider",
    "AnthropicTool",
    "APIResponse",
    "AuthenticationError",
    "CLIProviderError",
    "ClaudeProvider",
    "CodexProvider",
    "GoogleAITool",
    "HttpProvider",
    "LegacyAPITool",
    "LLMToolConfig",
    "LLMToolFactory",
    "ModelFaultError",
    "OpenAITool",
    "Provider",
    "ProviderResult",
    "StageRole",
    "ToolType",
    "TransportError",
    "complete_structured_with_fallback",
    "complete_with_fallback",
    "flatten_json_schema",
    "load_tool_config",
    "probe_catalog",
    "resolve_provider_chain",
    "validate_tool_config",
]
