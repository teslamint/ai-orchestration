"""Dogfood tests for bounded supervision across real child processes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from ai_orchestration.supervisor import (
    RunSpec,
    SupervisionPlan,
    run_supervised,
)
def _wait_for_path(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return path.exists()




def _timed_command(run_id: str, outcome: str, delay: float = 0.15) -> tuple[str, ...]:
    exit_statement = {
        "success": "",
        "failed": "raise SystemExit(7)",
        "rate_limited": "print('rate limit'); raise SystemExit(75)",
    }[outcome]
    code = (
        "from pathlib import Path; import time; "
        f"Path('identity').write_text({run_id!r}); "
        f"Path('started').write_text(str(time.time())); time.sleep({delay!r}); "
        f"Path('finished').write_text(str(time.time())); {exit_statement}"
    )
    return (sys.executable, "-c", code)


def _python_command(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def _peak_overlap(plan: SupervisionPlan) -> int:
    intervals = [
        (
            float((run.workspace / "started").read_text()),
            float((run.workspace / "finished").read_text()),
        )
        for run in plan.runs
        if (run.workspace / "started").exists()
        and (run.workspace / "finished").exists()
    ]
    points = [point for interval in intervals for point in interval]
    return max(
        sum(start <= point < finish for start, finish in intervals) for point in points
    )


def test_supervisor_isolates_concurrent_children(tmp_path):
    runs = tuple(
        RunSpec(
            f"isolated-{index}",
            _python_command(
                "from pathlib import Path; import time; "
                f"Path('identity').write_text({f'isolated-{index}'!r}); "
                "Path('started').write_text(str(time.time())); time.sleep(0.2); "
                "Path('finished').write_text(str(time.time()))"
            ),
            tmp_path / "workspaces" / f"run-{index}",
        )
        for index in range(12)
    )
    plan = SupervisionPlan(runs, max_concurrency=4)

    manifest = run_supervised(plan, tmp_path / "results")

    assert manifest.exit_code == 0
    assert len(manifest.results) == 12
    assert all(result.status == "succeeded" for result in manifest.results)
    assert _peak_overlap(plan) <= 4
    for run in runs:
        assert (run.workspace / "identity").read_text() == run.run_id
        assert Path(manifest.result_for(run.run_id).output_path).is_file()


def test_supervisor_preserves_mixed_terminal_results(tmp_path):
    definitions = ("success", "failed", "rate_limited") * 4
    runs = tuple(
        RunSpec(
            f"mixed-{index}",
            _timed_command(f"mixed-{index}", outcome),
            tmp_path / "workspaces" / f"run-{index}",
        )
        for index, outcome in enumerate(definitions)
    )
    plan = SupervisionPlan(
        runs,
        max_concurrency=5,
        rate_limit_retries=1,
    )

    manifest = run_supervised(plan, tmp_path / "results")

    assert manifest.exit_code == 1
    statuses = [result.status for result in manifest.results]
    assert statuses.count("succeeded") == 4
    assert statuses.count("failed") == 4
    assert statuses.count("rate_limited") == 4
    assert all(
        result.attempt_count == 2
        for result in manifest.results
        if result.status == "rate_limited"
    )
    assert _peak_overlap(plan) <= 5
    assert all((run.workspace / "identity").read_text() == run.run_id for run in runs)


def test_supervisor_cancellation_resume_preserves_completed_runs(tmp_path):
    fast = RunSpec(
        "fast",
        _python_command(
            "from pathlib import Path; Path('completed').write_text('yes')"
        ),
        tmp_path / "workspaces" / "fast",
    )
    slow_runs = tuple(
        RunSpec(
            f"slow-{index}",
            _python_command(
                "from pathlib import Path; import time; "
                "if_resume=Path('resume.flag').exists(); "
                "Path('started').write_text('yes'); "
                "time.sleep(1 if not if_resume else 0)"
            ),
            tmp_path / "workspaces" / f"slow-{index}",
        )
        for index in range(3)
    )
    plan = SupervisionPlan((fast, *slow_runs), max_concurrency=1)
    cancellation = threading.Event()

    def cancel_after_slow_run_starts():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if any((run.workspace / "started").exists() for run in slow_runs):
                cancellation.set()
                return
            time.sleep(0.02)
        cancellation.set()

    waiter = threading.Thread(target=cancel_after_slow_run_starts)
    waiter.start()
    output_root = tmp_path / "results"
    cancelled = run_supervised(plan, output_root, cancel_event=cancellation)
    waiter.join()

    assert cancelled.exit_code == 130
    assert cancelled.result_for("fast").status == "succeeded"
    fast_result = cancelled.result_for("fast")
    fast_log = Path(fast_result.output_path).read_bytes()
    for run in slow_runs:
        run.workspace.mkdir(parents=True, exist_ok=True)
        (run.workspace / "resume.flag").write_text("yes")

    resumed = run_supervised(plan, output_root, resume=True)

    assert resumed.exit_code == 0
    assert all(result.status == "succeeded" for result in resumed.results)
    assert resumed.result_for("fast") == fast_result
    assert Path(resumed.result_for("fast").output_path).read_bytes() == fast_log


def test_supervisor_dogfood_30_instances_and_concurrent_throughput(tmp_path):
    definitions = ("success",) * 22 + ("failed",) * 4 + ("rate_limited",) * 4

    def make_plan(prefix: str, concurrency: int) -> SupervisionPlan:
        return SupervisionPlan(
            tuple(
                RunSpec(
                    f"{prefix}-{index}",
                    _timed_command(f"{prefix}-{index}", outcome),
                    tmp_path / prefix / "workspaces" / f"run-{index}",
                )
                for index, outcome in enumerate(definitions)
            ),
            max_concurrency=concurrency,
            rate_limit_retries=1,
        )

    sequential_plan = make_plan("sequential", 1)
    concurrent_plan = make_plan("concurrent", 6)
    start = time.perf_counter()
    sequential = run_supervised(sequential_plan, tmp_path / "sequential-results")
    sequential_seconds = time.perf_counter() - start
    start = time.perf_counter()
    concurrent = run_supervised(concurrent_plan, tmp_path / "concurrent-results")
    concurrent_seconds = time.perf_counter() - start

    assert len(concurrent.results) == 30
    assert sequential.exit_code == concurrent.exit_code == 1
    for manifest in (sequential, concurrent):
        statuses = [result.status for result in manifest.results]
        assert statuses.count("succeeded") == 22
        assert statuses.count("failed") == 4
        assert statuses.count("rate_limited") == 4
    assert _peak_overlap(concurrent_plan) <= 6
    assert sequential_seconds >= concurrent_seconds * 1.2
    assert all(
        (run.workspace / "identity").read_text() == run.run_id
        for run in concurrent_plan.runs
    )


def test_supervisor_children_receive_devnull_stdin(tmp_path, monkeypatch):
    from ai_orchestration import supervisor

    original_popen = subprocess.Popen
    seen = []

    def recording_popen(*args, **kwargs):
        seen.append(kwargs.get("stdin"))
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(supervisor.subprocess, "Popen", recording_popen)
    run = RunSpec("stdin", _python_command("pass"), tmp_path / "workspace")

    run_supervised(
        SupervisionPlan((run,), max_concurrency=1),
        tmp_path / "results",
    )

    assert seen == [subprocess.DEVNULL]


def test_supervisor_keyboard_interrupt_cancels_running_child_group(tmp_path):
    pid_path = tmp_path / "workspace" / "child.pid"
    code = (
        "import os, pathlib, time; "
        "pathlib.Path('child.pid').write_text(str(os.getpid())); "
        "time.sleep(30)"
    )
    run = RunSpec("interrupt", _python_command(code), tmp_path / "workspace")
    def interrupt_after_child_starts():
        if _wait_for_path(pid_path):
            os.kill(os.getpid(), signal.SIGINT)

    timer = threading.Thread(target=interrupt_after_child_starts, daemon=True)
    timer.start()

    try:
        run_supervised(
            SupervisionPlan((run,), max_concurrency=1),
            tmp_path / "results",
        )
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("KeyboardInterrupt must propagate")

    child_pid = int(pid_path.read_text())
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("child process survived run cancellation")


def test_terminate_process_group_kills_grandchild_after_leader_exits(tmp_path):
    from ai_orchestration.supervisor import _terminate_process_group

    marker = tmp_path / "grandchild-survived"
    code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', "
        f'"import signal, time, pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
        f"time.sleep(1); pathlib.Path('{marker}').touch()\"] )"
    )
    process = subprocess.Popen(
        _python_command(code),
        start_new_session=True,
    )
    process.wait(timeout=10)
    _terminate_process_group(process, process.pid)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.02)
    assert not marker.exists()

@pytest.mark.parametrize("cancel", [False, True])
def test_supervisor_kills_grandchild_before_return(tmp_path, cancel):
    pid_file = tmp_path / "workspace" / "grandchild.pid"
    run = RunSpec(
        "grandchild",
        ("sh", "-c", "sleep 300 & echo $! > grandchild.pid; sleep 1"),
        pid_file.parent,
    )
    cancellation = threading.Event()

    def cancel_after_grandchild_starts():
        if _wait_for_path(pid_file):
            cancellation.set()

    timer = threading.Thread(target=cancel_after_grandchild_starts)
    if cancel:
        timer.start()

    result = run_supervised(
        SupervisionPlan((run,), max_concurrency=1),
        tmp_path / "results",
        cancel_event=cancellation,
    )
    if cancel:
        timer.join()

    assert result.result_for("grandchild").status == (
        "cancelled" if cancel else "succeeded"
    )
    grandchild_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("grandchild survived run_supervised")
