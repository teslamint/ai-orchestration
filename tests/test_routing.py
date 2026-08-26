"""Provider-chain resolution and fallback routing tests (U3).

Covers S5 (endpoint unreachable -> fallback_binary), S6 (model fault ->
fallback_model), and the failure-class table's routing decisions: which
failures skip fallback_model entirely, which retry it, and which never fall
back at all.
"""

import httpx
import pytest
from pydantic import BaseModel

from ai_orchestration.config import CatalogOutcome, CatalogStatus, StageConfig
from ai_orchestration.errors import ProviderError
from ai_orchestration.providers.base import (
    AuthenticationError,
    ModelFaultError,
    TransportError,
)
from ai_orchestration.providers.http import HttpProvider, probe_catalog
from ai_orchestration.providers.routing import (
    complete_structured_with_fallback,
    complete_with_fallback,
    resolve_provider_chain,
)


class _Plan(BaseModel):
    step_id: int


# --- probe_catalog ------------------------------------------------------------


def test_probe_catalog_reachable_with_models():
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "gpt-5.5"}, {"id": "opus-5"}]})

    status = probe_catalog(
        "http://stub/v1", "k", transport=httpx.MockTransport(handler)
    )
    assert status.outcome is CatalogOutcome.REACHABLE_WITH_MODELS
    assert status.models == frozenset({"gpt-5.5", "opus-5"})


def test_probe_catalog_reachable_without_id_when_data_key_missing():
    def handler(request):
        return httpx.Response(200, json={})

    status = probe_catalog(
        "http://stub/v1", "k", transport=httpx.MockTransport(handler)
    )
    assert status.outcome is CatalogOutcome.REACHABLE_WITHOUT_ID


def test_probe_catalog_unreachable_on_connection_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    status = probe_catalog(
        "http://stub/v1", "k", transport=httpx.MockTransport(handler)
    )
    assert status.outcome is CatalogOutcome.UNREACHABLE


# --- resolve_provider_chain: static resolution ------------------------------


def test_resolve_provider_chain_binary_model_returns_cli_provider():
    provider = resolve_provider_chain(
        StageConfig(model="claude"),
        catalog=CatalogStatus(outcome=CatalogOutcome.UNREACHABLE),
        base_url="http://stub/v1",
        api_key="k",
    )
    assert provider.get_binary_path().endswith("claude")


def test_resolve_provider_chain_proxy_model_returns_http_provider():
    provider = resolve_provider_chain(
        StageConfig(model="gpt-5.5"),
        catalog=CatalogStatus(
            outcome=CatalogOutcome.REACHABLE_WITH_MODELS, models=frozenset({"gpt-5.5"})
        ),
        base_url="http://stub/v1",
        api_key="k",
    )
    assert isinstance(provider, HttpProvider)
    assert provider.model == "gpt-5.5"


# --- complete_with_fallback: routing decisions ------------------------------


class _StubCLI:
    def __init__(self, binary, output=None, error=None):
        self.binary = binary
        self.output = output
        self.error = error

    def complete(self, prompt, **kwargs):
        if self.error is not None:
            raise self.error
        return self.output

    def is_available(self):
        return True


class _StubHttp:
    """A scriptable stand-in implementing the Provider protocol's HTTP side."""

    def __init__(self, model, script):
        self.model = model
        self._script = script  # list of (result_or_exception,)

    def complete(self, prompt, **kwargs):
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def complete_structured(self, prompt, *, schema, **kwargs):
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_complete_with_fallback_endpoint_unreachable_skips_fallback_model_uses_binary():
    # S5: connection refused is endpoint-level; fallback_model must never be
    # tried because it shares the dead transport.
    attempted_models = []

    def http_factory(model):
        attempted_models.append(model)
        return _StubHttp(model, [TransportError("refused")])

    cli_factory_calls = []

    def cli_factory(binary):
        cli_factory_calls.append(binary)
        return _StubCLI(binary, output="cli fallback output")

    stage = StageConfig(
        model="gpt-5.5", fallback_model="gpt-4o-mini", fallback_binary="codex"
    )
    result, provider_used = complete_with_fallback(
        stage,
        "prompt",
        http_provider_factory=http_factory,
        cli_provider_factory=cli_factory,
    )
    assert result == "cli fallback output"
    assert provider_used == "codex"
    assert attempted_models == ["gpt-5.5"]  # fallback_model never attempted
    assert cli_factory_calls == ["codex"]


