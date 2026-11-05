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
def _wait_for_path(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return path.exists()


def _plan(tmp_path: Path, count: int = 4, *, concurrency: int = 2):
    runs = tuple(
        RunSpec(
            run_id=f"run-{index}",
            command=_command(
                "from pathlib import Path; import time; "
                "Path('started').write_text(str(time.time())); "
                "time.sleep(0.25); "
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
            _command(
                "from pathlib import Path; import time; "
                "Path('started').touch(); time.sleep(30)"
            ),
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
    if not _wait_for_path(runs[0].workspace / "started"):
        process.kill()
        raise AssertionError("supervised child did not start before deadline")
    process.send_signal(signal.SIGINT)
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 130, (stdout, stderr)
    manifest = json.loads(
        (output_root / "supervision-result.json").read_text(encoding="utf-8")
    )
    assert manifest["cancelled"] is True
    assert {row["status"] for row in manifest["runs"]} <= {"cancelled", "not_started"}


def test_cli_defaults_concurrency_for_two_runs(tmp_path):
    runs = tuple(
        RunSpec(run_id, _command("pass"), tmp_path / f"{run_id}-workspace")
        for run_id in ("one", "two")
    )
    plan_path = tmp_path / "plan.json"
    output_root = tmp_path / "results"
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
    }
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run_cli(plan_path, output_root)

    assert completed.returncode == 0, completed.stderr


def test_validate_plan_rejects_case_only_run_id_and_workspace_collisions(tmp_path):
    runs = (
        RunSpec("Build", _command("pass"), tmp_path / "Work"),
        RunSpec("build", _command("pass"), tmp_path / "work"),
    )

    with pytest.raises(SupervisionValidationError, match="unique"):
        validate_plan(SupervisionPlan(runs, max_concurrency=2))


def test_validate_plan_rejects_case_only_workspace_collision(tmp_path):
    runs = (
        RunSpec("one", _command("pass"), tmp_path / "Work"),
        RunSpec("two", _command("pass"), tmp_path / "work"),
    )

    with pytest.raises(SupervisionValidationError, match="workspace"):
        validate_plan(SupervisionPlan(runs, max_concurrency=2))


def test_cli_records_worker_workspace_creation_failure(tmp_path, monkeypatch):
    import importlib.util

    from ai_orchestration import supervisor

    run = RunSpec("broken", _command("pass"), tmp_path / "workspace")
    plan_path = tmp_path / "plan.json"
    output_root = tmp_path / "results"
    _write_plan(plan_path, (run,), max_concurrency=1)
    module_spec = importlib.util.spec_from_file_location(
        "supervise_orchestration",
        Path("scripts/supervise_orchestration.py").resolve(),
    )
    assert module_spec is not None and module_spec.loader is not None
    script = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(script)
    original_mkdir = Path.mkdir

    def fail_workspace_mkdir(path, *args, **kwargs):
        if path == run.workspace:
            raise OSError("workspace creation denied")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(supervisor.Path, "mkdir", fail_workspace_mkdir)
    exit_code = script.main(
        ["--plan", str(plan_path), "--output-root", str(output_root)]
    )
    manifest = json.loads(
        (output_root / "supervision-result.json").read_text(encoding="utf-8")
    )

    assert exit_code == 1
    assert manifest["runs"][0]["status"] == "failed"
    assert manifest["runs"][0]["error"] == "worker failed: OSError"


def test_failed_child_with_429_output_is_not_rate_limited(tmp_path):
    run = RunSpec(
        "ordinary-failure",
        _command("print('429'); raise SystemExit(3)"),
        tmp_path / "workspace",
    )

    manifest = run_supervised(
        SupervisionPlan((run,), max_concurrency=1),
        tmp_path / "results",
    )

    assert manifest.result_for(run.run_id).status == "failed"
def test_library_default_concurrency_adapts_to_run_count(tmp_path):
    plan = SupervisionPlan(
        (RunSpec("one", _command("pass"), tmp_path / "one"),)
    )

    validate_plan(plan)

    assert plan.max_concurrency == 1


def test_post_dispatch_manifest_failure_does_not_cancel_siblings(tmp_path, monkeypatch):
    from ai_orchestration import supervisor

    failed = RunSpec(
        "manifest-fails",
        _command("import time; time.sleep(0.1); raise SystemExit(1)"),
        tmp_path / "failed",
    )
    healthy = RunSpec(
        "healthy-after-manifest-failure",
        _command(
            "import time; from pathlib import Path; time.sleep(0.3); "
            "Path('completed').touch()"
        ),
        tmp_path / "healthy",
    )
    original_write = supervisor._write_manifest
    failed_once = False

    def fail_terminal_write(path, payload):
        nonlocal failed_once
        if (
            not failed_once
            and any(row["status"] == "failed" for row in payload["runs"])
        ):
            failed_once = True
            raise OSError("manifest unavailable")
        return original_write(path, payload)

    monkeypatch.setattr(supervisor, "_write_manifest", fail_terminal_write)

    with pytest.raises(supervisor.SupervisionPersistenceError):
        run_supervised(
            SupervisionPlan((failed, healthy), max_concurrency=2),
            tmp_path / "results",
        )

    assert failed_once
    assert (healthy.workspace / "completed").exists()
def test_cli_reports_post_dispatch_persistence_failure_as_run_error(
    tmp_path, monkeypatch, capsys
):
    import importlib.util

    from ai_orchestration import supervisor

    run = RunSpec("broken-manifest", _command("raise SystemExit(1)"), tmp_path / "run")
    plan_path = tmp_path / "plan.json"
    output_root = tmp_path / "results"
    _write_plan(plan_path, (run,), max_concurrency=1)
    module_spec = importlib.util.spec_from_file_location(
        "supervise_orchestration",
        Path("scripts/supervise_orchestration.py").resolve(),
    )
    assert module_spec is not None and module_spec.loader is not None
    script = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(script)
    original_write = supervisor._write_manifest

    def fail_terminal_write(path, payload):
        if any(row["status"] == "failed" for row in payload["runs"]):
            raise OSError("manifest unavailable")
        return original_write(path, payload)

    monkeypatch.setattr(supervisor, "_write_manifest", fail_terminal_write)

    exit_code = script.main(
        ["--plan", str(plan_path), "--output-root", str(output_root)]
    )

    assert exit_code == 1
    assert "configuration error" not in capsys.readouterr().err


def test_output_root_overlap_check_is_case_insensitive(tmp_path, monkeypatch):
    from ai_orchestration import supervisor

    output_root = tmp_path / "results"
    workspace = tmp_path / "RESULTS" / "workspace"
    run = RunSpec("overlap", _command("pass"), workspace)
    original_resolve = Path.resolve

    def preserve_workspace_case(path, *args, **kwargs):
        if path == workspace:
            return workspace
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(supervisor.Path, "resolve", preserve_workspace_case)

    with pytest.raises(SupervisionValidationError, match="overlaps output root"):
        run_supervised(SupervisionPlan((run,)), output_root)
def test_worker_exception_row_uses_resolved_workspace_and_can_resume(
    tmp_path, monkeypatch
):
    from ai_orchestration import supervisor

    workspace = tmp_path / "parent" / ".." / "workspace"
    run = RunSpec("worker-error", _command("pass"), workspace)
    output_root = tmp_path / "results"

    def fail_run_one(*args, **kwargs):
        raise RuntimeError("simulated worker failure")

    monkeypatch.setattr(supervisor, "_run_one", fail_run_one)
    failed = run_supervised(SupervisionPlan((run,)), output_root)
    assert failed.results[0].workspace == str(workspace.resolve(strict=False))

    monkeypatch.undo()
    resumed = run_supervised(SupervisionPlan((run,)), output_root, resume=True)

    assert resumed.result_for(run.run_id).status == "succeeded"
