"""Content-free, process-safe provider call event recording."""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
import warnings
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable, Literal, Mapping, TypeAlias

Stage: TypeAlias = Literal[
    "brainstormer", "reviewer", "planner", "executor", "code_reviewer", "fixer"
]
ProviderKind: TypeAlias = Literal["proxy_model", "cli_binary", "direct_vendor"]
Outcome: TypeAlias = Literal["success", "error"]
ErrorKind: TypeAlias = Literal[
    "authentication", "transport", "model", "cli", "provider", "recorder"
]
UsageSink: TypeAlias = Callable[[Mapping[str, int] | None], None]

_STAGES = frozenset(
    {"brainstormer", "reviewer", "planner", "executor", "code_reviewer", "fixer"}
)
_PROVIDER_KINDS = frozenset({"proxy_model", "cli_binary", "direct_vendor"})
_OUTCOMES = frozenset({"success", "error"})
_ERROR_KINDS = frozenset(
    {"authentication", "transport", "model", "cli", "provider", "recorder"}
)
_UNSET = object()


class CallEventError(ValueError):
    """A call event or event stream violates the call-event/v1 contract."""


def estimate_input_tokens(prompt: str | bytes) -> int:
    """Estimate tokens as the ceiling of UTF-8 bytes divided by four."""
    prompt_bytes = _prompt_bytes(prompt)
    return (len(prompt_bytes) + 3) // 4


def _prompt_bytes(prompt: str | bytes) -> bytes:
    if isinstance(prompt, str):
        return prompt.encode("utf-8")
    if isinstance(prompt, bytes):
        try:
            prompt.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CallEventError("prompt bytes must contain valid UTF-8") from exc
        return prompt
    raise CallEventError("prompt must be a string or bytes")


def _validate_integer(
    name: str, value: object, *, minimum: int = 0, nullable: bool = False
) -> None:
    if nullable and value is None:
        return
    if type(value) is not int or value < minimum:
        null_suffix = " or null" if nullable else ""
        raise CallEventError(f"{name} must be an integer >= {minimum}{null_suffix}")


