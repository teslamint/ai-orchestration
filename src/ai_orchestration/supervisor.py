"""Bounded supervision for independent, non-interactive child runs."""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RATE_LIMIT_EXIT = 75
_ERROR_LIMIT = 160
_STATUSES = {"succeeded", "failed", "rate_limited", "cancelled", "not_started"}


class SupervisionValidationError(ValueError):
    """A supervision plan cannot be dispatched safely."""


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    command: tuple[str, ...]
    workspace: Path
    _command_was_scalar: bool = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        scalar = isinstance(self.command, (str, bytes))
        object.__setattr__(self, "_command_was_scalar", scalar)
        object.__setattr__(
            self,
            "command",
            (self.command,) if scalar else tuple(self.command),
        )
        object.__setattr__(self, "workspace", Path(self.workspace))


@dataclass(frozen=True)
class SupervisionPlan:
    runs: tuple[RunSpec, ...]
    max_concurrency: int = 4
    rate_limit_retries: int = 0
    rate_limit_delay: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "runs", tuple(self.runs))


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    workspace: str
    output_path: str
    attempt_count: int
    exit_code: int | None
    error: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "workspace": self.workspace,
            "output_path": self.output_path,
            "attempt_count": self.attempt_count,
            "exit_code": self.exit_code,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: object) -> RunResult:
        if not isinstance(payload, dict):
            raise SupervisionValidationError("manifest run rows must be objects")
        required = {
            "run_id",
            "status",
            "workspace",
            "output_path",
            "attempt_count",
            "exit_code",
            "error",
        }
        if set(payload) != required:
            raise SupervisionValidationError("manifest run row has an invalid shape")
        run_id = payload["run_id"]
        status = payload["status"]
        workspace = payload["workspace"]
        output_path = payload["output_path"]
        attempt_count = payload["attempt_count"]
        exit_code = payload["exit_code"]
        error = payload["error"]
        if (
            not isinstance(run_id, str)
            or not isinstance(status, str)
            or status not in _STATUSES
            or not isinstance(workspace, str)
            or not isinstance(output_path, str)
            or type(attempt_count) is not int
            or attempt_count < 0
            or (exit_code is not None and type(exit_code) is not int)
            or (error is not None and not isinstance(error, str))
        ):
            raise SupervisionValidationError("manifest run row contains invalid values")
        return cls(
            run_id,
            status,
            workspace,
            output_path,
            attempt_count,
            exit_code,
            error,
        )


@dataclass(frozen=True)
class SupervisionManifest:
    results: tuple[RunResult, ...]
    manifest_path: Path
    cancelled: bool = False

    @property
    def exit_code(self) -> int:
        if self.cancelled or any(
            result.status == "cancelled" for result in self.results
        ):
            return 130
        return 0 if all(result.status == "succeeded" for result in self.results) else 1

    def result_for(self, run_id: str) -> RunResult:
        for result in self.results:
            if result.run_id == run_id:
                return result
        raise KeyError(run_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": "supervision-result/v1",
            "cancelled": self.cancelled,
            "exit_code": self.exit_code,
            "runs": [result.to_dict() for result in self.results],
        }


def validate_plan(plan: SupervisionPlan) -> None:
    """Validate all dispatch inputs before starting a child process."""
    if not isinstance(plan, SupervisionPlan):
        raise SupervisionValidationError("plan must be a SupervisionPlan")
    if not plan.runs:
        raise SupervisionValidationError("plan must contain at least one run")
    if type(plan.max_concurrency) is not int or not (
        1 <= plan.max_concurrency <= len(plan.runs)
    ):
        raise SupervisionValidationError(
            "concurrency must be between 1 and the number of runs"
        )
    if type(plan.rate_limit_retries) is not int or plan.rate_limit_retries < 0:
        raise SupervisionValidationError("rate-limit retries must be non-negative")
    if (
        not isinstance(plan.rate_limit_delay, (int, float))
        or isinstance(plan.rate_limit_delay, bool)
        or not math.isfinite(plan.rate_limit_delay)
        or plan.rate_limit_delay < 0
    ):
        raise SupervisionValidationError(
            "rate-limit delay must be finite and non-negative"
        )

    identities: set[str] = set()
    workspaces: set[Path] = set()
    for run in plan.runs:
        if not isinstance(run, RunSpec):
            raise SupervisionValidationError("plan runs must be RunSpec values")
        if not isinstance(run.run_id, str) or not _RUN_ID.fullmatch(run.run_id):
            raise SupervisionValidationError(
                "run_id must be a safe non-empty identifier"
            )
        if run.run_id in identities:
            raise SupervisionValidationError("run_id values must be unique")
        identities.add(run.run_id)
        if (
            run._command_was_scalar
            or not run.command
            or any(
                not isinstance(part, str) or not part or "\0" in part
                for part in run.command
            )
        ):
            raise SupervisionValidationError(
                f"command for {run.run_id} must contain non-empty strings"
            )
        workspace = Path(run.workspace)
        if not workspace.is_absolute():
            raise SupervisionValidationError(
                f"workspace for {run.run_id} must be absolute"
            )
        if workspace.exists() and workspace.is_symlink():
            raise SupervisionValidationError(
                f"workspace for {run.run_id} must not be a symlink"
            )
        if workspace.exists() and not workspace.is_dir():
            raise SupervisionValidationError(
                f"workspace for {run.run_id} must be a directory"
            )
        _reject_symlink_components(workspace, label="workspace")
        normalized = workspace.resolve(strict=False)
        if normalized in workspaces:
            raise SupervisionValidationError("workspace values must be unique")
        if any(
            _is_relative_to(normalized, existing)
            or _is_relative_to(existing, normalized)
            for existing in workspaces
        ):
            raise SupervisionValidationError("workspace values must not overlap")
        workspaces.add(normalized)


