"""Configuration contracts: per-stage routing, provider endpoint, and workspace anchor.

U1 defines slot-kind validation and an injectable ``CatalogStatus`` boundary.
It never calls the network; ``resolve_stage_config`` is exercised exclusively
against a caller-supplied ``CatalogStatus``. U3 is the only unit that
implements ``probe_catalog()`` and performs the real HTTP request.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from ai_orchestration.errors import ConfigError, RoutingError

DEFAULT_PROXY_BASE_URL = "https://cliproxyapi.tailnet-0a4d.ts.net:8317/v1"
PROXY_API_KEY_ENV_VAR = "CLIPROXYAPI_KEY"
WORKSPACE_ENV_VAR = "ORCHESTRATOR_WORKSPACE"

STAGE_NAMES: tuple[str, ...] = (
    "brainstormer",
    "reviewer",
    "planner",
    "executor",
    "code_reviewer",
    "fixer",
)

# Exact CLI binary names. Any other slot value is a proxy model id.
_KNOWN_BINARIES: frozenset[str] = frozenset({"agy", "codex", "claude"})


class SlotKind(str, Enum):
    """How a resolved slot value must be validated."""

    BINARY = "binary"
    PROXY_MODEL = "proxy_model"


def classify_slot(value: str) -> SlotKind:
    """Classify a ``model`` or ``fallback_binary`` value.

    An exact CLI binary name selects the subprocess provider; every other
    value is treated as a proxy model id. This never guesses: the check is
    membership in the known-binary set, nothing fuzzier.
    """
    return SlotKind.BINARY if value in _KNOWN_BINARIES else SlotKind.PROXY_MODEL


class CatalogOutcome(str, Enum):
    """Result shape returned by U3's ``probe_catalog()``."""

    REACHABLE_WITH_MODELS = "reachable_with_models"
    REACHABLE_WITHOUT_ID = "reachable_without_id"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class CatalogStatus:
    """Injectable catalog-probe result consumed by ``resolve_stage_config``.

    ``models`` is only meaningful when ``outcome`` is
    ``REACHABLE_WITH_MODELS``. U1 never constructs this from a live request;
    callers (tests today, U3's ``probe_catalog()`` after U3 lands) supply it.
    """

    outcome: CatalogOutcome
    models: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class StageConfig:
    """Resolved routing for one pipeline stage."""

    model: str
    fallback_model: Optional[str] = None
    fallback_binary: Optional[str] = None

    @classmethod
    def from_raw(cls, raw: object, *, stage_name: str) -> "StageConfig":
        """Parse a tool-config JSON value: a bare string or a stage object."""
        if isinstance(raw, str):
            return cls(model=raw)
        if isinstance(raw, dict):
            if "model" not in raw:
                raise ConfigError(
                    f"{stage_name}: config stage object is missing the required "
                    "'model' key"
                )
            model = raw["model"]
            if not isinstance(model, str):
                raise ConfigError(f"{stage_name}: 'model' must be a string")
            fallback_model = raw.get("fallback_model")
            if fallback_model is not None and not isinstance(fallback_model, str):
                raise ConfigError(f"{stage_name}: 'fallback_model' must be a string")
            fallback_binary = raw.get("fallback_binary")
            if fallback_binary is not None and not isinstance(fallback_binary, str):
                raise ConfigError(f"{stage_name}: 'fallback_binary' must be a string")
            return cls(
                model=model,
                fallback_model=fallback_model,
                fallback_binary=fallback_binary,
            )
        raise ConfigError(
            f"{stage_name}: stage config must be a string or an object, got "
            f"{type(raw).__name__}"
        )


DEFAULT_STAGE_TABLE: dict[str, StageConfig] = {
    "brainstormer": StageConfig(model="gemini-3.1-pro-low", fallback_binary="agy"),
    "reviewer": StageConfig(model="gpt-5.5", fallback_binary="codex"),
    "planner": StageConfig(model="gpt-5.5", fallback_binary="codex"),
    "executor": StageConfig(model="claude-sonnet-5", fallback_binary="claude"),
    "code_reviewer": StageConfig(model="gpt-5.5", fallback_binary="codex"),
    "fixer": StageConfig(model="claude-sonnet-5", fallback_binary="claude"),
}


def _default_binary_exists(binary: str) -> bool:
    return shutil.which(binary) is not None


def _validate_binary_slot(
    stage_name: str,
    slot_name: str,
    binary: str,
    binary_exists: Callable[[str], bool],
) -> None:
    if not binary_exists(binary):
        raise RoutingError(
            f"{stage_name}: {slot_name} binary '{binary}' not found on PATH"
        )


def _validate_proxy_slot(
    stage_name: str,
    slot_name: str,
    model_id: str,
    catalog: CatalogStatus,
) -> None:
    if catalog.outcome is CatalogOutcome.UNREACHABLE:
        # S5: an unreachable catalog is skipped, not failed, so the offline
        # fallback path stays reachable.
        return
    if catalog.outcome is CatalogOutcome.REACHABLE_WITHOUT_ID:
        # The endpoint answered but returned no enumerable model list;
        # membership cannot be verified, so this is skipped like UNREACHABLE.
        return
    if model_id not in catalog.models:
        raise RoutingError(
            f"{stage_name}: {slot_name} '{model_id}' not found in reachable catalog"
        )


