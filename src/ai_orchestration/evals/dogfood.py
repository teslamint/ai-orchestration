"""Deterministic validation for the Cycle 1 dogfood contract.

This module never invokes a model or opens a network connection. Live model
execution stays fail-closed until the transport authorization gate lands.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import statistics
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


class ContractError(ValueError):
    """A persisted eval artifact does not satisfy the versioned contract."""


@dataclass(frozen=True)
class EvalTask:
    task_id: str
    trial_ids: tuple[str, ...]
    expected_terminal: str


@dataclass(frozen=True)
class Rubric:
    version: str
    dimensions: tuple[str, ...]
    minimum_dimension: int
    maximum_dimension: int
    usable_score: int
    max_latency_ratio: float


@dataclass(frozen=True)
class EvalContract:
    version: str
    tasks: tuple[EvalTask, ...]
    rubric: Rubric


@dataclass(frozen=True)
class ExpectedAttempt:
    task_id: str | None
    stage: str
    provider_id: str
    attempt: int


@dataclass(frozen=True)
class EventValidation:
    valid: bool
    event_count: int


@dataclass(frozen=True)
class ArtifactScore:
    dimensions: Mapping[str, int]
    total: int
    usable: bool


@dataclass(frozen=True)
class TrialResult:
    task_id: str
    trial_id: str
    terminal_status: str
    elapsed_ms: int
    rubric_score: int | None
    usable: bool
    cost: float | None


@dataclass(frozen=True)
class ComparisonReport:
    comparable_pairs: int
    latency_ratio: float | None
    latency_passed: bool | None
    cost_status: str
    candidate_usable: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "comparison-report/v1",
            "comparable_pairs": self.comparable_pairs,
            "latency_ratio": self.latency_ratio,
            "latency_passed": self.latency_passed,
            "cost_status": self.cost_status,
            "candidate_usable": self.candidate_usable,
        }


@dataclass(frozen=True)
class ReceiptReservation:
    scope: str
    nonce: str
    reservation_path: Path


_TASK_IDS = ("C1-R", "C1-L", "C1-E")
_TRIAL_IDS = ("01", "02", "03")
_DIMENSIONS = (
    "requirement_coverage",
    "executable_correctness",
    "test_quality",
    "maintainability",
    "evidence_clarity",
)
_EVENT_FIELDS = {
    "version",
    "run_id",
    "task_id",
    "executor_step_id",
    "stage",
    "provider_kind",
    "provider_id",
    "attempt",
    "prompt_bytes",
    "estimated_input_tokens",
    "reported_input_tokens",
    "reported_output_tokens",
    "transport_retry_count",
    "latency_ms",
    "outcome",
    "error_kind",
}
_CONTENT_FIELDS = {"prompt", "response", "messages", "system_prompt", "content"}
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_USER_APPROVAL_KEY_ENV = "AI_ORCHESTRATION_USER_APPROVAL_KEY"
PRODUCER_ATTESTATION_KEY_ENV = "AI_ORCHESTRATION_PRODUCER_ATTESTATION_KEY"
_RECEIPT_SIGNATURE_FIELDS = (
    "version",
    "nonce",
    "issuer",
    "scope",
    "packet_sha256",
    "session_id",
    "issued_at",
    "expires_at",
    "consumed_at",
)
_PRODUCER_SIGNATURE_FIELDS = (
    "version",
    "run_id",
    "state_path",
    "state_sha256",
    "call_events_path",
    "call_events_sha256",
)
_CREDENTIAL_NAMES = {
    "CLIPROXYAPI_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_AI_API_KEY",
}
_ERROR_KINDS = {
    "authentication",
    "transport",
    "model",
    "cli",
    "provider",
    "recorder",
}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"required contract file does not exist: {path}") from exc
    except OSError as exc:
        raise ContractError(f"required contract file is unreadable: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON in {path.name}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path.name} must contain a JSON object")
    return value


def load_eval_contract(path: Path) -> EvalContract:
    """Load and strictly validate the fixed dogfood/v1 dataset and grader."""
    root = Path(path)
    dataset = _load_object(root / "dataset.json")
    grader = _load_object(root / "grader.yaml")
    if dataset.get("version") != "dogfood/v1":
        raise ContractError("unsupported dataset version")
    if set(dataset) != {"version", "environment_fingerprint", "tasks"}:
        raise ContractError("dataset has an unexpected shape")
    raw_tasks = dataset.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ContractError("dataset tasks must be a list")
    tasks: list[EvalTask] = []
    for raw in raw_tasks:
        if not isinstance(raw, dict) or set(raw) != {
            "task_id",
            "trial_ids",
            "expected_terminal",
            "description",
        }:
            raise ContractError("dataset task has an unexpected shape")
        trials = raw["trial_ids"]
        if not isinstance(trials, list) or not all(isinstance(x, str) for x in trials):
            raise ContractError("task trial_ids must be strings")
        tasks.append(EvalTask(raw["task_id"], tuple(trials), raw["expected_terminal"]))
    if tuple(task.task_id for task in tasks) != _TASK_IDS:
        raise ContractError("dataset must contain the fixed C1 task order")
    if any(task.trial_ids != _TRIAL_IDS for task in tasks):
        raise ContractError("each task must contain fixed trials 01 through 03")

    if grader.get("version") != "grader/v1":
        raise ContractError("unsupported grader version")
    expected_grader_fields = {
        "version",
        "dimensions",
        "score_range",
        "usable_score",
        "require_no_zero",
        "max_latency_ratio",
    }
    if set(grader) != expected_grader_fields:
        raise ContractError("grader has an unexpected shape")
    dimensions = grader["dimensions"]
    if dimensions != list(_DIMENSIONS):
        raise ContractError("grader dimensions do not match grader/v1")
    score_range = grader["score_range"]
    if score_range != [0, 2] or grader["usable_score"] != 8:
        raise ContractError("grader thresholds do not match grader/v1")
    if grader["require_no_zero"] is not True:
        raise ContractError("grader must reject zero dimensions")
    ratio = grader["max_latency_ratio"]
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or ratio != 1.25:
        raise ContractError("grader latency threshold does not match grader/v1")
    for schema_name, schema_id in (
        ("call-event.schema.json", "call-event/v1"),
        ("result.schema.json", "dogfood-result/v1"),
    ):
        schema = _load_object(root / schema_name)
        if (
            schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema"
            or schema.get("$id") != schema_id
            or schema.get("additionalProperties") is not False
        ):
            raise ContractError(f"unsupported or permissive schema: {schema_name}")
    return EvalContract(
        "dogfood/v1",
        tuple(tasks),
        Rubric("grader/v1", _DIMENSIONS, 0, 2, 8, 1.25),
    )


def validate_call_events(
    path: Path, expected_attempts: Sequence[ExpectedAttempt]
) -> EventValidation:
    """Validate content-free JSONL and require every expected routing attempt."""
    event_path = Path(path)
    try:
        lines = event_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ContractError("call-event file does not exist") from exc
    events: list[dict[str, Any]] = []
    for ordinal, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(f"malformed call event at line {ordinal}") from exc
        if not isinstance(event, dict):
            raise ContractError(f"call event at line {ordinal} must be an object")
        forbidden = set(event).intersection(_CONTENT_FIELDS)
        if forbidden:
            raise ContractError(
                f"call event contains content field: {sorted(forbidden)[0]}"
            )
        if set(event) != _EVENT_FIELDS:
            raise ContractError(f"call event at line {ordinal} has an unexpected shape")
        if event["version"] != "call-event/v1":
            raise ContractError("unsupported call-event version")
        if not isinstance(event["run_id"], str) or not event["run_id"]:
            raise ContractError("call event run_id must be a non-empty string")
        if event["task_id"] is not None and not isinstance(event["task_id"], str):
            raise ContractError("call event task_id must be a string or null")
        if event["executor_step_id"] is not None and not isinstance(
            event["executor_step_id"], (str, int)
        ):
            raise ContractError("call event executor_step_id has an invalid type")
        if event["stage"] not in {
            "brainstormer",
            "reviewer",
            "planner",
            "executor",
            "code_reviewer",
            "fixer",
        }:
            raise ContractError("call event stage is invalid")
        if event["provider_kind"] not in {
            "proxy_model",
            "cli_binary",
            "direct_vendor",
        }:
            raise ContractError("call event provider_kind is invalid")
        if not isinstance(event["provider_id"], str) or not event["provider_id"]:
            raise ContractError("call event provider_id must be non-empty")
        for name in (
            "attempt",
            "prompt_bytes",
            "estimated_input_tokens",
            "latency_ms",
        ):
            value = event[name]
            minimum = 1 if name == "attempt" else 0
            if type(value) is not int or value < minimum:
                raise ContractError(f"call event {name} is invalid")
        for name in (
            "reported_input_tokens",
            "reported_output_tokens",
            "transport_retry_count",
        ):
            value = event[name]
            if value is not None and (type(value) is not int or value < 0):
                raise ContractError(f"call event {name} is invalid")
        if event["outcome"] not in {"success", "error"}:
            raise ContractError("call event has an invalid outcome")
        if event["outcome"] == "success" and event["error_kind"] is not None:
            raise ContractError("successful call event must not have error_kind")
        if event["outcome"] == "error" and event["error_kind"] not in _ERROR_KINDS:
            raise ContractError("failed call event has an invalid error_kind")
        encoded = json.dumps(event, sort_keys=True)
        for name in _CREDENTIAL_NAMES:
            credential = os.environ.get(name)
            if credential and len(credential) >= 8 and credential in encoded:
                raise ContractError(f"call event contains credential value from {name}")
        events.append(event)
    observed = Counter(
        (event["task_id"], event["stage"], event["provider_id"], event["attempt"])
        for event in events
    )
    expected = Counter(
        (
            expected.task_id,
            expected.stage,
            expected.provider_id,
            expected.attempt,
        )
        for expected in expected_attempts
    )
    if observed != expected:
        missing = sum((expected - observed).values())
        if missing:
            raise ContractError(f"missing expected call events: {missing}")
        unexpected = sum((observed - expected).values())
        if unexpected:
            raise ContractError(f"unexpected call events: {unexpected}")
        raise ContractError("call event counts do not match expected attempts")
    return EventValidation(True, len(events))


def validate_result(path: Path) -> dict[str, Any]:
    """Validate one sanitized dogfood-result/v1 document."""
    payload = _load_object(Path(path))
    required = {
        "version",
        "task_id",
        "trial_id",
        "build_commit",
        "provider_config_sha256",
        "expected_terminal",
        "observed_terminal",
        "comparable",
        "stability_checks",
        "test_passed",
        "rubric_scores",
        "elapsed_ms",
        "usage",
        "cost_status",
        "artifact_manifest",
        "artifact_checks",
    }
    forbidden = set(payload).intersection(_CONTENT_FIELDS)
    if forbidden:
        raise ContractError(f"result contains content field: {sorted(forbidden)[0]}")
    if set(payload) != required:
        raise ContractError("result has an unexpected shape")
    if payload["version"] != "dogfood-result/v1":
        raise ContractError("unsupported result version")
    if payload["task_id"] not in _TASK_IDS or payload["trial_id"] not in _TRIAL_IDS:
        raise ContractError("result task or trial identifier is invalid")
    for name, length in (("build_commit", 40), ("provider_config_sha256", 64)):
        value = payload[name]
        if (
            not isinstance(value, str)
            or len(value) != length
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ContractError(f"result {name} is invalid")
    if type(payload["elapsed_ms"]) is not int or payload["elapsed_ms"] < 0:
        raise ContractError("result elapsed_ms is invalid")
    if payload["cost_status"] not in {"measured", "not_measurable"}:
        raise ContractError("result cost_status is invalid")
    if (
        type(payload["comparable"]) is not bool
        or type(payload["test_passed"]) is not bool
    ):
        raise ContractError("result boolean field is invalid")
    for name in ("stability_checks", "artifact_manifest", "artifact_checks"):
        if not isinstance(payload[name], dict):
            raise ContractError(f"result {name} must be an object")
    if payload["usage"] is not None and not isinstance(payload["usage"], dict):
        raise ContractError("result usage must be an object or null")
    if payload["rubric_scores"] is not None:
        rubric = payload["rubric_scores"]
        if not isinstance(rubric, dict) or set(rubric) != set(_DIMENSIONS):
            raise ContractError("result rubric_scores are invalid")
        if any(
            type(value) is not int or value < 0 or value > 2
            for value in rubric.values()
        ):
            raise ContractError("result rubric score is invalid")
    encoded = json.dumps(payload, sort_keys=True)
    for name in _CREDENTIAL_NAMES:
        credential = os.environ.get(name)
        if credential and len(credential) >= 8 and credential in encoded:
            raise ContractError(f"result contains credential value from {name}")
    return payload


def grade_artifact(path: Path, rubric: Rubric) -> ArtifactScore:
    """Validate deterministic rubric scores stored in a blinded artifact."""
    payload = _load_object(Path(path))
    if set(payload) != {"rubric_scores"} or not isinstance(
        payload["rubric_scores"], dict
    ):
        raise ContractError("artifact must contain only rubric_scores")
    scores = payload["rubric_scores"]
    if set(scores) != set(rubric.dimensions):
        raise ContractError("artifact rubric dimensions do not match")
    if any(type(value) is not int for value in scores.values()):
        raise ContractError("artifact rubric scores must be integers")
    if any(
        value < rubric.minimum_dimension or value > rubric.maximum_dimension
        for value in scores.values()
    ):
        raise ContractError("artifact rubric score is outside the allowed range")
    total = sum(scores.values())
    return ArtifactScore(
        dict(scores), total, total >= rubric.usable_score and 0 not in scores.values()
    )


def compare_builds(
    baseline: Sequence[TrialResult], candidate: Sequence[TrialResult]
) -> ComparisonReport:
    """Compare matching terminal trials without inventing unavailable metrics."""
    baseline_by_id = {(row.task_id, row.trial_id): row for row in baseline}
    ratios: list[float] = []
    measured_cost = True
    common_cost_pairs = 0
    for row in candidate:
        other = baseline_by_id.get((row.task_id, row.trial_id))
        if other is None or other.terminal_status != row.terminal_status:
            continue
        if row.elapsed_ms < 0 or other.elapsed_ms <= 0:
            raise ContractError("comparable elapsed time must be positive")
        ratios.append(row.elapsed_ms / other.elapsed_ms)
        if row.task_id == "C1-E":
            common_cost_pairs += 1
            if row.cost is None or other.cost is None:
                measured_cost = False
    latency_ratio = statistics.median(ratios) if ratios else None
    return ComparisonReport(
        comparable_pairs=len(ratios),
        latency_ratio=latency_ratio,
        latency_passed=None if latency_ratio is None else latency_ratio <= 1.25,
        cost_status=(
            "measured" if common_cost_pairs and measured_cost else "not_measurable"
        ),
        candidate_usable=sum(1 for row in candidate if row.usable),
    )


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError(f"receipt {field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"receipt {field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"receipt {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _signature(
    payload: Mapping[str, Any], fields: tuple[str, ...], key: str, key_error: str
) -> str:
    if not isinstance(key, str) or not key:
        raise ContractError(key_error)
    try:
        signed = {name: payload[name] for name in fields}
    except KeyError as exc:
        raise ContractError("attestation fields are incomplete") from exc
    canonical = json.dumps(
        signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hmac.new(key.encode("utf-8"), canonical, "sha256").hexdigest()


def receipt_signature(payload: Mapping[str, Any], key: str) -> str:
    """Create the HMAC attestation over the immutable receipt fields."""
    return _signature(
        payload,
        _RECEIPT_SIGNATURE_FIELDS,
        key,
        "receipt attestation key is unavailable",
    )


def producer_manifest_signature(payload: Mapping[str, Any], key: str) -> str:
    """Create the HMAC attestation over the producer manifest identity fields."""
    return _signature(
        payload,
        _PRODUCER_SIGNATURE_FIELDS,
        key,
        "producer attestation key is unavailable",
    )


def consume_receipt(
    receipt: Path,
    packet_sha256: str,
    session_id: str,
    *,
    expected_scope: str,
) -> ReceiptReservation:
    """Atomically consume one current, packet-bound, user-issued receipt."""
    path = Path(receipt)
    if not _HEX_64.fullmatch(packet_sha256):
        raise ContractError("packet digest must be a lowercase SHA-256")
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ContractError("receipt does not exist") from exc
    if path.is_symlink():
        raise ContractError("receipt must not be a symlink")
    if metadata.st_mode & 0o777 != 0o600:
        raise ContractError("receipt mode must be 0600")
    payload = _load_object(path)
    required = {
        "version",
        "nonce",
        "issuer",
        "scope",
        "packet_sha256",
        "session_id",
        "issued_at",
        "expires_at",
        "consumed_at",
        "approval_signature",
    }
    if set(payload) != required or payload.get("version") != "approval-receipt/v1":
        raise ContractError("receipt has an unsupported shape or version")
    if payload["issuer"] != "user":
        raise ContractError("receipt issuer must be user")
    if expected_scope not in {"pilot", "full", "grading", "invocation"}:
        raise ContractError("expected receipt scope is invalid")
    if payload["scope"] != expected_scope:
        raise ContractError("receipt scope does not match")
    if payload["packet_sha256"] != packet_sha256:
        raise ContractError("receipt packet digest does not match")
    if payload["session_id"] != session_id:
        raise ContractError("receipt session does not match")
    if payload["consumed_at"] is not None:
        raise ContractError("receipt was already consumed")
    approval_signature = payload["approval_signature"]
    if not isinstance(approval_signature, str) or not _HEX_64.fullmatch(
        approval_signature
    ):
        raise ContractError("receipt first-hand attestation is invalid")
    approval_key = os.environ.get(_USER_APPROVAL_KEY_ENV)
    if not approval_key:
        raise ContractError("receipt attestation key is unavailable")
    expected_signature = receipt_signature(payload, approval_key)
    if not hmac.compare_digest(approval_signature, expected_signature):
        raise ContractError("receipt first-hand attestation is invalid")
    nonce = payload["nonce"]
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ContractError("receipt nonce is invalid")
    issued = _parse_time(payload["issued_at"], "issued_at")
    expires = _parse_time(payload["expires_at"], "expires_at")
    now = datetime.now(timezone.utc)
    if expires <= now:
        raise ContractError("receipt has expired")
    if issued > now or expires <= issued or (expires - issued).total_seconds() > 1800:
        raise ContractError("receipt validity window is invalid")

    reserved = path.with_name(f".{path.name}.{nonce}.reserved")
    try:
        os.link(path, reserved)
    except FileNotFoundError as exc:
        raise ContractError("receipt was consumed concurrently") from exc
    except FileExistsError as exc:
        raise ContractError("receipt reservation already exists") from exc
    try:
        path.unlink()
    except BaseException:
        reserved.unlink(missing_ok=True)
        raise
    consumed = dict(payload)
    consumed["consumed_at"] = now.isoformat()
    _atomic_private_json(reserved, consumed)
    return ReceiptReservation(payload["scope"], nonce, reserved)


def _atomic_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
