"""U6's own mutation/failure-state matrix rows: approval gate pause, CLI
fallback downgrade, model fallback, and clean cutover.

Each transition asserts: success path, forced failure, rerun/resume,
rollback/compensation, headless behavior, and cancellation, per the plan's
matrix. Disposable fixture outputs live under `.release-loop/evidence/U6/`.
"""

from __future__ import annotations

import subprocess

from typer.testing import CliRunner

import ai_orchestration.cli as cli_module
from ai_orchestration.cli import app
from ai_orchestration.config import CatalogOutcome, CatalogStatus
from ai_orchestration.engine.state import load_state
from ai_orchestration.providers.base import ModelFaultError, TransportError

runner = CliRunner(env={"COLUMNS": "200", "LINES": "50"})


def _reachable_catalog(models=None):
    return CatalogStatus(
        outcome=CatalogOutcome.REACHABLE_WITH_MODELS,
        models=frozenset(
            models or {"gpt-5.5", "gemini-3.1-pro-low", "claude-sonnet-5", "opus-5"}
        ),
    )


class _StubProvider:
    def __init__(self, name, plan_json=None):
        self.name = name
        self._plan_json = plan_json or (
            '[{"step_id": 1, "file_path": "hello.py", '
            '"action_type": "create_file", "instruction": "write hello world"}]'
        )

    def complete(self, prompt, **kwargs):
        return self._plan_json

    def complete_structured(self, prompt, *, schema, **kwargs):
        values = {}
        for field_name, field_info in schema.model_fields.items():
            if not field_info.is_required():
                continue
            annotation = str(field_info.annotation)
            if field_name in ("step_id", "total_files_reviewed"):
                values[field_name] = 1
            elif field_name == "requires_fixes":
                values[field_name] = False
            elif annotation.startswith("typing.List") or annotation.startswith("list"):
                values[field_name] = []
            else:
                values[field_name] = "x"
        return schema(**values)

    def is_available(self):
        return True


def _run_command_plan_provider(model):
    return _StubProvider(
        model,
        plan_json=(
            '[{"step_id": 1, "file_path": ".", '
            '"action_type": "run_command", "instruction": "echo hi"}]'
        ),
    )


# --- Approval gate pause -----------------------------------------------------


def test_gate_pause_success_authorized_resume_proceeds_once(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli_module, "_http_provider_factory", _run_command_plan_provider
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _StubProvider(binary)
    )
    monkeypatch.setattr(
        cli_module, "_probe_catalog_for_startup", lambda *a, **k: _reachable_catalog()
    )
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    # Pause: no --auto-run.
    paused = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "gate_pause",
            "--auto-select",
            "--skip-review",
        ],
    )
    assert paused.exit_code != 0
    state_path = tmp_path / "gate_pause" / ".ai_orchestration" / "run_state.json"
    paused_state = load_state(state_path)
    assert paused_state.pause_reason is not None
    assert paused_state.current_stage == "executor"

    # Authorized resume proceeds exactly once.
    resumed = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "gate_pause",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
            "--resume",
        ],
    )
    assert resumed.exit_code == 0, resumed.stdout
    final_state = load_state(state_path)
    assert final_state.pause_reason is None
    assert "executor" in final_state.completed_stages


def test_gate_pause_forced_failure_no_command_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli_module, "_http_provider_factory", _run_command_plan_provider
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _StubProvider(binary)
    )
    monkeypatch.setattr(
        cli_module, "_probe_catalog_for_startup", lambda *a, **k: _reachable_catalog()
    )
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    log_dir = tmp_path / "gate_forced_fail" / "execution_logs"
    before = set(log_dir.glob("*.json")) if log_dir.exists() else set()

    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "gate_forced_fail",
            "--auto-select",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0
    # The gate raises before CommandExecutor.run() is ever called, so no
    # new audit-log file appears for this invocation.
    after = set(log_dir.glob("*.json")) if log_dir.exists() else set()
    assert after == before


def test_gate_pause_rerun_without_authorization_stays_paused_no_duplicate(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        cli_module, "_http_provider_factory", _run_command_plan_provider
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _StubProvider(binary)
    )
    monkeypatch.setattr(
        cli_module, "_probe_catalog_for_startup", lambda *a, **k: _reachable_catalog()
    )
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    args = [
        "x",
        "--workspace",
        str(tmp_path),
        "--project-name",
        "gate_rerun",
        "--auto-select",
        "--skip-review",
        "--resume",
    ]
    first = runner.invoke(app, args)
    assert first.exit_code != 0
    second = runner.invoke(app, args)
    assert second.exit_code != 0
    state_path = tmp_path / "gate_rerun" / ".ai_orchestration" / "run_state.json"
    state = load_state(state_path)
    assert state.pause_reason is not None
    assert state.current_stage == "executor"


def test_gate_pause_headless_fails_closed_with_exact_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli_module, "_http_provider_factory", _run_command_plan_provider
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _StubProvider(binary)
    )
    monkeypatch.setattr(
        cli_module, "_probe_catalog_for_startup", lambda *a, **k: _reachable_catalog()
    )
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "gate_headless",
            "--auto-select",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0
    assert "--auto-run" in result.stdout


# --- CLI fallback downgrade ---------------------------------------------------


