"""Atomic release-loop phase state and its closed transition table."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from ai_orchestration.compound_loop.source import ContractError


class LedgerError(ContractError):
    """A release-loop ledger is malformed or an event is not authorized."""


LEDGER_SCHEMA = "embedded-release-loop/v1"
PHASES = frozenset(
    {"design", "plan", "implement", "review", "ship", "retro", "done", "blocked"}
)
PHASE_STATUSES = frozenset({"in-progress", "waiting-user", "blocked", "complete"})
MODEL_STAGE_ORDER = (
    "brainstormer",
    "brainstorming_reviewer",
    "planner",
    "executor",
    "code_reviewer",
    "fixer",
)
_FIELDS = (
    "schema",
    "phase",
    "phase_status",
    "current_stage",
    "completed_stages",
    "resume_phase",
    "resume_phase_status",
)


@dataclass(frozen=True)
class ReleaseLoopState:
    """Persisted release phase with a separate six-stage implementation marker."""

    phase: str
    phase_status: str
    current_stage: Optional[str] = None
    completed_stages: tuple[str, ...] = ()
    resume_phase: Optional[str] = None
    resume_phase_status: Optional[str] = None
    schema: str = LEDGER_SCHEMA

    @property
    def next_stage(self) -> Optional[str]:
        if self.current_stage is not None:
            return self.current_stage
        return next(
            (
                stage
                for stage in MODEL_STAGE_ORDER
                if stage not in self.completed_stages
            ),
            None,
        )


@dataclass(frozen=True)
class TransitionResult:
    """Result of one validated event applied to a ledger state."""

    state: ReleaseLoopState
    event: str


def _validate_state(state: ReleaseLoopState) -> None:
    if state.schema != LEDGER_SCHEMA:
        raise LedgerError(f"ledger schema is unsupported: {state.schema}")
    if state.phase not in PHASES:
        raise LedgerError(f"ledger phase is unsupported: {state.phase}")
    if state.phase_status not in PHASE_STATUSES:
        raise LedgerError(f"ledger phase_status is unsupported: {state.phase_status}")
    if state.phase == "done" and state.phase_status != "complete":
        raise LedgerError("done phase must be complete")
    if state.phase == "blocked" and state.phase_status != "blocked":
        raise LedgerError("blocked phase must be blocked")
    if state.phase_status == "complete" and state.phase != "done":
        raise LedgerError("only done phase may be complete")
    if len(set(state.completed_stages)) != len(state.completed_stages):
        raise LedgerError("completed stage markers contain duplicates")
    if tuple(state.completed_stages) != tuple(
        stage for stage in MODEL_STAGE_ORDER if stage in state.completed_stages
    ):
        raise LedgerError("completed stage markers are out of order")
    if state.current_stage is not None:
        if state.phase != "implement" or state.phase_status != "in-progress":
            raise LedgerError("current stage is only valid during implement")
        if state.current_stage not in MODEL_STAGE_ORDER:
            raise LedgerError(f"current stage is unsupported: {state.current_stage}")
        expected = state.next_stage
        if expected != state.current_stage:
            raise LedgerError("current stage marker is stale")
    if state.resume_phase is not None and state.resume_phase not in PHASES - {
        "blocked"
    }:
        raise LedgerError("resume phase is unsupported")
    if (
        state.resume_phase_status is not None
        and state.resume_phase_status not in PHASE_STATUSES
    ):
        raise LedgerError("resume phase_status is unsupported")


def _transition(state: ReleaseLoopState, event: str) -> ReleaseLoopState:
    if not event or not isinstance(event, str):
        raise LedgerError("ledger event is empty or invalid")
    _validate_state(state)
    if event in {"merge", "push", "archive", "ship", "retro"}:
        raise LedgerError("Ship and Retro actions are external to this adapter")
    if state.phase == "done":
        raise LedgerError("done ledger cannot transition")

    if event == "block":
        if state.phase == "blocked":
            raise LedgerError("ledger is already blocked")
        return replace(
            state,
            phase="blocked",
            phase_status="blocked",
            resume_phase=state.phase,
            resume_phase_status=state.phase_status,
        )
    if state.phase == "blocked":
        if (
            event != "resume"
            or state.resume_phase is None
            or state.resume_phase_status is None
        ):
            raise LedgerError("blocked ledger requires resume context")
        return replace(
            state,
            phase=state.resume_phase,
            phase_status=state.resume_phase_status,
            resume_phase=None,
            resume_phase_status=None,
        )

    if event == "request-design-approval" and state.phase == "design":
        if state.phase_status != "in-progress":
            raise LedgerError("design approval request requires in-progress state")
        return replace(state, phase_status="waiting-user")
    if event == "request-ship-approval" and state.phase == "ship":
        if state.phase_status != "in-progress":
            raise LedgerError("Ship approval request requires in-progress state")
        return replace(state, phase_status="waiting-user")
    if event == "design-approved" and state.phase == "design":
        if state.phase_status not in {"in-progress", "waiting-user"}:
            raise LedgerError("design approval requires an active design phase")
        return replace(state, phase="plan", phase_status="in-progress")
    if event == "plan-approved" and state.phase == "plan":
        if state.phase_status != "in-progress":
            raise LedgerError("plan approval requires in-progress state")
        return replace(state, phase="implement", phase_status="in-progress")
    if event == "implementation-complete" and state.phase == "implement":
        if state.phase_status != "in-progress" or state.next_stage is not None:
            raise LedgerError("implementation is incomplete")
        return replace(state, phase="review", phase_status="in-progress")
    if event == "review-clean" and state.phase == "review":
        if state.phase_status != "in-progress":
            raise LedgerError("review completion requires in-progress state")
        return replace(state, phase="ship", phase_status="in-progress")
    if event == "ship-approved" and state.phase == "ship":
        if state.phase_status != "waiting-user":
            raise LedgerError("Ship approval requires waiting-user state")
        return replace(state, phase="retro", phase_status="in-progress")
    if event == "retro-complete" and state.phase == "retro":
        if state.phase_status != "in-progress":
            raise LedgerError("Retro completion requires in-progress state")
        return replace(state, phase="done", phase_status="complete")

    if state.phase == "implement" and state.phase_status == "in-progress":
        prefix, separator, stage = event.partition(":")
        if separator and prefix in {"stage-start", "stage-complete"}:
            if stage not in MODEL_STAGE_ORDER:
                raise LedgerError(f"stage is unsupported: {stage}")
            if prefix == "stage-start":
                if state.current_stage is not None:
                    raise LedgerError("another stage is already active")
                if state.next_stage != stage:
                    raise LedgerError("stage order does not match resume marker")
                return replace(state, current_stage=stage)
            if state.current_stage != stage:
                raise LedgerError("stage completion does not match current marker")
            return replace(
                state,
                current_stage=None,
                completed_stages=(*state.completed_stages, stage),
            )

    raise LedgerError(f"event is not valid for ledger state: {event}")


def next_transition(state: ReleaseLoopState, event: str) -> TransitionResult:
    """Apply one closed event and return the next validated state."""

    next_state = _transition(state, event)
    _validate_state(next_state)
    return TransitionResult(state=next_state, event=event)


def _parse_ledger(text: str) -> ReleaseLoopState:
    parts = text.split("---", 2)
    if len(parts) != 3 or not text.startswith("---"):
        raise LedgerError("ledger frontmatter delimiters are invalid")
    values: dict[str, object] = {}
    current_key: Optional[str] = None
    for line in parts[1].splitlines():
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("-"):
            if current_key != "completed_stages" or not stripped.startswith("- "):
                raise LedgerError("ledger list field is invalid")
            values.setdefault(current_key, [])
            assert isinstance(values[current_key], list)
            values[current_key].append(stripped[2:].strip())
            continue
        if ":" not in line or line.startswith((" ", "\t")):
            raise LedgerError("ledger frontmatter contains an invalid line")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key not in _FIELDS or key in values:
            raise LedgerError(f"ledger field is unknown or duplicated: {key}")
        current_key = key
        if key == "completed_stages":
            if value not in {"", "[]"}:
                raise LedgerError("ledger completed_stages must be a list")
            values[key] = []
        elif value == "null":
            values[key] = None
        elif not value:
            raise LedgerError(f"ledger field is empty: {key}")
        else:
            values[key] = value
    if values.get("schema") != LEDGER_SCHEMA:
        raise LedgerError("ledger schema is unsupported")
    if set(values) != set(_FIELDS):
        raise LedgerError("ledger is missing a required field")
    raw_stages = values["completed_stages"]
    if not isinstance(raw_stages, list) or not all(
        isinstance(item, str) for item in raw_stages
    ):
        raise LedgerError("ledger completed_stages is invalid")
    try:
        state = ReleaseLoopState(
            phase=values["phase"],
            phase_status=values["phase_status"],
            current_stage=values["current_stage"],
            completed_stages=tuple(raw_stages),
            resume_phase=values["resume_phase"],
            resume_phase_status=values["resume_phase_status"],
            schema=values["schema"],
        )
    except TypeError as exc:
        raise LedgerError("ledger fields have invalid types") from exc
    _validate_state(state)
    return state


def load_ledger(path: Path) -> Optional[ReleaseLoopState]:
    """Read a release-loop ledger or return ``None`` when it does not exist."""

    ledger_path = Path(path)
    if not ledger_path.exists():
        return None
    try:
        with ledger_path.open("r", encoding="utf-8", newline=None) as handle:
            text = handle.read()
    except (OSError, UnicodeError) as exc:
        raise LedgerError(f"cannot read ledger: {ledger_path}") from exc
    return _parse_ledger(text)


def _serialize_ledger(state: ReleaseLoopState) -> str:
    _validate_state(state)
    lines = [
        "---",
        f"schema: {state.schema}",
        f"phase: {state.phase}",
        f"phase_status: {state.phase_status}",
        f"current_stage: {state.current_stage or 'null'}",
        "completed_stages:",
    ]
    lines.extend(f"  - {stage}" for stage in state.completed_stages)
    if not state.completed_stages:
        lines[-1] = "completed_stages: []"
    lines.extend(
        [
            f"resume_phase: {state.resume_phase or 'null'}",
            f"resume_phase_status: {state.resume_phase_status or 'null'}",
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def save_ledger(state: ReleaseLoopState, path: Path) -> None:
    """Persist a validated ledger through same-directory atomic replacement."""

    ledger_path = Path(path)
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            dir=str(ledger_path.parent), prefix=f".{ledger_path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_serialize_ledger(state))
            os.replace(temporary, ledger_path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    except LedgerError:
        raise
    except BaseException as exc:
        raise LedgerError(f"atomic ledger write failed: {ledger_path}") from exc
