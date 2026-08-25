"""Configuration contracts for ai_orchestration (U1).

Covers StageConfig/ProviderConfig parsing, resolve_stage_config precedence and
slot-kind validation against an injected CatalogStatus (never the network), and
resolve_workspace_base precedence.
"""

import pytest

from ai_orchestration.config import (
    DEFAULT_STAGE_TABLE,
    STAGE_NAMES,
    CatalogOutcome,
    CatalogStatus,
    ProviderConfig,
    SlotKind,
    StageConfig,
    classify_slot,
    resolve_stage_config,
    resolve_workspace_base,
)
from ai_orchestration.errors import ConfigError, RoutingError

UNREACHABLE = CatalogStatus(outcome=CatalogOutcome.UNREACHABLE)


def reachable(*models: str) -> CatalogStatus:
    return CatalogStatus(
        outcome=CatalogOutcome.REACHABLE_WITH_MODELS, models=frozenset(models)
    )


# --- StageConfig parsing -----------------------------------------------------


def test_stage_config_from_bare_string_shorthand():
    stage = StageConfig.from_raw("gpt-5.5", stage_name="planner")
    assert stage == StageConfig(model="gpt-5.5")
    assert stage.fallback_model is None
    assert stage.fallback_binary is None


def test_stage_config_from_full_object():
    raw = {"model": "opus-5", "fallback_model": "gpt-5.5", "fallback_binary": "codex"}
    stage = StageConfig.from_raw(raw, stage_name="planner")
    assert stage == StageConfig(
        model="opus-5", fallback_model="gpt-5.5", fallback_binary="codex"
    )


def test_stage_config_object_missing_model_key_raises_config_error():
    with pytest.raises(ConfigError, match="planner"):
        StageConfig.from_raw({"fallback_model": "gpt-5.5"}, stage_name="planner")


def test_stage_config_wrong_shape_raises_config_error():
    with pytest.raises(ConfigError, match="planner"):
        StageConfig.from_raw(123, stage_name="planner")


def test_stage_config_non_string_model_raises_config_error():
    with pytest.raises(ConfigError):
        StageConfig.from_raw({"model": 5}, stage_name="planner")


# --- classify_slot ------------------------------------------------------------


@pytest.mark.parametrize("binary", ["agy", "codex", "claude"])
def test_classify_slot_known_binaries(binary):
    assert classify_slot(binary) is SlotKind.BINARY


@pytest.mark.parametrize("model_id", ["gpt-5.5", "gemini-3.1-pro-low", "opus-5"])
def test_classify_slot_proxy_model_ids(model_id):
    assert classify_slot(model_id) is SlotKind.PROXY_MODEL


# --- resolve_stage_config precedence ------------------------------------------


def test_resolve_stage_config_cli_flag_overrides_config_and_default():
    result = resolve_stage_config(
        "planner",
        cli_value="cli-model",
        file_stages={"planner": {"model": "file-model"}},
        catalog=UNREACHABLE,
    )
    assert result.model == "cli-model"


def test_resolve_stage_config_file_overrides_built_in_default():
    result = resolve_stage_config(
        "planner",
        cli_value=None,
        file_stages={"planner": "file-model"},
        catalog=UNREACHABLE,
    )
    assert result.model == "file-model"


def test_resolve_stage_config_falls_back_to_built_in_default():
    result = resolve_stage_config(
        "planner", cli_value=None, file_stages={}, catalog=UNREACHABLE
    )
    assert result == DEFAULT_STAGE_TABLE["planner"]


def test_resolve_stage_config_unknown_stage_raises_config_error():
    with pytest.raises(ConfigError, match="unknown stage"):
        resolve_stage_config(
            "not_a_stage", cli_value=None, file_stages={}, catalog=UNREACHABLE
        )


def test_default_table_covers_all_six_stages():
    assert set(DEFAULT_STAGE_TABLE) == set(STAGE_NAMES)
    assert len(STAGE_NAMES) == 6


def test_default_stage_missing_fallback_model_is_none():
    # Edge: no stage ships a default fallback_model; only fallback_binary.
    for name in STAGE_NAMES:
        assert DEFAULT_STAGE_TABLE[name].fallback_model is None


# --- resolve_stage_config slot-kind validation (injected catalog only) -------


def test_resolve_stage_config_rejects_unknown_proxy_id_on_reachable_catalog():
    with pytest.raises(RoutingError, match="planner"):
        resolve_stage_config(
            "planner",
            cli_value=None,
            file_stages={"planner": "not-a-real-model"},
            catalog=reachable("gpt-5.5", "opus-5"),
        )