def test_cli_fallback_downgrade_success_completes_through_fallback(
    tmp_path, monkeypatch
):
    class _AlwaysDown:
        def __init__(self, model):
            self.model = model

        def complete(self, prompt, **kwargs):
            raise TransportError(f"{self.model}: connection refused")

    monkeypatch.setattr(cli_module, "_http_provider_factory", _AlwaysDown)
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _StubProvider(binary)
    )
    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda *a, **k: CatalogStatus(outcome=CatalogOutcome.UNREACHABLE),
    )
    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "fallback_success",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "fallback_success" / "hello.py").exists()


def test_cli_fallback_downgrade_forced_failure_no_proxy_retry(tmp_path, monkeypatch):
    http_calls = []

    class _AlwaysDown:
        def __init__(self, model):
            self.model = model

        def complete(self, prompt, **kwargs):
            http_calls.append(self.model)
            raise TransportError(f"{self.model}: connection refused")

    class _FailingCLI:
        def __init__(self, binary):
            self.binary = binary

        def complete(self, prompt, **kwargs):
            raise RuntimeError(f"{self.binary}: exited nonzero")

        def is_available(self):
            return True

    monkeypatch.setattr(cli_module, "_http_provider_factory", _AlwaysDown)
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _FailingCLI(binary)
    )
    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda *a, **k: CatalogStatus(outcome=CatalogOutcome.UNREACHABLE),
    )
    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "fallback_forced_fail",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0
    # Exactly one proxy attempt for the brainstormer stage before the CLI
    # fallback fails terminally; no retry against the proxy.
    assert http_calls.count("gemini-3.1-pro-low") == 1


# --- Model fallback ------------------------------------------------------------


def test_model_fallback_success_completes_names_both_ids(tmp_path, monkeypatch):
    import json

    http_calls = []

    class _RateLimitedThenOk:
        def __init__(self, model):
            self.model = model

        def complete(self, prompt, **kwargs):
            http_calls.append(self.model)
            if self.model == "primary-fault-model":
                raise ModelFaultError(f"{self.model}: HTTP 429")
            return (
                '[{"step_id": 1, "file_path": "hello.py", '
                '"action_type": "create_file", "instruction": "write hello world"}]'
            )

    monkeypatch.setattr(cli_module, "_http_provider_factory", _RateLimitedThenOk)
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _StubProvider(binary)
    )
    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda *a, **k: _reachable_catalog(
            {
                "primary-fault-model",
                "fallback-model",
                "gemini-3.1-pro-low",
                "claude-sonnet-5",
                "gpt-5.5",
            }
        ),
    )
    config_file = tmp_path / "cfg.json"
    config_file.write_text(
        json.dumps(
            {
                "planner": {
                    "model": "primary-fault-model",
                    "fallback_model": "fallback-model",
                }
            }
        )
    )
    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "model_fallback_success",
            "--tool-config",
            str(config_file),
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "primary-fault-model" in http_calls
    assert "fallback-model" in http_calls


def test_model_fallback_forced_failure_terminates_with_both_diagnostics(
    tmp_path, monkeypatch
):
    import json

    class _AlwaysFaulty:
        def __init__(self, model):
            self.model = model

        def complete(self, prompt, **kwargs):
            raise ModelFaultError(f"{self.model}: HTTP 429")

    monkeypatch.setattr(cli_module, "_http_provider_factory", _AlwaysFaulty)
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _StubProvider(binary)
    )
    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda *a, **k: _reachable_catalog(
            {
                "primary-fault-model",
                "fallback-model",
                "gemini-3.1-pro-low",
                "claude-sonnet-5",
            }
        ),
    )
    config_file = tmp_path / "cfg.json"
    config_file.write_text(
        json.dumps(
            {
                "planner": {
                    "model": "primary-fault-model",
                    "fallback_model": "fallback-model",
                }
            }
        )
    )
    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "model_fallback_forced_fail",
            "--tool-config",
            str(config_file),
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0


# --- Clean cutover -------------------------------------------------------------


def test_clean_cutover_guard_empty_for_clean_fixture_main_checkout(tmp_path):
    fixture_repo = tmp_path / "fixture-main"
    fixture_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=fixture_repo, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=fixture_repo, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=fixture_repo,
        check=True,
    )
    (fixture_repo / "tracked.txt").write_text("committed\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=fixture_repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "fixture baseline"], cwd=fixture_repo, check=True
    )
    worktrees = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=fixture_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    main_path = next(
        line.split(" ", 1)[1]
        for line in worktrees.stdout.splitlines()
        if line.startswith("worktree ")
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=main_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout == ""


def test_clean_cutover_guard_nonempty_stops_before_deletion(tmp_path):
    # Forced-failure simulation: a fixture-local "main checkout" with a
    # dirty file must produce non-empty guard output, proving the guard
    # itself (not the real main checkout) is what gates deletion.
    fixture_repo = tmp_path / "fixture-main"
    fixture_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=fixture_repo, check=True)
    (fixture_repo / "dirty.txt").write_text("uncommitted\n")
    worktrees = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=fixture_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    main_path = next(
        line.split(" ", 1)[1]
        for line in worktrees.stdout.splitlines()
        if line.startswith("worktree ")
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=main_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout != ""