def test_complete_with_fallback_429_uses_fallback_model_not_cli():
    # S6: model-level fault (429) retries on the same endpoint via
    # fallback_model; the CLI provider must never be invoked.
    attempted_models = []

    def http_factory(model):
        attempted_models.append(model)
        if model == "gpt-5.5":
            return _StubHttp(model, [ModelFaultError("429")])
        return _StubHttp(model, ["fallback model output"])

    cli_factory_calls = []

    def cli_factory(binary):
        cli_factory_calls.append(binary)
        return _StubCLI(binary, output="should not be used")

    stage = StageConfig(
        model="gpt-5.5", fallback_model="gpt-4o-mini", fallback_binary="codex"
    )
    result, provider_used = complete_with_fallback(
        stage,
        "prompt",
        http_provider_factory=http_factory,
        cli_provider_factory=cli_factory,
    )
    assert result == "fallback model output"
    assert provider_used == "gpt-4o-mini"
    assert attempted_models == ["gpt-5.5", "gpt-4o-mini"]
    assert cli_factory_calls == []  # CLI never invoked


def test_complete_with_fallback_no_fallback_model_fails_stage_on_429():
    def http_factory(model):
        return _StubHttp(model, [ModelFaultError("429")])

    stage = StageConfig(model="gpt-5.5")  # no fallback_model configured
    with pytest.raises(ProviderError):
        complete_with_fallback(
            stage,
            "prompt",
            http_provider_factory=http_factory,
            cli_provider_factory=lambda binary: _StubCLI(binary, output="unused"),
        )


def test_complete_with_fallback_401_fails_without_any_fallback():
    # A credential fault is not fixed by another model or a subprocess.
    http_calls = []
    cli_calls = []

    def http_factory(model):
        http_calls.append(model)
        return _StubHttp(model, [AuthenticationError("401")])

    def cli_factory(binary):
        cli_calls.append(binary)
        return _StubCLI(binary, output="unused")

    stage = StageConfig(
        model="gpt-5.5", fallback_model="gpt-4o-mini", fallback_binary="codex"
    )
    with pytest.raises(ProviderError):
        complete_with_fallback(
            stage,
            "prompt",
            http_provider_factory=http_factory,
            cli_provider_factory=cli_factory,
        )
    assert http_calls == ["gpt-5.5"]  # no retry against fallback_model
    assert cli_calls == []  # no CLI fallback either


def test_complete_with_fallback_cli_fallback_never_escalates_back_to_proxy():
    # "in particular a failed fallback_binary never escalates back to the
    # proxy that was already unreachable."
    http_calls = []

    def http_factory(model):
        http_calls.append(model)
        return _StubHttp(model, [TransportError("refused")])

    def cli_factory(binary):
        return _StubCLI(binary, error=RuntimeError(f"{binary} exited nonzero"))

    stage = StageConfig(model="gpt-5.5", fallback_binary="codex")
    with pytest.raises(Exception):
        complete_with_fallback(
            stage,
            "prompt",
            http_provider_factory=http_factory,
            cli_provider_factory=cli_factory,
        )
    assert http_calls == [
        "gpt-5.5"
    ]  # exactly one proxy attempt, no retry after CLI fails


def test_complete_with_fallback_missing_fallback_binary_fails_stage():
    # Startup validation (U1) should have already caught this, but the
    # routing layer must not silently succeed if it somehow gets here.
    def http_factory(model):
        return _StubHttp(model, [TransportError("refused")])

    stage = StageConfig(model="gpt-5.5", fallback_binary="codex")
    with pytest.raises(Exception):
        complete_with_fallback(
            stage,
            "prompt",
            http_provider_factory=http_factory,
            cli_provider_factory=lambda binary: _StubCLI(
                binary, error=RuntimeError("codex: binary not found on PATH")
            ),
        )


# --- complete_structured_with_fallback --------------------------------------


def test_complete_structured_with_fallback_falls_back_on_unusable_output():
    attempts = []

    def http_factory(model):
        attempts.append(model)
        if model == "gpt-5.5":
            return _StubHttp(model, [ModelFaultError("no extractable JSON")])
        return _StubHttp(model, [_Plan(step_id=9)])

    result, provider_used = complete_structured_with_fallback(
        StageConfig(model="gpt-5.5", fallback_model="gpt-4o-mini"),
        "prompt",
        schema=_Plan,
        http_provider_factory=http_factory,
        cli_provider_factory=lambda binary: _StubCLI(binary, output="unused"),
    )
    assert result == _Plan(step_id=9)
    assert provider_used == "gpt-4o-mini"
    assert attempts == ["gpt-5.5", "gpt-4o-mini"]


def test_complete_structured_with_fallback_binary_model_uses_cli_complete_structured():
    class _StubCLIStructured(_StubCLI):
        def complete_structured(self, prompt, *, schema, **kwargs):
            return schema(step_id=42)

    result, provider_used = complete_structured_with_fallback(
        StageConfig(model="codex"),
        "prompt",
        schema=_Plan,
        http_provider_factory=lambda model: _StubHttp(model, []),
        cli_provider_factory=lambda binary: _StubCLIStructured(binary),
    )
    assert result == _Plan(step_id=42)
    assert provider_used == "codex"


