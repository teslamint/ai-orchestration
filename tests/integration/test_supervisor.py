"""Dogfood tests for bounded supervision across real child processes."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from ai_orchestration.supervisor import (
    RunSpec,
    SupervisionPlan,
    run_supervised,
)


def _timed_command(run_id: str, outcome: str, delay: float = 0.05) -> tuple[str, ...]:
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
                "Path('started').write_text(str(time.time())); time.sleep(0.08); "
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
    timer = threading.Timer(0.2, cancellation.set)
    timer.start()
    output_root = tmp_path / "results"
    cancelled = run_supervised(plan, output_root, cancel_event=cancellation)
    timer.join()

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