def test_resolve_stage_config_accepts_known_proxy_id_on_reachable_catalog():
    result = resolve_stage_config(
        "planner",
        cli_value=None,
        file_stages={"planner": "gpt-5.5"},
        catalog=reachable("gpt-5.5", "opus-5"),
    )
    assert result.model == "gpt-5.5"


def test_resolve_stage_config_skips_validation_when_catalog_unreachable():
    # S5: an unreachable catalog is skipped, not failed, so the offline path
    # (and this test) never performs network I/O.
    result = resolve_stage_config(
        "planner",
        cli_value=None,
        file_stages={"planner": "some-unverified-id"},
        catalog=UNREACHABLE,
    )
    assert result.model == "some-unverified-id"


def test_resolve_stage_config_binary_model_never_checked_against_catalog():
    # Acceptance: "no binary name is sent to the model catalog." An empty
    # reachable catalog would reject any proxy id, but a binary-valued model
    # must resolve because it is validated via PATH, not /v1/models.
    result = resolve_stage_config(
        "planner",
        cli_value=None,
        file_stages={"planner": "codex"},
        catalog=reachable(),
        binary_exists=lambda _name: True,
    )
    assert result.model == "codex"


def test_resolve_stage_config_rejects_binary_not_on_path():
    with pytest.raises(RoutingError, match="codex"):
        resolve_stage_config(
            "planner",
            cli_value=None,
            file_stages={"planner": "codex"},
            catalog=UNREACHABLE,
            binary_exists=lambda _name: False,
        )


def test_resolve_stage_config_rejects_fallback_binary_not_on_path():
    with pytest.raises(RoutingError, match="fallback_binary"):
        resolve_stage_config(
            "planner",
            cli_value=None,
            file_stages={"planner": {"model": "gpt-5.5", "fallback_binary": "codex"}},
            catalog=UNREACHABLE,
            binary_exists=lambda _name: False,
        )


def test_resolve_stage_config_fallback_model_is_always_a_proxy_id():
    # fallback_model is same-endpoint by definition; it is checked against the
    # catalog even when its value happens to collide with a known binary name.
    with pytest.raises(RoutingError, match="fallback_model"):
        resolve_stage_config(
            "planner",
            cli_value=None,
            file_stages={"planner": {"model": "gpt-5.5", "fallback_model": "codex"}},
            catalog=reachable("gpt-5.5"),
        )


def test_resolve_stage_config_malformed_file_entry_raises_config_error():
    with pytest.raises(ConfigError, match="planner"):
        resolve_stage_config(
            "planner",
            cli_value=None,
            file_stages={"planner": ["not", "a", "valid", "shape"]},
            catalog=UNREACHABLE,
        )


# --- ProviderConfig -------------------------------------------------------


def test_provider_config_defaults_and_env_api_key(monkeypatch):
    monkeypatch.setenv("CLIPROXYAPI_KEY", "secret-key")
    config = ProviderConfig.from_raw(None)
    assert config.base_url.startswith("https://")
    assert config.api_key == "secret-key"


def test_provider_config_file_overrides_base_url():
    config = ProviderConfig.from_raw({"base_url": "http://127.0.0.1:9"})
    assert config.base_url == "http://127.0.0.1:9"


def test_provider_config_file_api_key_overrides_env(monkeypatch):
    monkeypatch.setenv("CLIPROXYAPI_KEY", "env-key")
    config = ProviderConfig.from_raw({"api_key": "file-key"})
    assert config.api_key == "file-key"


def test_provider_config_malformed_base_url_raises_config_error():
    with pytest.raises(ConfigError):
        ProviderConfig.from_raw({"base_url": 5})


# --- resolve_workspace_base precedence ----------------------------------------


def test_resolve_workspace_base_uses_workspace_flag_first():
    result = resolve_workspace_base(
        workspace_flag="./explicit", env={"ORCHESTRATOR_WORKSPACE": "/tmp/env-anchor"}
    )
    assert result == __import__("pathlib").Path("./explicit")


def test_resolve_workspace_base_falls_back_to_env_var():
    result = resolve_workspace_base(
        workspace_flag=None, env={"ORCHESTRATOR_WORKSPACE": "/tmp/env-anchor"}
    )
    assert result == __import__("pathlib").Path("/tmp/env-anchor")


def test_resolve_workspace_base_falls_back_to_cwd_workspace():
    from pathlib import Path

    result = resolve_workspace_base(workspace_flag=None, env={})
    assert result == Path.cwd() / "workspace"


def test_resolve_workspace_base_absolute_flag_remains_absolute():
    from pathlib import Path

    result = resolve_workspace_base(workspace_flag="/abs/anchor", env={})
    assert result == Path("/abs/anchor")
    assert result.is_absolute()