def resolve_stage_config(
    stage_name: str,
    *,
    cli_value: Optional[str] = None,
    file_stages: Optional[Mapping[str, object]] = None,
    catalog: CatalogStatus,
    binary_exists: Optional[Callable[[str], bool]] = None,
) -> StageConfig:
    """Resolve one stage's routing and validate every populated slot.

    Precedence, highest first: ``cli_value``, then ``file_stages``, then the
    built-in default table. Validation runs on the resolved value regardless
    of its source: a binary-valued slot is checked with ``binary_exists``
    (PATH lookup, defaulting to ``shutil.which``); a proxy-valued slot is
    checked against ``catalog``, which is never contacted here.
    """
    if stage_name not in STAGE_NAMES:
        raise ConfigError(f"unknown stage: {stage_name!r}")

    file_stages = file_stages or {}
    resolved_binary_exists = binary_exists or _default_binary_exists

    uses_default = cli_value is None and stage_name not in file_stages
    if cli_value is not None:
        stage = StageConfig.from_raw(cli_value, stage_name=stage_name)
    elif stage_name in file_stages:
        stage = StageConfig.from_raw(file_stages[stage_name], stage_name=stage_name)
    else:
        stage = DEFAULT_STAGE_TABLE[stage_name]

    if classify_slot(stage.model) is SlotKind.BINARY:
        _validate_binary_slot(stage_name, "model", stage.model, resolved_binary_exists)
    else:
        _validate_proxy_slot(stage_name, "model", stage.model, catalog)

    if stage.fallback_model is not None:
        # fallback_model is same-endpoint by definition (§Stage resolution):
        # always a proxy id, never classified by binary-name pattern.
        if stage.fallback_model == stage.model:
            raise ConfigError(
                f"{stage_name}: fallback_model '{stage.fallback_model}' is "
                "identical to model; a real model fault would retry the "
                "same failing model"
            )
        _validate_proxy_slot(
            stage_name, "fallback_model", stage.fallback_model, catalog
        )

    if stage.fallback_binary is not None:
        if stage.fallback_binary not in _KNOWN_BINARIES:
            raise ConfigError(
                f"{stage_name}: fallback_binary '{stage.fallback_binary}' is "
                f"not a supported CLI binary (must be one of "
                f"{sorted(_KNOWN_BINARIES)})"
            )
        if not uses_default:
            _validate_binary_slot(
                stage_name,
                "fallback_binary",
                stage.fallback_binary,
                resolved_binary_exists,
            )

    return stage


@dataclass(frozen=True)
class ProviderConfig:
    """CLIProxyAPI endpoint configuration."""

    base_url: str = DEFAULT_PROXY_BASE_URL
    api_key: Optional[str] = None

    @classmethod
    def from_raw(cls, raw: Optional[Mapping[str, object]]) -> "ProviderConfig":
        if raw is None:
            raw = {}
        elif not isinstance(raw, Mapping):
            raise ConfigError("provider config must be an object")
        if "base_url" in raw:
            base_url = raw["base_url"]
            if not isinstance(base_url, str):
                raise ConfigError("provider config: 'base_url' must be a string")
        else:
            base_url = DEFAULT_PROXY_BASE_URL

        if "api_key" in raw:
            api_key = raw["api_key"]
            if not isinstance(api_key, str):
                raise ConfigError("provider config: 'api_key' must be a string")
        else:
            api_key = os.environ.get(PROXY_API_KEY_ENV_VAR)

        return cls(base_url=base_url, api_key=api_key)


def resolve_workspace_base(
    workspace_flag: Optional[str], env: Mapping[str, str]
) -> Path:
    """Resolve the workspace anchor: ``--workspace``, else env var, else cwd.

    Precedence, highest first: the ``--workspace`` flag, then
    ``ORCHESTRATOR_WORKSPACE``, then the current working directory's
    ``workspace/``. Absolute paths are used as-is.
    """
    if workspace_flag is not None:
        return Path(workspace_flag)
    if WORKSPACE_ENV_VAR in env:
        return Path(env[WORKSPACE_ENV_VAR])
    return Path.cwd() / "workspace"


@dataclass
class OrchestratorConfig:
    """Run-level configuration assembled by the CLI (wired in U5)."""

    workspace_path: Path = field(default_factory=lambda: Path("./workspace"))
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    stages: dict[str, StageConfig] = field(
        default_factory=lambda: dict(DEFAULT_STAGE_TABLE)
    )
    auto_approve: bool = False
    auto_run: bool = False
    auto_fix: bool = False
    auto_select: bool = False
    skip_review: bool = False
    max_fix_iterations: int = 1
    debug: bool = False
    debug_log_path: Optional[Path] = None
    enable_ralph_wiggum: bool = False
    ralph_wiggum_threshold: float = 0.8
    ralph_wiggum_max_iterations: int = 3
    ralph_wiggum_completion_promise: Optional[str] = None
    ralph_wiggum_state_file: bool = True
