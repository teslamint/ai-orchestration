"""Per-stage provider resolution and fault-class-aware fallback (§Failure classes).

Two remedies for two different faults (per the design's decision on
CLIProxyAPI fallback semantics):

- `TransportError` (connection refused, DNS, timeout): endpoint-level.
  `fallback_model` is skipped — it shares the dead transport — and
  `fallback_binary` is tried instead, logging the downgrade (S5).
- `ModelFaultError` (429, 5xx, unusable output): model-level while the
  endpoint is healthy. Retries via `fallback_model` on the same endpoint if
  configured (S6); otherwise fails the stage. Never drops to a subprocess.
- `AuthenticationError` (401/403): a credential fault. Never falls back to
  anything.
- Every CLI attempt is terminal: no onward fallback, and a failed
  `fallback_binary` never escalates back to the proxy that was already
  unreachable.
"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel

from ai_orchestration.config import CatalogStatus, SlotKind, StageConfig, classify_slot
from ai_orchestration.errors import ProviderError
from ai_orchestration.providers.base import (
    AuthenticationError,
    CLIProviderError,
    ModelFaultError,
    TransportError,
)
from ai_orchestration.providers.call_events import CallRecorder
from ai_orchestration.providers.http import HttpProvider

HttpProviderFactory = Callable[[str], object]
CliProviderFactory = Callable[[str], object]


def _error_kind(exc: BaseException) -> str:
    if isinstance(exc, AuthenticationError):
        return "authentication"
    if isinstance(exc, TransportError):
        return "transport"
    if isinstance(exc, ModelFaultError):
        return "model"
    if isinstance(exc, CLIProviderError):
        return "cli"
    return "provider"


def _invoke_recorded(
    provider,
    method_name: str,
    prompt: str,
    *,
    recorder: CallRecorder | None,
    stage_name: str | None,
    provider_kind: str,
    provider_id: str,
    attempt_number: int,
    call_kwargs: dict,
    executor_step_id: str | int | None = None,
):
    if recorder is None:
        return getattr(provider, method_name)(prompt, **call_kwargs)
    if stage_name is None:
        raise ValueError("stage_name is required when recording provider calls")
    attempt = recorder.start_attempt(
        stage=stage_name,
        provider_kind=provider_kind,
        provider_id=provider_id,
        attempt=attempt_number,
        prompt=prompt,
        executor_step_id=executor_step_id,
    )
    if hasattr(provider, "usage_sink"):
        provider.usage_sink = attempt.attach_usage
    try:
        result = getattr(provider, method_name)(prompt, **call_kwargs)
    except BaseException as exc:
        attempt.finish(outcome="error", error_kind=_error_kind(exc))
        raise
    attempt.finish(outcome="success")
    return result


def resolve_provider_chain(
    stage: StageConfig,
    *,
    catalog: CatalogStatus,
    base_url: str,
    api_key: str | None,
):
    """Resolve `stage.model` to a concrete provider instance (no fallback logic)."""
    from ai_orchestration.providers.base import ToolType
    from ai_orchestration.providers.cli import LLMToolFactory

    if classify_slot(stage.model) is SlotKind.BINARY:
        return LLMToolFactory.create(ToolType(stage.model))
    return HttpProvider(model=stage.model, base_url=base_url, api_key=api_key)


def complete_with_fallback(
    stage: StageConfig,
    prompt: str,
    *,
    http_provider_factory: HttpProviderFactory,
    cli_provider_factory: CliProviderFactory,
    recorder: CallRecorder | None = None,
    stage_name: str | None = None,
    executor_step_id: str | int | None = None,
    **complete_kwargs,
) -> tuple[str, str]:
    """Complete a prompt for `stage`, applying fault-class-aware fallback.

    Returns `(content, provider_identifier)`. `provider_identifier` is the
    model id that actually answered, or the binary name for a CLI fallback.
    """
    if classify_slot(stage.model) is SlotKind.BINARY:
        provider = cli_provider_factory(stage.model)
        return (
            _invoke_recorded(
                provider,
                "complete",
                prompt,
                recorder=recorder,
                stage_name=stage_name,
                provider_kind="cli_binary",
                provider_id=stage.model,
                attempt_number=1,
                call_kwargs=complete_kwargs,
                executor_step_id=executor_step_id,
            ),
            stage.model,
        )

    primary = http_provider_factory(stage.model)
    try:
        return (
            _invoke_recorded(
                primary,
                "complete",
                prompt,
                recorder=recorder,
                stage_name=stage_name,
                provider_kind="proxy_model",
                provider_id=stage.model,
                attempt_number=1,
                call_kwargs=complete_kwargs,
                executor_step_id=executor_step_id,
            ),
            stage.model,
        )
    except TransportError as primary_exc:
        # S5: endpoint-level fault. fallback_model shares the dead
        # transport, so skip straight to fallback_binary.
        if stage.fallback_binary is None:
            raise
        cli = cli_provider_factory(stage.fallback_binary)
        try:
            return (
                _invoke_recorded(
                    cli,
                    "complete",
                    prompt,
                    recorder=recorder,
                    stage_name=stage_name,
                    provider_kind="cli_binary",
                    provider_id=stage.fallback_binary,
                    attempt_number=2,
                    call_kwargs=complete_kwargs,
                    executor_step_id=executor_step_id,
                ),
                stage.fallback_binary,
            )
        except Exception as fallback_exc:
            raise ProviderError(
                f"{stage.model}: {primary_exc}; fallback_binary "
                f"{stage.fallback_binary}: {fallback_exc}"
            ) from fallback_exc
    except AuthenticationError:
        # A credential fault is not fixed by another model or a subprocess.
        raise
    except ProviderError as primary_exc:
        # S6: model-level fault (429/5xx/unusable output). Retry on the
        # same endpoint via fallback_model if configured; never drop to CLI.
        if stage.fallback_model is None:
            raise
        fallback = http_provider_factory(stage.fallback_model)
        try:
            return (
                _invoke_recorded(
                    fallback,
                    "complete",
                    prompt,
                    recorder=recorder,
                    stage_name=stage_name,
                    provider_kind="proxy_model",
                    provider_id=stage.fallback_model,
                    attempt_number=2,
                    call_kwargs=complete_kwargs,
                    executor_step_id=executor_step_id,
                ),
                stage.fallback_model,
            )
        except Exception as fallback_exc:
            raise ProviderError(
                f"{stage.model}: {primary_exc}; fallback_model "
                f"{stage.fallback_model}: {fallback_exc}"
            ) from fallback_exc


def complete_structured_with_fallback(
    stage: StageConfig,
    prompt: str,
    *,
    schema: type[BaseModel],
    http_provider_factory: HttpProviderFactory,
    cli_provider_factory: CliProviderFactory,
    recorder: CallRecorder | None = None,
    stage_name: str | None = None,
    executor_step_id: str | int | None = None,
    **complete_kwargs,
) -> tuple[BaseModel, str]:
    """Structured-output counterpart of `complete_with_fallback`."""
    if classify_slot(stage.model) is SlotKind.BINARY:
        provider = cli_provider_factory(stage.model)
        return (
            _invoke_recorded(
                provider,
                "complete_structured",
                prompt,
                recorder=recorder,
                stage_name=stage_name,
                provider_kind="cli_binary",
                provider_id=stage.model,
                attempt_number=1,
                call_kwargs={"schema": schema, **complete_kwargs},
                executor_step_id=executor_step_id,
            ),
            stage.model,
        )

    primary = http_provider_factory(stage.model)
    try:
        return (
            _invoke_recorded(
                primary,
                "complete_structured",
                prompt,
                recorder=recorder,
                stage_name=stage_name,
                provider_kind="proxy_model",
                provider_id=stage.model,
                attempt_number=1,
                call_kwargs={"schema": schema, **complete_kwargs},
                executor_step_id=executor_step_id,
            ),
            stage.model,
        )
    except TransportError as primary_exc:
        if stage.fallback_binary is None:
            raise
        cli = cli_provider_factory(stage.fallback_binary)
        try:
            return (
                _invoke_recorded(
                    cli,
                    "complete_structured",
                    prompt,
                    recorder=recorder,
                    stage_name=stage_name,
                    provider_kind="cli_binary",
                    provider_id=stage.fallback_binary,
                    attempt_number=2,
                    call_kwargs={"schema": schema, **complete_kwargs},
                    executor_step_id=executor_step_id,
                ),
                stage.fallback_binary,
            )
        except Exception as fallback_exc:
            raise ProviderError(
                f"{stage.model}: {primary_exc}; fallback_binary "
                f"{stage.fallback_binary}: {fallback_exc}"
            ) from fallback_exc
    except AuthenticationError:
        raise
    except ProviderError as primary_exc:
        if stage.fallback_model is None:
            raise
        fallback = http_provider_factory(stage.fallback_model)
        try:
            return (
                _invoke_recorded(
                    fallback,
                    "complete_structured",
                    prompt,
                    recorder=recorder,
                    stage_name=stage_name,
                    provider_kind="proxy_model",
                    provider_id=stage.fallback_model,
                    attempt_number=2,
                    call_kwargs={"schema": schema, **complete_kwargs},
                    executor_step_id=executor_step_id,
                ),
                stage.fallback_model,
            )
        except Exception as fallback_exc:
            raise ProviderError(
                f"{stage.model}: {primary_exc}; fallback_model "
                f"{stage.fallback_model}: {fallback_exc}"
            ) from fallback_exc
