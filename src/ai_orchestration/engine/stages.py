"""Stage registry, approach-option parsing, and command execution/audit logs.

`parse_approach_options` is ported from the inline approach-parsing block in
the legacy CLI's `main()`: it recognizes `### Approach N: Title` / bullet
headings, strips template placeholders (`[Name]`), and deduplicates. Six
stages remain strictly sequential (§Interface: stage roles unchanged).
`CommandExecutor` ports the committed retry/audit-log behavior verbatim,
including the `retries=1` default (two total attempts), plus a configurable,
finite subprocess timeout (§Failure classes: "exceeds the stage timeout").
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ai_orchestration.errors import StateError
from ai_orchestration.utils.slug import generate_command_slug

# A hung shell command must not stall a run indefinitely. Matches the
# CLI-provider default (`providers/cli.py:DEFAULT_CLI_TIMEOUT_SECONDS`).
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300.0

STAGE_ORDER: tuple[str, ...] = (
    "brainstormer",
    "brainstorming_reviewer",
    "planner",
    "executor",
    "code_reviewer",
    "fixer",
)

_OPTION_PATTERN = re.compile(
    r"^###?\s*(approach|option|plan|접근\s*방식)\s*\d*:?\s*\S", re.IGNORECASE
)
_TITLE_EXTRACT_PATTERN = re.compile(
    r"^###?\s*(?:approach|option|plan|접근\s*방식)\s*\d*:?\s*(.*)$", re.IGNORECASE
)
_PLACEHOLDER_PATTERN = re.compile(r"\[.*?\]")
_HAS_REAL_CONTENT = re.compile(r"[가-힣a-zA-Z]")


def _has_real_title(line: str) -> bool:
    """True unless the heading's title is entirely a placeholder or empty."""
    match = _TITLE_EXTRACT_PATTERN.match(line)
    if not match:
        return True
    title_part = match.group(1)
    stripped = _PLACEHOLDER_PATTERN.sub("", title_part).strip()
    return bool(stripped) and bool(_HAS_REAL_CONTENT.search(stripped))


def parse_approach_options(text: str) -> list[str]:
    """Extract candidate approach option lines from brainstorming text.

    Tries three passes in order, matching the legacy fallback chain:
    1. Lines matching the approach/option/plan heading pattern.
    2. Bare `### ` headings (if pass 1 found nothing).
    3. `- **` bold-bullet lines (if pass 2 found nothing).
    Each pass deduplicates and rejects placeholder-only titles.
    """
    lines = [line.strip() for line in text.split("\n")]
    options: list[str] = []
    seen: set[str] = set()

    for line in lines:
        if _OPTION_PATTERN.match(line) and _has_real_title(line) and line not in seen:
            seen.add(line)
            options.append(line)

    if not options:
        for line in lines:
            if line.startswith("### ") and line not in seen and _has_real_title(line):
                seen.add(line)
                options.append(line)

    if not options:
        for line in lines:
            normalized = line.removeprefix("- **").split("**", 1)[0]
            title = normalized.split(":", 1)[-1]
            stripped_title = _PLACEHOLDER_PATTERN.sub("", title).strip()
            if (
                line.startswith("- **")
                and normalized not in seen
                and bool(_HAS_REAL_CONTENT.search(stripped_title))
            ):
                seen.add(normalized)
                options.append(line)
    return options