@dataclass(frozen=True, slots=True, kw_only=True)
class CallEvent:
    """One finalized routing-layer attempt with no prompt or response content."""

    version: str = "call-event/v1"
    run_id: str
    task_id: str | None
    executor_step_id: str | int | None
    stage: Stage
    provider_kind: ProviderKind
    provider_id: str
    attempt: int
    prompt_bytes: int
    estimated_input_tokens: int
    reported_input_tokens: int | None
    reported_output_tokens: int | None
    transport_retry_count: int | None
    latency_ms: int
    outcome: Outcome
    error_kind: ErrorKind | None

    def __post_init__(self) -> None:
        if self.version != "call-event/v1":
            raise CallEventError("version must be call-event/v1")
        try:
            uuid.UUID(self.run_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise CallEventError("run_id must be a UUID string") from exc
        if not isinstance(self.task_id, (str, type(None))):
            raise CallEventError("task_id must be a string or null")
        if not (
            self.executor_step_id is None
            or isinstance(self.executor_step_id, str)
            or type(self.executor_step_id) is int
        ):
            raise CallEventError("executor_step_id must be a string, integer, or null")
        if not isinstance(self.stage, str) or self.stage not in _STAGES:
            raise CallEventError(f"unsupported stage: {self.stage}")
        if (
            not isinstance(self.provider_kind, str)
            or self.provider_kind not in _PROVIDER_KINDS
        ):
            raise CallEventError(f"unsupported provider_kind: {self.provider_kind}")
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise CallEventError("provider_id must be a non-empty string")
        _validate_integer("attempt", self.attempt, minimum=1)
        _validate_integer("prompt_bytes", self.prompt_bytes)
        _validate_integer("estimated_input_tokens", self.estimated_input_tokens)
        expected_estimate = (self.prompt_bytes + 3) // 4
        if self.estimated_input_tokens != expected_estimate:
            raise CallEventError(
                "estimated_input_tokens must equal ceil(prompt_bytes / 4)"
            )
        _validate_integer(
            "reported_input_tokens", self.reported_input_tokens, nullable=True
        )
        _validate_integer(
            "reported_output_tokens", self.reported_output_tokens, nullable=True
        )
        _validate_integer(
            "transport_retry_count", self.transport_retry_count, nullable=True
        )
        _validate_integer("latency_ms", self.latency_ms)
        if not isinstance(self.outcome, str) or self.outcome not in _OUTCOMES:
            raise CallEventError(f"unsupported outcome: {self.outcome}")
        if self.outcome == "success" and self.error_kind is not None:
            raise CallEventError("error_kind must be null for a successful outcome")
        if self.outcome == "error" and (
            not isinstance(self.error_kind, str) or self.error_kind not in _ERROR_KINDS
        ):
            raise CallEventError("error_kind must identify an error outcome")

    def to_dict(self) -> dict[str, object]:
        """Return the exact call-event/v1 JSON object."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> CallEvent:
        """Validate and decode one exact call-event/v1 JSON object."""
        if not isinstance(payload, Mapping):
            raise CallEventError("call event must be a JSON object")
        if not all(isinstance(name, str) for name in payload):
            raise CallEventError("call event field names must be strings")
        expected = {field.name for field in fields(cls)}
        actual = set(payload)
        unknown = actual - expected
        missing = expected - actual
        if unknown:
            raise CallEventError(f"call event has unknown fields: {sorted(unknown)}")
        if missing:
            raise CallEventError(f"call event has missing fields: {sorted(missing)}")
        return cls(**payload)  # type: ignore[arg-type]


class CallAttempt:
    """Single-use token that finalizes one call event."""

    def __init__(
        self,
        recorder: CallRecorder,
        *,
        run_id: str,
        task_id: str | None,
        executor_step_id: str | int | None,
        stage: Stage,
        provider_kind: ProviderKind,
        provider_id: str,
        attempt: int,
        prompt_bytes: int,
        started_ns: int,
    ) -> None:
        self._recorder = recorder
        self._event_values = {
            "run_id": run_id,
            "task_id": task_id,
            "executor_step_id": executor_step_id,
            "stage": stage,
            "provider_kind": provider_kind,
            "provider_id": provider_id,
            "attempt": attempt,
            "prompt_bytes": prompt_bytes,
            "estimated_input_tokens": (prompt_bytes + 3) // 4,
        }
        self._started_ns = started_ns
        self._usage_attached = False
        self._reported_input_tokens: int | None = None
        self._reported_output_tokens: int | None = None
        self._finished = False

        CallEvent(
            **self._event_values,
            reported_input_tokens=None,
            reported_output_tokens=None,
            transport_retry_count=None,
            latency_ms=0,
            outcome="success",
            error_kind=None,
        )

    def attach_usage(self, usage: Mapping[str, int] | None) -> None:
        """Attach normalized or legacy transport usage exactly once."""
        if self._finished:
            raise CallEventError("cannot attach usage to an already finished attempt")
        if self._usage_attached:
            raise CallEventError("usage was already attached")
        if usage is not None and not isinstance(usage, Mapping):
            raise CallEventError("usage must be a mapping or null")

        input_tokens = _usage_value(usage, "input_tokens", "prompt_tokens")
        output_tokens = _usage_value(usage, "output_tokens", "completion_tokens")
        _validate_integer("reported_input_tokens", input_tokens, nullable=True)
        _validate_integer("reported_output_tokens", output_tokens, nullable=True)
        self._reported_input_tokens = input_tokens
        self._reported_output_tokens = output_tokens
        self._usage_attached = True

    def finish(
        self,
        *,
        outcome: Outcome,
        error_kind: ErrorKind | None = None,
        transport_retry_count: int | None = None,
        latency_ms: int | None = None,
    ) -> CallEvent:
        """Finalize and record this attempt exactly once."""
        if self._finished:
            raise CallEventError("attempt is already finished")
        if latency_ms is None:
            elapsed_ns = max(0, time.monotonic_ns() - self._started_ns)
            latency_ms = elapsed_ns // 1_000_000
        event = CallEvent(
            **self._event_values,
            reported_input_tokens=self._reported_input_tokens,
            reported_output_tokens=self._reported_output_tokens,
            transport_retry_count=transport_retry_count,
            latency_ms=latency_ms,
            outcome=outcome,
            error_kind=error_kind,
        )
        self._finished = True
        self._recorder.record(event)
        return event


def _usage_value(
    usage: Mapping[str, int] | None, normalized_name: str, legacy_name: str
) -> int | None:
    if usage is None:
        return None
    normalized = usage.get(normalized_name)
    legacy = usage.get(legacy_name)
    if normalized is not None and legacy is not None and normalized != legacy:
        raise CallEventError(
            f"usage has conflicting {normalized_name} and {legacy_name} values"
        )
    return normalized if normalized is not None else legacy


class CallRecorder:
    """Append complete call-event/v1 rows under a process-safe file lock."""

    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        task_id: str | None = None,
        executor_step_id: str | int | None = None,
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.task_id = task_id
        self.executor_step_id = executor_step_id
        self._lock_path = self.path.with_name(f".{self.path.name}.lock")
        CallEvent(
            run_id=run_id,
            task_id=task_id,
            executor_step_id=executor_step_id,
            stage="planner",
            provider_kind="proxy_model",
            provider_id="validation",
            attempt=1,
            prompt_bytes=0,
            estimated_input_tokens=0,
            reported_input_tokens=None,
            reported_output_tokens=None,
            transport_retry_count=None,
            latency_ms=0,
            outcome="success",
            error_kind=None,
        )

    def start_attempt(
        self,
        *,
        stage: Stage,
        provider_kind: ProviderKind,
        provider_id: str,
        attempt: int,
        prompt: str | bytes,
        task_id: str | None | object = _UNSET,
        executor_step_id: str | int | None | object = _UNSET,
    ) -> CallAttempt:
        """Start one attempt without retaining prompt content."""
        encoded_prompt = _prompt_bytes(prompt)
        resolved_task_id = self.task_id if task_id is _UNSET else task_id
        resolved_step_id = (
            self.executor_step_id if executor_step_id is _UNSET else executor_step_id
        )
        return CallAttempt(
            self,
            run_id=self.run_id,
            task_id=resolved_task_id,  # type: ignore[arg-type]
            executor_step_id=resolved_step_id,  # type: ignore[arg-type]
            stage=stage,
            provider_kind=provider_kind,
            provider_id=provider_id,
            attempt=attempt,
            prompt_bytes=len(encoded_prompt),
            started_ns=time.monotonic_ns(),
        )

    def record(self, event: CallEvent) -> None:
        """Record an event; warn instead of changing provider behavior on failure."""
        if not isinstance(event, CallEvent):
            raise CallEventError("record requires a validated CallEvent")
        try:
            self._append(event)
        except (CallEventError, OSError) as exc:
            warnings.warn(
                f"failed to record call event: {exc}", RuntimeWarning, stacklevel=2
            )

    def read_events(self) -> list[CallEvent]:
        """Read and validate the complete event stream."""
        return read_call_events(self.path)

    def _append(self, event: CallEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            event_fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                existing = _read_fd(event_fd)
                complete = self._quarantine_partial_tail(event_fd, existing)
                _decode_rows(complete, self.path)
                payload = (
                    json.dumps(
                        event.to_dict(), ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    + b"\n"
                )
                self._write_all(event_fd, payload)
                os.fsync(event_fd)
            finally:
                os.close(event_fd)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _quarantine_partial_tail(self, event_fd: int, content: bytes) -> bytes:
        if not content or content.endswith(b"\n"):
            return content
        newline = content.rfind(b"\n")
        complete_length = newline + 1
        partial = content[complete_length:]
        quarantine_path = self.path.with_name(
            f"{self.path.name}.partial.{uuid.uuid4().hex}"
        )
        quarantine_fd = os.open(
            quarantine_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            self._write_all(quarantine_fd, partial)
            os.fsync(quarantine_fd)
        finally:
            os.close(quarantine_fd)
        os.ftruncate(event_fd, complete_length)
        os.fsync(event_fd)
        return content[:complete_length]

    def _write_all(self, fd: int, payload: bytes) -> None:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("event write made no progress")
            remaining = remaining[written:]


def _read_fd(fd: int) -> bytes:
    size = os.fstat(fd).st_size
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        chunk = os.pread(fd, min(64 * 1024, size - offset), offset)
        if not chunk:
            raise OSError("event read ended before the reported file size")
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _decode_rows(content: bytes, path: Path) -> list[CallEvent]:
    if content and not content.endswith(b"\n"):
        raise CallEventError(f"call event stream at {path} has a partial trailing row")
    events: list[CallEvent] = []
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        try:
            payload = json.loads(raw_line)
            events.append(CallEvent.from_dict(payload))
        except (CallEventError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CallEventError(
                f"invalid call event at {path} line {line_number}: {exc}"
            ) from exc
    return events


def read_call_events(path: Path) -> list[CallEvent]:
    """Read a stable snapshot and fail closed on any malformed or partial row."""
    path = Path(path)
    if not path.exists():
        return []
    lock_path = path.with_name(f".{path.name}.lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_SH)
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return []
        return _decode_rows(content, path)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