# --- HTTP 200 + malformed JSON body (finding #6) ----------------------------


def test_http_provider_complete_malformed_json_body_raises_model_fault_error():
    def handler(request):
        return httpx.Response(
            200, content=b"not json{{{", headers={"content-type": "application/json"}
        )

    provider = HttpProvider(
        model="x",
        base_url="http://stub/v1",
        api_key="k",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    with pytest.raises(ModelFaultError):
        provider.complete("hi")


def test_http_provider_complete_structured_malformed_json_body_raises_model_fault_error():
    def handler(request):
        return httpx.Response(
            200, content=b"not json{{{", headers={"content-type": "application/json"}
        )

    provider = HttpProvider(
        model="x",
        base_url="http://stub/v1",
        api_key="k",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    with pytest.raises(ModelFaultError):
        provider.complete_structured("hi", schema=_Plan)


# --- Default HTTP timeout wiring (finding #10) ------------------------------


def test_http_provider_default_timeout_is_wired():
    from ai_orchestration.providers.http import DEFAULT_HTTP_TIMEOUT_SECONDS

    provider = HttpProvider(model="x", base_url="http://stub/v1", api_key="k")
    assert provider.timeout == DEFAULT_HTTP_TIMEOUT_SECONDS
    assert DEFAULT_HTTP_TIMEOUT_SECONDS is not None
    assert DEFAULT_HTTP_TIMEOUT_SECONDS > 0


def test_http_provider_timeout_reaches_openai_client(monkeypatch):
    captured = {}

    class _SpyOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import ai_orchestration.providers.http as http_module

    monkeypatch.setattr(http_module, "OpenAI", _SpyOpenAI)
    HttpProvider(model="x", base_url="http://stub/v1", api_key="k", timeout=42.0)
    assert captured.get("timeout") == 42.0


# --- Chained diagnostics naming both model/binary on double failure (#23) --


def test_complete_with_fallback_names_both_diagnostics_and_chains_cause():
    class _AlwaysFaulty:
        def __init__(self, model):
            self.model = model

        def complete(self, prompt, **kwargs):
            raise ModelFaultError(f"{self.model}: HTTP 429")

    stage = StageConfig(model="primary-fault-model", fallback_model="fallback-model")
    with pytest.raises(ProviderError) as exc_info:
        complete_with_fallback(
            stage,
            "prompt",
            http_provider_factory=_AlwaysFaulty,
            cli_provider_factory=lambda b: _StubCLI(b, output="unused"),
        )
    message = str(exc_info.value)
    assert "primary-fault-model" in message
    assert "fallback-model" in message
    assert exc_info.value.__cause__ is not None


def test_complete_structured_with_fallback_names_both_diagnostics_and_chains_cause():
    class _AlwaysFaultyStructured:
        def __init__(self, model):
            self.model = model

        def complete_structured(self, prompt, *, schema, **kwargs):
            raise ModelFaultError(f"{self.model}: HTTP 429")

    stage = StageConfig(model="primary-fault-model", fallback_model="fallback-model")
    with pytest.raises(ProviderError) as exc_info:
        complete_structured_with_fallback(
            stage,
            "prompt",
            schema=_Plan,
            http_provider_factory=_AlwaysFaultyStructured,
            cli_provider_factory=lambda b: _StubCLI(b, output="unused"),
        )
    message = str(exc_info.value)
    assert "primary-fault-model" in message
    assert "fallback-model" in message
    assert exc_info.value.__cause__ is not None


# --- Self-referential fallback_model rejected (finding #24) ----------------


def test_resolve_stage_config_rejects_self_referential_fallback_model():
    from ai_orchestration.config import resolve_stage_config
    from ai_orchestration.errors import ConfigError

    with pytest.raises(ConfigError, match="fallback_model"):
        resolve_stage_config(
            "planner",
            cli_value=None,
            file_stages={"planner": {"model": "gpt-5.5", "fallback_model": "gpt-5.5"}},
            catalog=CatalogStatus(outcome=CatalogOutcome.UNREACHABLE),
        )


# --- fallback_binary restricted to the known CLI set (finding #17) ---------


def test_resolve_stage_config_rejects_unsupported_fallback_binary():
    from ai_orchestration.config import resolve_stage_config
    from ai_orchestration.errors import ConfigError

    with pytest.raises(ConfigError, match="fallback_binary"):
        resolve_stage_config(
            "planner",
            cli_value=None,
            file_stages={
                "planner": {"model": "gpt-5.5", "fallback_binary": "some-other-cli"}
            },
            catalog=CatalogStatus(outcome=CatalogOutcome.UNREACHABLE),
            binary_exists=lambda _name: True,
        )
