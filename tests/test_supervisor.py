"""Contract tests for bounded independent-run supervision."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_orchestration.supervisor import (
    RunSpec,
    SupervisionPlan,
    SupervisionValidationError,
    run_supervised,
    validate_plan,
)


def _command(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def _plan(tmp_path: Path, count: int = 4, *, concurrency: int = 2):
    runs = tuple(
        RunSpec(
            run_id=f"run-{index}",
            command=_command(
                "from pathlib import Path; import time; "
                "Path('started').write_text(str(time.time())); "
                "time.sleep(0.12); "
                "Path('finished').write_text(str(time.time()))"
            ),
            workspace=tmp_path / f"workspace-{index}",
        )
        for index in range(count)
    )
    return SupervisionPlan(runs=runs, max_concurrency=concurrency)


def test_validate_plan_rejects_duplicate_identity_workspace_and_invalid_limit(
    tmp_path,
):
    base = _plan(tmp_path, count=2)
    duplicate_id = SupervisionPlan(
        runs=(
            base.runs[0],
            RunSpec("run-0", base.runs[1].command, base.runs[1].workspace),
        ),
        max_concurrency=2,
    )
    duplicate_workspace = SupervisionPlan(
        runs=(
            base.runs[0],
            RunSpec("run-1", base.runs[1].command, base.runs[0].workspace),
        ),
        max_concurrency=2,
    )
    invalid_limit = SupervisionPlan(runs=base.runs, max_concurrency=3)

    for plan, message in (
        (duplicate_id, "run_id"),
        (duplicate_workspace, "workspace"),
        (invalid_limit, "concurrency"),
    ):
        with pytest.raises(SupervisionValidationError, match=message):
            validate_plan(plan)


def test_validate_plan_rejects_string_argv_and_non_directory_parent(tmp_path):
    with pytest.raises(SupervisionValidationError, match="command"):
        validate_plan(
            SupervisionPlan(
                (RunSpec("string-argv", "echo", tmp_path / "workspace"),),
                max_concurrency=1,
            )
        )
    parent = tmp_path / "workspace-file"
    parent.write_text("not a directory")
    with pytest.raises(SupervisionValidationError, match="workspace"):
        validate_plan(
            SupervisionPlan(
                (RunSpec("file-parent", ("echo",), parent / "child"),),
                max_concurrency=1,
            )
        )


def test_run_supervised_bounds_concurrency_and_isolates_outputs(tmp_path):
    plan = _plan(tmp_path)
    output_root = tmp_path / "results"

    manifest = run_supervised(plan, output_root)

    assert manifest.exit_code == 0
    assert [result.status for result in manifest.results] == ["succeeded"] * len(
        plan.runs
    )
    intervals = []
    for run in plan.runs:
        started = float((run.workspace / "started").read_text())
        finished = float((run.workspace / "finished").read_text())
        intervals.append((started, finished))
        assert Path(manifest.result_for(run.run_id).output_path).exists()
        assert json.loads(
            (output_root / "supervision-result.json").read_text(encoding="utf-8")
        )["runs"]
    peak = max(
        sum(start <= point < finish for start, finish in intervals)
        for point in [value for interval in intervals for value in interval]
    )
    assert peak <= plan.max_concurrency
    assert all(run.workspace != output_root for run in plan.runs)


def test_run_supervised_rejects_preexisting_output_log_symlink(tmp_path):
    output_root = tmp_path / "results"
    (output_root / "runs").mkdir(parents=True)
    target = tmp_path / "outside.log"
    target.write_text("must remain unchanged")
    (output_root / "runs" / "run-0.log").symlink_to(target)
    plan = _plan(tmp_path, count=1, concurrency=1)

    with pytest.raises(SupervisionValidationError, match="output log"):
        run_supervised(plan, output_root)
    assert target.read_text() == "must remain unchanged"


def test_resume_rejects_inconsistent_succeeded_row(tmp_path):
    plan = _plan(tmp_path, count=1, concurrency=1)
    output_root = tmp_path / "results"
    run_supervised(plan, output_root)
    manifest_path = output_root / "supervision-result.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["runs"][0]["exit_code"] = 7
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SupervisionValidationError, match="succeeded"):
        run_supervised(plan, output_root, resume=True)


def test_failed_run_does_not_cancel_healthy_siblings(tmp_path):
    failed = RunSpec(
        "failed",
        _command("raise SystemExit(7)"),
        tmp_path / "failed-workspace",
    )
    healthy = RunSpec(
        "healthy",
        _command("from pathlib import Path; Path('healthy').write_text('ok')"),
        tmp_path / "healthy-workspace",
    )
    manifest = run_supervised(
        SupervisionPlan((failed, healthy), max_concurrency=2), tmp_path / "results"
    )

    assert manifest.exit_code == 1
    assert manifest.result_for("failed").status == "failed"
    assert manifest.result_for("healthy").status == "succeeded"
    assert (healthy.workspace / "healthy").read_text() == "ok"


def test_rate_limit_retry_is_bounded_and_recorded(tmp_path):
    command = _command(
        "from pathlib import Path; p=Path('attempts'); "
        "n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1)); "
        "raise SystemExit(75)"
    )
    spec = RunSpec("rate-limited", command, tmp_path / "workspace")
    manifest = run_supervised(
        SupervisionPlan((spec,), max_concurrency=1, rate_limit_retries=2),
        tmp_path / "results",
    )

    result = manifest.result_for("rate-limited")
    assert manifest.exit_code == 1
    assert result.status == "rate_limited"
    assert result.attempt_count == 3
    assert (spec.workspace / "attempts").read_text() == "3"


def _write_plan(path: Path, runs, *, max_concurrency: int = 2) -> None:
    payload = {
        "version": "supervision-plan/v1",
        "runs": [
            {
                "run_id": run.run_id,
                "command": list(run.command),
                "workspace": str(run.workspace),
            }
            for run in runs
        ],
        "max_concurrency": max_concurrency,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run_cli(
    plan: Path, output_root: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/supervise_orchestration.py",
            "--plan",
            str(plan),
            "--output-root",
            str(output_root),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_publishes_manifest_and_aggregates_exit_status(tmp_path):
    runs = (
        RunSpec(
            "success",
            _command("from pathlib import Path; Path('done').write_text('yes')"),
            tmp_path / "success-workspace",
        ),
        RunSpec(
            "failure", _command("raise SystemExit(4)"), tmp_path / "failure-workspace"
        ),
    )
    plan_path = tmp_path / "plan.json"
    output_root = tmp_path / "results"
    _write_plan(plan_path, runs)

    completed = _run_cli(plan_path, output_root)

    assert completed.returncode == 1, completed.stderr
    manifest = json.loads(
        (output_root / "supervision-result.json").read_text(encoding="utf-8")
    )
    assert {row["status"] for row in manifest["runs"]} == {"succeeded", "failed"}
    assert "input()" not in completed.stdout


def test_cli_rejects_invalid_plan_before_dispatch(tmp_path):
    marker = tmp_path / "dispatched"
    run = RunSpec(
        "would-run",
        _command(f"from pathlib import Path; Path({str(marker)!r}).write_text('yes')"),
        tmp_path / "workspace",
    )
    plan_path = tmp_path / "plan.json"
    output_root = tmp_path / "results"
    _write_plan(plan_path, (run,), max_concurrency=1)

    completed = _run_cli(plan_path, output_root, "--max-concurrency", "0")

    assert completed.returncode == 2
    assert not marker.exists()
    assert not (output_root / "supervision-result.json").exists()


def test_cli_resume_preserves_succeeded_rows_and_retries_unfinished(tmp_path):
    stable = RunSpec(
        "stable",
        _command(
            "from pathlib import Path; Path('stable').write_text('done', encoding='utf-8')"
        ),
        tmp_path / "stable-workspace",
    )
    retry = RunSpec(
        "retry",
        _command(
            "from pathlib import Path; p=Path('attempts'); "
            "n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1)); "
            "n == 0 and __import__('sys').exit(4)"
        ),
        tmp_path / "retry-workspace",
    )
    plan_path = tmp_path / "plan.json"
    output_root = tmp_path / "results"
    _write_plan(plan_path, (stable, retry))

    first = _run_cli(plan_path, output_root)
    stable_log = (output_root / "runs" / "stable.log").read_bytes()
    first_manifest = json.loads(
        (output_root / "supervision-result.json").read_text(encoding="utf-8")
    )

    resumed = _run_cli(plan_path, output_root, "--resume")

    assert first.returncode == 1, first.stderr
    assert resumed.returncode == 0, resumed.stderr
    assert (output_root / "runs" / "stable.log").read_bytes() == stable_log
    rows = {
        row["run_id"]: row
        for row in json.loads(
            (output_root / "supervision-result.json").read_text(encoding="utf-8")
        )["runs"]
    }
    first_rows = {row["run_id"]: row for row in first_manifest["runs"]}
    assert rows["stable"] == first_rows["stable"]
    assert rows["retry"]["status"] == "succeeded"
    assert rows["retry"]["attempt_count"] == 1


def test_cli_signal_cancels_owned_children_and_records_pending_rows(tmp_path):
    runs = tuple(
        RunSpec(
            f"run-{index}",
            _command("import time; time.sleep(5)"),
            tmp_path / f"workspace-{index}",
        )
        for index in range(2)
    )
    plan_path = tmp_path / "plan.json"
    output_root = tmp_path / "results"
    _write_plan(plan_path, runs, max_concurrency=1)
    process = subprocess.Popen(
        [
            sys.executable,
            "scripts/supervise_orchestration.py",
            "--plan",
            str(plan_path),
            "--output-root",
            str(output_root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.25)
    process.send_signal(signal.SIGINT)
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 130, (stdout, stderr)
    manifest = json.loads(
        (output_root / "supervision-result.json").read_text(encoding="utf-8")
    )
    assert manifest["cancelled"] is True
    assert {row["status"] for row in manifest["runs"]} <= {"cancelled", "not_started"}
