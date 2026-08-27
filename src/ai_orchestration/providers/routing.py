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
from ai_orchestration.providers.base import AuthenticationError, TransportError
from ai_orchestration.providers.http import HttpProvider

HttpProviderFactory = Callable[[str], object]
CliProviderFactory = Callable[[str], object]


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
    **complete_kwargs,
) -> tuple[str, str]:
    """Complete a prompt for `stage`, applying fault-class-aware fallback.

    Returns `(content, provider_identifier)`. `provider_identifier` is the
    model id that actually answered, or the binary name for a CLI fallback.
    """
    if classify_slot(stage.model) is SlotKind.BINARY:
        provider = cli_provider_factory(stage.model)
        return provider.complete(prompt, **complete_kwargs), stage.model

    primary = http_provider_factory(stage.model)
    try:
        return primary.complete(prompt, **complete_kwargs), stage.model
    except TransportError as primary_exc:
        # S5: endpoint-level fault. fallback_model shares the dead
        # transport, so skip straight to fallback_binary.
        if stage.fallback_binary is None:
            raise
        cli = cli_provider_factory(stage.fallback_binary)
        try:
            return cli.complete(prompt, **complete_kwargs), stage.fallback_binary
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
            return fallback.complete(prompt, **complete_kwargs), stage.fallback_model
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
    **complete_kwargs,
) -> tuple[BaseModel, str]:
    """Structured-output counterpart of `complete_with_fallback`."""
    if classify_slot(stage.model) is SlotKind.BINARY:
        provider = cli_provider_factory(stage.model)
        return (
            provider.complete_structured(prompt, schema=schema, **complete_kwargs),
            stage.model,
        )

    primary = http_provider_factory(stage.model)
    try:
        return (
            primary.complete_structured(prompt, schema=schema, **complete_kwargs),
            stage.model,
        )
    except TransportError as primary_exc:
        if stage.fallback_binary is None:
            raise
        cli = cli_provider_factory(stage.fallback_binary)
        try:
            return (
                cli.complete_structured(prompt, schema=schema, **complete_kwargs),
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
                fallback.complete_structured(prompt, schema=schema, **complete_kwargs),
                stage.fallback_model,
            )
        except Exception as fallback_exc:
            raise ProviderError(
                f"{stage.model}: {primary_exc}; fallback_model "
                f"{stage.fallback_model}: {fallback_exc}"
            ) from fallback_exc
