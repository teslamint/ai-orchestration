"""CLIProxyAPI HTTP provider via the official `openai` SDK (decision 3a).

Targets the OpenAI-compatible proxy endpoint. Requests a flat JSON schema
where supported, then falls back to `utils/extract.py` on refusal,
malformed JSON, or validation failure (decision 3). `probe_catalog` is the
seam U1's config validation and U3's own startup checks share; it never
raises, returning a `CatalogStatus` outcome instead.
"""

from __future__ import annotations

import json
from typing import Optional

import httpx
from openai import APIConnectionError, APIStatusError, OpenAI
from pydantic import BaseModel, ValidationError

from ai_orchestration.config import CatalogOutcome, CatalogStatus
from ai_orchestration.providers.base import (
    AuthenticationError,
    ModelFaultError,
    TransportError,
    flatten_json_schema,
)
from ai_orchestration.utils.extract import extract_json_object

# Default per-call wall-clock limit: a hung proxy call must not stall the
# run indefinitely (§Failure classes: "exceeds the stage timeout").
DEFAULT_HTTP_TIMEOUT_SECONDS = 120.0


def probe_catalog(
    base_url: str,
    api_key: Optional[str],
    *,
    transport: Optional[httpx.BaseTransport] = None,
) -> CatalogStatus:
    """Probe `/v1/models` and classify the result.

    Never raises: an unreachable endpoint is a normal `UNREACHABLE` outcome
    (S5's skip-not-fail condition), not an exception.
    """
    http_client = httpx.Client(transport=transport) if transport is not None else None
    try:
        client = OpenAI(
            base_url=base_url, api_key=api_key or "unset", http_client=http_client
        )
        response = client.models.list()
        model_ids = frozenset(m.id for m in (response.data or []))
        if model_ids:
            return CatalogStatus(
                outcome=CatalogOutcome.REACHABLE_WITH_MODELS, models=model_ids
            )
        return CatalogStatus(outcome=CatalogOutcome.REACHABLE_WITHOUT_ID)
    except (APIConnectionError, httpx.ConnectError, httpx.TimeoutException):
        return CatalogStatus(outcome=CatalogOutcome.UNREACHABLE)
    except APIStatusError:
        # The endpoint answered but the models call itself failed (e.g. 401
        # on this route); treat as reachable-without-id rather than
        # unreachable, since the transport itself is up.
        return CatalogStatus(outcome=CatalogOutcome.REACHABLE_WITHOUT_ID)
    except Exception:
        return CatalogStatus(outcome=CatalogOutcome.UNREACHABLE)


class HttpProvider:
    """Provider targeting one model on the CLIProxyAPI endpoint."""

    def __init__(
        self,
        *,
        model: str = "",
        base_url: str,
        api_key: Optional[str],
        transport: Optional[httpx.BaseTransport] = None,
        max_retries: int = 2,
        timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        http_client = (
            httpx.Client(transport=transport) if transport is not None else None
        )
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "unset",
            http_client=http_client,
            max_retries=max_retries,
            timeout=timeout,
        )

    def is_available(self) -> bool:
        return True

    def complete(self, prompt: str, *, system: Optional[str] = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            response = self._client.chat.completions.create(
                model=self.model, messages=messages
            )
        except (APIConnectionError, httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TransportError(f"{self.model}: endpoint unreachable ({exc})") from exc
        except APIStatusError as exc:
            self._raise_for_status(exc)
        except json.JSONDecodeError as exc:
            # HTTP 200 with a malformed JSON body: the transport is healthy
            # but the response is unusable, so this is a model-level fault,
            # not an unhandled crash escaping the typed error taxonomy.
            raise ModelFaultError(
                f"{self.model}: malformed JSON response body ({exc})"
            ) from exc
        return response.choices[0].message.content or ""

    def complete_structured(
        self, prompt: str, *, schema: type[BaseModel], system: Optional[str] = None
    ) -> BaseModel:
        """Request a flat schema, then fall back to text extraction (decision 3)."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        flat_schema = flatten_json_schema(schema.model_json_schema())

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": flat_schema,
                        "strict": True,
                    },
                },
            )
        except (APIConnectionError, httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TransportError(f"{self.model}: endpoint unreachable ({exc})") from exc
        except APIStatusError as exc:
            self._raise_for_status(exc)
        except json.JSONDecodeError as exc:
            raise ModelFaultError(
                f"{self.model}: malformed JSON response body ({exc})"
            ) from exc

        text = response.choices[0].message.content or ""
        payload = extract_json_object(text)
        if payload is not None:
            try:
                return schema.model_validate(payload)
            except ValidationError:
                pass  # fall through to a second extraction attempt below

        # Model refused/ignored the schema (e.g. claude-sonnet-5) or emitted
        # prose around JSON (e.g. gemini partial compliance): retry once
        # more, purely on the extraction path, before giving up.
        payload = extract_json_object(text)
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

    def _raise_for_status(self, exc: APIStatusError):
        status = exc.status_code
        if status in (401, 403):
            raise AuthenticationError(
                f"{self.model}: authentication failed (HTTP {status})"
            ) from exc
        if status == 429 or status >= 500:
            raise ModelFaultError(f"{self.model}: HTTP {status}") from exc
        raise ModelFaultError(f"{self.model}: HTTP {status}") from exc
