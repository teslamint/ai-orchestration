"""Durable run-state persistence: atomic save/load, fresh-rerun default, resume.

Per S3: re-running a project starts fresh by default; `--resume` continues
from persisted state, skipping completed stages. Writes are atomic via
`os.replace` so a crash mid-write never corrupts the last-good state.

`acquire_run_lock` guards against two concurrent processes racing on the
same `--project-name`/`--workspace`: without it, the second process's
`save_state` last-writer-wins over the first, silently discarding whichever
run wrote second-to-last. The lock is held via `fcntl.flock` directly on
the run-state directory's file descriptor, not a throwaway sibling file --
`flock` locks an open file description tied to an inode, so locking a
disposable `.lock` file left it vulnerable to external unlink/recreate (a
stray cleanup script, a workspace reset) minting a fresh inode and an
independent lock for a second process. Locking the directory that
actually holds `run_state.json` ties the lock's lifetime to the same
object the lock protects: an external actor cannot delete-and-recreate it
without first destroying the run state itself, a distinct, out-of-scope
failure. The OS releases the lock automatically if the holding process
crashes or is killed, so a stale lock can never wedge future runs.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

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


_STR_FIELDS = ("goal", "project_name")
_OPTIONAL_STR_FIELDS = ("current_stage", "pause_reason")
_DICT_FIELDS = ("config_snapshot", "outputs")


def _validate_run_state_payload(payload: Any, path: Path) -> None:
    """Validate a decoded JSON payload against RunState's persisted-boundary
    shape before construction.

    Raises `StateError` naming only the offending field and its expected
    versus actual type -- never the field's value, since a malformed field
    may itself carry sensitive data (e.g. a secret pasted into the wrong
    slot). This is the boundary check finding #2 required: a syntactically
    valid JSON document with a wrong-typed field (e.g. `"outputs": 12345`)
    must become a sanitized `StateError` here, not an uncaught `TypeError`
    surfacing later through every exception boundary above this call.
    """
    if not isinstance(payload, dict):
        raise StateError(
            f"run state at {path} must be a JSON object, got {type(payload).__name__}"
        )
    for name in _STR_FIELDS:
        if name in payload and not isinstance(payload[name], str):
            raise StateError(
                f"run state at {path} field '{name}' must be a string, "
                f"got {type(payload[name]).__name__}"
            )
    for name in _OPTIONAL_STR_FIELDS:
        if name in payload:
            value = payload[name]
            if value is not None and not isinstance(value, str):
                raise StateError(
                    f"run state at {path} field '{name}' must be a string "
                    f"or null, got {type(value).__name__}"
                )
    for name in _DICT_FIELDS:
        if name in payload and not isinstance(payload[name], dict):
            raise StateError(
                f"run state at {path} field '{name}' must be an object, "
                f"got {type(payload[name]).__name__}"
            )
    if "completed_stages" in payload:
        stages = payload["completed_stages"]
        if not isinstance(stages, list) or not all(
            isinstance(item, str) for item in stages
        ):
            raise StateError(
                f"run state at {path} field 'completed_stages' must be a "
                "list of strings"
            )
    if "logs" in payload:
        logs = payload["logs"]
        if not isinstance(logs, list) or not all(
            isinstance(item, dict) for item in logs
        ):
            raise StateError(
                f"run state at {path} field 'logs' must be a list of objects"
            )
    if "schema_version" in payload:
        version = payload["schema_version"]
        # bool is an int subclass in Python; exclude it explicitly so a
        # JSON `true`/`false` never silently passes as schema_version 1/0.
        if type(version) is not int:
            raise StateError(
                f"run state at {path} field 'schema_version' must be an "
                f"integer, got {type(version).__name__}"
            )


def load_state(path: Path) -> Optional[RunState]:
    """Load a `RunState` from `path`, or None if the file does not exist.

    Raises `StateError` for a present but corrupt file, distinguishing
    "no prior run" (None) from "prior run state is unreadable" (raise).
    Every field's type is validated against RunState's persisted shape
    before construction, so a syntactically valid but wrong-typed
    persisted file can never crash later with an uncaught `TypeError`.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"corrupt run state at {path}: {exc}") from exc
    _validate_run_state_payload(payload, path)
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


class RunLockedError(StateError):
    """Another process already holds the run lock for this state path."""


@contextlib.contextmanager
def acquire_run_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive, non-blocking lock guarding `path` for the duration
    of the `with` block.

    The lock is held on the project workspace directory, not the mutable
    `.ai_orchestration` state directory. `save_state()` may recreate that
    state directory after an external cleanup, so locking it would allow a
    second process to lock the replacement inode while the first still owns
    the old one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_dir = path.parent.parent
    lock_dir.mkdir(parents=True, exist_ok=True)
    while True:
        fd = os.open(str(lock_dir), os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise RunLockedError(
                f"another run already holds the lock for {path}"
            ) from exc
        try:
            fd_stat = os.fstat(fd)
            dir_stat = os.stat(lock_dir)
        except OSError:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            continue
        if (fd_stat.st_dev, fd_stat.st_ino) != (dir_stat.st_dev, dir_stat.st_ino):
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            continue
        break
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
