"""Durable run-state persistence: atomic save/load, fresh-rerun default, resume.

Per S3: re-running a project starts fresh by default; `--resume` continues
from persisted state, skipping completed stages. Writes are atomic via
`os.replace` so a crash mid-write never corrupts the last-good state.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ai_orchestration.errors import StateError

STATE_SCHEMA_VERSION = 1


@dataclass
class RunState:
    """Persisted run state: goal, config snapshot, progress, and pause reason.

    Contains everything §Deferred/U4's Interfaces require so a fresh rerun
    and a resume are distinguishable: goal, config snapshot, completed
    stages, current stage, outputs, logs, pause reason, and schema version.
    """

    goal: str
    project_name: str
    config_snapshot: dict[str, Any]
    completed_stages: list[str] = field(default_factory=list)
    current_stage: Optional[str] = None
    outputs: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    pause_reason: Optional[str] = None
    schema_version: int = STATE_SCHEMA_VERSION


def save_state(state: RunState, path: Path) -> None:
    """Write `state` to `path` atomically via a same-directory temp file + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(state)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def load_state(path: Path) -> Optional[RunState]:
    """Load a `RunState` from `path`, or None if the file does not exist.

    Raises `StateError` for a present but corrupt file, distinguishing
    "no prior run" (None) from "prior run state is unreadable" (raise).
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"corrupt run state at {path}: {exc}") from exc
    try:
        return RunState(**payload)
    except TypeError as exc:
        raise StateError(
            f"run state at {path} has an incompatible shape: {exc}"
        ) from exc


def resolve_run_start(path: Path, *, resume: bool) -> RunState:
    """Resolve the starting state for a run: fresh by default, resume on request.

    - `resume=False` (default): always start fresh, regardless of any saved
      state on disk. This is S3's "starts fresh by default" rule.
    - `resume=True` with saved state present: continue from it.
    - `resume=True` with no saved state: starts fresh (nothing to resume).
    """
    fresh = RunState(
        goal="",
        project_name="",
        config_snapshot={},
        completed_stages=[],
        current_stage=None,
        outputs={},
        logs=[],
        pause_reason=None,
    )
    if not resume:
        return fresh
    saved = load_state(path)
    if saved is None:
        return fresh
    return saved