def run_supervised(
    plan: SupervisionPlan,
    output_root: Path,
    *,
    resume: bool = False,
    cancel_event: threading.Event | None = None,
) -> SupervisionManifest:
    """Run independent child commands with bounded concurrency."""
    validate_plan(plan)
    root = Path(output_root).absolute()
    _reject_symlink_components(root)
    if root.exists() and root.is_symlink():
        raise SupervisionValidationError("output root must not be a symlink")
    if root.exists() and not root.is_dir():
        raise SupervisionValidationError("output root must be a directory")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    runs_root = root / "runs"
    if runs_root.exists() and runs_root.is_symlink():
        raise SupervisionValidationError("runs output directory must not be a symlink")
    if runs_root.exists() and not runs_root.is_dir():
        raise SupervisionValidationError("runs output path must be a directory")
    runs_root.mkdir(parents=True, exist_ok=True)
    for run in plan.runs:
        workspace = Path(run.workspace).resolve(strict=False)
        if _is_relative_to(workspace, root) or _is_relative_to(root, workspace):
            raise SupervisionValidationError(
                f"workspace for {run.run_id} overlaps output root"
            )

    cancellation = cancel_event or threading.Event()
    lock = threading.Lock()
    manifest_path = root / "supervision-result.json"
    if manifest_path.exists() and manifest_path.is_symlink():
        raise SupervisionValidationError("result manifest must not be a symlink")
    if manifest_path.exists() and not manifest_path.is_file():
        raise SupervisionValidationError("result manifest path must be a file")
    for run in plan.runs:
        output_path = _output_path(root, run.run_id)
        if output_path.is_symlink():
            raise SupervisionValidationError(
                f"output log for {run.run_id} must not be a symlink"
            )
        if output_path.exists() and not output_path.is_file():
            raise SupervisionValidationError(
                f"output log for {run.run_id} must be a file"
            )
    results = {run.run_id: _not_started_result(run, root) for run in plan.runs}
    if resume and manifest_path.exists():
        for result in _load_resume_results(manifest_path, plan, root):
            if result.status == "succeeded":
                results[result.run_id] = result

    def persist() -> None:
        snapshot = SupervisionManifest(
            tuple(results[run.run_id] for run in plan.runs),
            manifest_path,
            cancellation.is_set(),
        )
        _write_manifest(manifest_path, snapshot.to_dict())

    persist()

    def update(result: RunResult) -> None:
        with lock:
            results[result.run_id] = result
            persist()

    def worker(run: RunSpec) -> None:
        if results[run.run_id].status == "succeeded":
            return
        if cancellation.is_set():
            update(
                RunResult(
                    run.run_id,
                    "not_started",
                    str(Path(run.workspace).resolve(strict=False)),
                    str(_output_path(root, run.run_id)),
                    0,
                    None,
                    None,
                )
            )
            return
        result = _run_one(run, root, plan, cancellation)
        update(result)

    with ThreadPoolExecutor(max_workers=plan.max_concurrency) as pool:
        futures = [pool.submit(worker, run) for run in plan.runs]
        for future in futures:
            future.result()
    with lock:
        persist()
    return SupervisionManifest(
        tuple(results[run.run_id] for run in plan.runs),
        manifest_path,
        cancellation.is_set(),
    )