def _confirm(prompt: str) -> bool:
    """Interactive confirm, overridable in tests via monkeypatch."""
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def _kill_process_group(process: "subprocess.Popen[str]") -> None:
    """Kill `process`'s entire process session/group, not just the child.

    Matches `providers/cli.py`'s `_kill_process_group`: `process` was
    started with `start_new_session=True`, so its pid is also its process
    group id. Killing only the immediate child on timeout lets a
    descendant that forked off and inherited the same stdout/stderr pipes
    outlive the timeout and keep running.
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


@dataclass
class CommandExecutionLog:
    """Structured log entry for a single command execution attempt."""

    timestamp: str
    command: str
    cwd: Optional[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    attempt: int


@dataclass
class CommandExecutionSummary:
    """Summary of all attempts for a single command execution."""

    command_id: str
    command: str
    cwd: Optional[str]
    started_at: str
    finished_at: str
    total_attempts: int
    final_status: str
    final_exit_code: Optional[int]
    attempts: list[dict[str, Any]]


@dataclass
class CommandExecutor:
    """Executor for shell commands with auto-approve, retry, and structured logging.

    Ported verbatim from `orchestrator_cli.py`'s `CommandExecutor`:
    `retries=1` means two total attempts (`1 + retries`), both recorded in
    the JSON audit log. `timeout` bounds each attempt so a hung command
    cannot stall the run indefinitely. Each attempt runs in its own process
    session (`start_new_session=True`, matching the CLI-provider pattern in
    `providers/cli.py:_run_cli_subprocess`), so a timeout kills the whole
    process group via `os.killpg`, not just the immediate child -- a
    descendant that forks off and inherits the child's stdout/stderr pipes
    cannot outlive the timeout and hold those pipes open. A timeout is
    recorded as a failed attempt like any other command-execution error.
    """

    auto_approve: bool = False
    retries: int = 1
    timeout: Optional[float] = DEFAULT_COMMAND_TIMEOUT_SECONDS
    log_directory: Path = field(default_factory=lambda: Path("execution_logs"))
    _execution_counter: int = field(default=0, init=False)

    def __post_init__(self):
        self.log_directory = Path(self.log_directory)
        self.log_directory.mkdir(parents=True, exist_ok=True)

    def _generate_command_id(self, command: str) -> str:
        self._execution_counter += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        slug = generate_command_slug(command)
        return f"{timestamp}_{self._execution_counter:04d}_{slug}"

    def _write_execution_log(self, summary: CommandExecutionSummary) -> Path:
        log_file = self.log_directory / f"{summary.command_id}.json"
        log_data = {
            "command_id": summary.command_id,
            "command": summary.command,
            "cwd": summary.cwd,
            "started_at": summary.started_at,
            "finished_at": summary.finished_at,
            "total_attempts": summary.total_attempts,
            "final_status": summary.final_status,
            "final_exit_code": summary.final_exit_code,
            "attempts": summary.attempts,
        }
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
        return log_file

    def run(
        self, command: str, cwd: Optional[str] = None
    ) -> tuple[bool, str, list[CommandExecutionLog]]:
        """Execute `command` with optional confirmation, retries, and audit logging.

        Returns `(success, output, logs)`.
        """
        command_id = self._generate_command_id(command)
        started_at = datetime.now().isoformat()
        logs: list[CommandExecutionLog] = []
        attempts_data: list[dict[str, Any]] = []

        if not self.auto_approve:
            should_run = _confirm(f"Do you want to execute this command: {command}?")
            if not should_run:
                finished_at = datetime.now().isoformat()
                summary = CommandExecutionSummary(
                    command_id=command_id,
                    command=command,
                    cwd=cwd,
                    started_at=started_at,
                    finished_at=finished_at,
                    total_attempts=0,
                    final_status="skipped",
                    final_exit_code=None,
                    attempts=[],
                )
                self._write_execution_log(summary)
                return False, "Command execution skipped by user.", []

        max_attempts = 1 + self.retries
        final_status = "failed"
        final_exit_code: Optional[int] = None
        final_output = ""

        for attempt in range(max_attempts):
            timestamp = datetime.now().isoformat()
            start_time = time.monotonic()
            try:
                command_args = shlex.split(command)
                process = subprocess.Popen(
                    command_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    cwd=cwd,
                    start_new_session=True,
                )
                try:
                    stdout, stderr = process.communicate(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    _kill_process_group(process)
                    raise
                duration_ms = int((time.monotonic() - start_time) * 1000)
                log_entry = CommandExecutionLog(
                    timestamp=timestamp,
                    command=command,
                    cwd=cwd,
                    exit_code=process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=duration_ms,
                    attempt=attempt + 1,
                )
                logs.append(log_entry)
                attempts_data.append(
                    {
                        "timestamp": log_entry.timestamp,
                        "attempt": log_entry.attempt,
                        "exit_code": log_entry.exit_code,
                        "stdout": log_entry.stdout,
                        "stderr": log_entry.stderr,
                        "duration_ms": log_entry.duration_ms,
                    }
                )
                if process.returncode == 0:
                    final_status = "success"
                    final_exit_code = process.returncode
                    final_output = stdout.strip()
                    break
                final_exit_code = process.returncode
                final_output = (
                    f"Command failed with exit code {process.returncode}: {stderr}"
                )
            except subprocess.TimeoutExpired:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                log_entry = CommandExecutionLog(
                    timestamp=timestamp,
                    command=command,
                    cwd=cwd,
                    exit_code=-1,
                    stdout="",
                    stderr=f"Command timed out after {self.timeout}s",
                    duration_ms=duration_ms,
                    attempt=attempt + 1,
                )
                logs.append(log_entry)
                attempts_data.append(
                    {
                        "timestamp": log_entry.timestamp,
                        "attempt": log_entry.attempt,
                        "exit_code": log_entry.exit_code,
                        "stdout": log_entry.stdout,
                        "stderr": log_entry.stderr,
                        "duration_ms": log_entry.duration_ms,
                    }
                )
                final_exit_code = -1
                final_output = f"Command timed out after {self.timeout}s"
            except Exception as exc:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                log_entry = CommandExecutionLog(
                    timestamp=timestamp,
                    command=command,
                    cwd=cwd,
                    exit_code=-1,
                    stdout="",
                    stderr=str(exc),
                    duration_ms=duration_ms,
                    attempt=attempt + 1,
                )
                logs.append(log_entry)
                attempts_data.append(
                    {
                        "timestamp": log_entry.timestamp,
                        "attempt": log_entry.attempt,
                        "exit_code": log_entry.exit_code,
                        "stdout": log_entry.stdout,
                        "stderr": log_entry.stderr,
                        "duration_ms": log_entry.duration_ms,
                    }
                )
                final_exit_code = -1
                final_output = f"Command execution error: {exc}"

        finished_at = datetime.now().isoformat()
        summary = CommandExecutionSummary(
            command_id=command_id,
            command=command,
            cwd=cwd,
            started_at=started_at,
            finished_at=finished_at,
            total_attempts=len(attempts_data),
            final_status=final_status,
            final_exit_code=final_exit_code,
            attempts=attempts_data,
        )
        self._write_execution_log(summary)
        return final_status == "success", final_output, logs


def execute_stage(
    stage_name: str,
    *,
    handler: Callable[[], None],
    completed_stages: list[str],
) -> None:
    """Run one stage's handler and append it to the completed-stages list.

    A thin wrapper so callers (CLI, U6 integration tests) get one uniform
    call shape and a consistent completed-stages side effect regardless of
    which stage runs.
    """
    handler()
    completed_stages.append(stage_name)


def run_pipeline(
    *,
    handlers: dict[str, Callable[[], None]],
    completed_stages: list[str],
    start_stage: Optional[str],
    skip_review: bool = False,
) -> list[str]:
    """Run the six-stage pipeline in order, honoring resume and skip-review.

    Stages already present in `completed_stages` before `start_stage` are
    not re-run (resume, S3). When `start_stage` is None the pipeline runs
    from the beginning. `skip_review` omits `code_reviewer` and `fixer`
    (`--skip-review`), matching the legacy CLI's Stage 5-6 gate.

    Raises `StateError` if `start_stage` is not a valid stage name, or is
    a stage excluded from the active order by `skip_review` (e.g. resuming
    at "code_reviewer" while `--skip-review` is set) -- an unavailable
    resume point must fail loudly, not silently start from the wrong index.
    """
    order = STAGE_ORDER
    if skip_review:
        order = tuple(s for s in order if s not in ("code_reviewer", "fixer"))

    if start_stage is None:
        start_index = 0
    else:
        try:
            start_index = order.index(start_stage)
        except ValueError as exc:
            raise StateError(
                f"start_stage {start_stage!r} is not available in the "
                f"current pipeline order {order!r}"
            ) from exc

    for stage_name in order[start_index:]:
        if stage_name in completed_stages:
            continue
        handler = handlers[stage_name]
        execute_stage(stage_name, handler=handler, completed_stages=completed_stages)

    return completed_stages
