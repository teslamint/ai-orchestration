"""Transport-adjacent authorization controls for eval-only external calls."""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import socket
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from ai_orchestration.errors import GateError

TransportKind = Literal["http_json", "cli_argv"]


@dataclass(frozen=True)
class OutboundPayload:
    """An immutable rendering of one exact HTTP JSON body or CLI argv."""

    transport: TransportKind
    _rendered: bytes
    argv: tuple[str, ...] | None = None
    destination: str | None = None
    timeout_seconds: float | None = None
    retry_limit: int | None = None

    @classmethod
    def for_http_json(
        cls,
        payload: bytes | bytearray | memoryview | Mapping[str, Any],
        *,
        destination: str | None = None,
        timeout_seconds: float | None = None,
        retry_limit: int | None = None,
    ) -> OutboundPayload:
        if isinstance(payload, (bytes, bytearray, memoryview)):
            rendered = bytes(payload)
            try:
                json.loads(rendered)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("HTTP payload must contain valid UTF-8 JSON") from exc
        elif isinstance(payload, Mapping):
            try:
                rendered = json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ValueError("HTTP payload must be JSON serializable") from exc
        else:
            raise TypeError("HTTP payload must be JSON bytes or a mapping")
        return cls(
            transport="http_json",
            _rendered=rendered,
            destination=destination,
            timeout_seconds=timeout_seconds,
            retry_limit=retry_limit,
        )

    @classmethod
    def for_cli_argv(cls, argv: Sequence[str]) -> OutboundPayload:
        values = tuple(argv)
        if not values or any(not isinstance(value, str) for value in values):
            raise ValueError("CLI argv must contain at least one string")
        if any("\0" in value for value in values):
            raise ValueError("CLI argv entries must not contain NUL bytes")
        rendered = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        return cls(transport="cli_argv", _rendered=rendered, argv=values)

    def render(self) -> bytes:
        return self._rendered

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self._rendered).hexdigest()


@runtime_checkable
class AuthorizationGate(Protocol):
    def authorize(self, payload: OutboundPayload) -> None: ...


class NoOpAuthorizationGate:
    """The production default, which performs no I/O or persistence."""

    def authorize(self, payload: OutboundPayload) -> None:
        del payload


@dataclass(frozen=True)
class SocketAuthorizationGate:
    """Suspend one eval call until its exact payload receives authorization."""

    socket_path: Path
    payload_path: Path
    timeout: float
    max_response_bytes: int = 16_384

    def __post_init__(self) -> None:
        object.__setattr__(self, "socket_path", Path(self.socket_path))
        object.__setattr__(self, "payload_path", Path(self.payload_path))
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("authorization timeout must be finite and positive")
        if self.max_response_bytes <= 0:
            raise ValueError("authorization response limit must be positive")

    def authorize(self, payload: OutboundPayload) -> None:
        request_id = secrets.token_hex(16)
        created = False
        failure: BaseException | None = None
        try:
            self._write_payload(payload)
            created = True
            response = self._request_authorization(payload, request_id)
            self._validate_response(response, payload, request_id)
        except Exception as exc:
            failure = exc
        finally:
            if created:
                try:
                    self.payload_path.unlink()
                except OSError as exc:
                    if failure is None:
                        failure = GateError(
                            f"authorization payload cleanup failed: {exc}"
                        )
        if failure is not None:
            if isinstance(failure, (GateError, ValueError, TypeError)):
                raise failure
            raise GateError(f"authorization failed: {failure}") from failure

    def _write_payload(self, payload: OutboundPayload) -> None:
        path = self.payload_path.absolute()
        self._reject_symlink_components(path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise GateError(f"authorization payload already exists: {path}") from exc
        except OSError as exc:
            raise GateError(
                f"authorization payload could not be created: {exc}"
            ) from exc
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload.render())
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _reject_symlink_components(path: Path) -> None:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise GateError(
                    f"symlinked authorization payload path is not allowed: {current}"
                )

    def _request_authorization(
        self, payload: OutboundPayload, request_id: str
    ) -> dict[str, Any]:
        packet = {
            "version": "authorization-request/v1",
            "request_id": request_id,
            "transport": payload.transport,
            "payload_path": str(self.payload_path.absolute()),
            "payload_sha256": payload.sha256,
            "destination": payload.destination,
            "timeout_seconds": payload.timeout_seconds,
            "retry_limit": payload.retry_limit,
        }
        encoded = (
            json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        deadline = time.monotonic() + self.timeout
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self._remaining(deadline))
                connection.connect(str(self.socket_path))
                connection.settimeout(self._remaining(deadline))
                connection.sendall(encoded)
                response = self._receive_line(connection, deadline)
        except (TimeoutError, socket.timeout) as exc:
            raise GateError("authorization timed out before transport") from exc
        except OSError as exc:
            raise GateError(
                f"authorization socket failed before transport: {exc}"
            ) from exc
        try:
            decoded = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GateError("authorization response is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise GateError("authorization response must be a JSON object")
        return decoded

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GateError("authorization timed out before transport")
        return remaining

    def _receive_line(self, connection: socket.socket, deadline: float) -> bytes:
        response = bytearray()
        while b"\n" not in response:
            connection.settimeout(self._remaining(deadline))
            chunk = connection.recv(
                min(4096, self.max_response_bytes + 1 - len(response))
            )
            if not chunk:
                raise GateError("authorization socket closed without a response")
            response.extend(chunk)
            if len(response) > self.max_response_bytes:
                raise GateError("authorization response exceeds the size limit")
        line, separator, trailing = bytes(response).partition(b"\n")
        if separator != b"\n" or trailing:
            raise GateError("authorization response must contain exactly one packet")
        return line

    @staticmethod
    def _validate_response(
        response: dict[str, Any], payload: OutboundPayload, request_id: str
    ) -> None:
        expected_fields = {
            "version",
            "request_id",
            "payload_sha256",
            "authorized",
        }
        if set(response) != expected_fields:
            raise GateError("authorization response has an unexpected shape")
        if response["version"] != "authorization-response/v1":
            raise GateError("authorization response version is unsupported")
        if response["request_id"] != request_id:
            raise GateError("authorization response is not for the current request")
        if response["payload_sha256"] != payload.sha256:
            raise GateError("authorization response payload digest does not match")
        if response["authorized"] is not True:
            raise GateError("authorization rejected before transport")