def _load_resume_results(
    manifest_path: Path, plan: SupervisionPlan, output_root: Path
) -> tuple[RunResult, ...]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupervisionValidationError("resume manifest is unreadable") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != "supervision-result/v1"
    ):
        raise SupervisionValidationError("resume manifest has an unsupported version")
    rows = payload.get("runs")
    if not isinstance(rows, list) or len(rows) != len(plan.runs):
        raise SupervisionValidationError("resume manifest does not match the plan")
    by_id: dict[str, RunResult] = {}
    expected_workspaces = {
        run.run_id: str(Path(run.workspace).resolve(strict=False)) for run in plan.runs
    }
    expected_outputs = {
        run.run_id: str(_output_path(output_root, run.run_id)) for run in plan.runs
    }
    for raw_row in rows:
        result = RunResult.from_dict(raw_row)
        if result.run_id in by_id or result.run_id not in expected_workspaces:
            raise SupervisionValidationError(
                "resume manifest run identities do not match"
            )
        if (
            result.workspace != expected_workspaces[result.run_id]
            or result.output_path != expected_outputs[result.run_id]
        ):
            raise SupervisionValidationError(
                "resume manifest paths do not match the plan"
            )
        if result.status == "succeeded":
            if (
                result.attempt_count < 1
                or result.exit_code != 0
                or result.error is not None
            ):
                raise SupervisionValidationError(
                    f"resume manifest succeeded row for {result.run_id} is inconsistent"
                )
            output_path = Path(result.output_path)
            if (
                not output_path.exists()
                or output_path.is_symlink()
                or not output_path.is_file()
            ):
                raise SupervisionValidationError(
                    f"resume output for {result.run_id} is missing or unsafe"
                )
        by_id[result.run_id] = result
    if set(by_id) != set(expected_workspaces):
        raise SupervisionValidationError("resume manifest run identities do not match")
    return tuple(by_id[run.run_id] for run in plan.runs)


def _run_one(
    run: RunSpec,
    output_root: Path,
    plan: SupervisionPlan,
    cancellation: threading.Event,
) -> RunResult:
    workspace = Path(run.workspace).resolve(strict=False)
    workspace.mkdir(parents=True, exist_ok=True)
    output_path = _output_path(output_root, run.run_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = 0
    while True:
        if cancellation.is_set():
            return _result(
                run, workspace, output_path, "cancelled", attempts, None, None
            )
        attempts += 1
        with output_path.open("ab") as output:
            try:
                process = subprocess.Popen(
                    run.command,
                    cwd=workspace,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env=os.environ.copy(),
                    start_new_session=True,
                )
            except OSError as exc:
                return _result(
                    run,
                    workspace,
                    output_path,
                    "failed",
                    attempts,
                    None,
                    f"process start failed: {exc.__class__.__name__}",
                )
            exit_code = _wait_for_process(process, cancellation)
        if cancellation.is_set():
            return _result(
                run, workspace, output_path, "cancelled", attempts, exit_code, None
            )
        if exit_code == 0:
            return _result(run, workspace, output_path, "succeeded", attempts, 0, None)
        rate_limited = _is_rate_limited(exit_code, output_path)
        if rate_limited and attempts <= plan.rate_limit_retries:
            if _wait_for_retry(plan.rate_limit_delay, cancellation):
                return _result(
                    run, workspace, output_path, "cancelled", attempts, exit_code, None
                )
            continue
        status = "rate_limited" if rate_limited else "failed"
        return _result(
            run,
            workspace,
            output_path,
            status,
            attempts,
            exit_code,
            f"child exited with code {exit_code}",
        )


def _wait_for_process(
    process: subprocess.Popen[bytes], cancellation: threading.Event
) -> int:
    while True:
        try:
            return process.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            if cancellation.is_set():
                _terminate_process_group(process)
                return process.wait()


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()


def _wait_for_retry(delay: float, cancellation: threading.Event) -> bool:
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        if cancellation.wait(timeout=min(0.05, max(0, deadline - time.monotonic()))):
            return True
    return cancellation.is_set()


def _is_rate_limited(exit_code: int, output_path: Path) -> bool:
    if exit_code == _RATE_LIMIT_EXIT:
        return True
    try:
        text = output_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lowered = text.lower()
    return "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered


def _result(
    run: RunSpec,
    workspace: Path,
    output_path: Path,
    status: str,
    attempts: int,
    exit_code: int | None,
    error: str | None,
) -> RunResult:
    return RunResult(
        run.run_id,
        status,
        str(workspace),
        str(output_path),
        attempts,
        exit_code,
        error[:_ERROR_LIMIT] if error else None,
    )


def _not_started_result(run: RunSpec, output_root: Path) -> RunResult:
    return RunResult(
        run.run_id,
        "not_started",
        str(Path(run.workspace).resolve(strict=False)),
        str(_output_path(output_root, run.run_id)),
        0,
        None,
        None,
    )


def _output_path(root: Path, run_id: str) -> Path:
    return root / "runs" / f"{run_id}.log"


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _reject_symlink_components(path: Path, *, label: str = "path") -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise SupervisionValidationError(
                f"symlinked {label} component is not allowed: {current}"
            )
        if current.exists() and not current.is_dir():
            raise SupervisionValidationError(
                f"{label} path component must be a directory: {current}"
            )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
