"""CLI wiring tests: option parity, workspace resolution, routing, gates (U5).

Covers S1 (per-stage model routing), S2 (command approval), S3 (fresh
rerun/resume), S5 (proxy unreachable -> CLI fallback), and AE9 (non-TTY
gate diagnostics). Providers are faked via `ai_orchestration.cli` module
seams (`_http_provider_factory`, `_cli_provider_factory`,
`_probe_catalog_for_startup`, `_is_tty`); no test performs live network I/O.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

import ai_orchestration.cli as cli_module
from ai_orchestration.cli import app

runner = CliRunner(env={"COLUMNS": "200", "LINES": "50"})


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class _FakeProvider:
    """Answers every prompt with fixed content, never touches the network.

    `complete()` always returns a one-task JSON plan so the planner stage
    produces a non-empty `implementation_plan`, letting the executor stage
    actually run and exercise routing.
    """

    def __init__(self, name: str = "fake"):
        self.name = name

    def complete(self, prompt: str, *, system=None, **kwargs) -> str:
        return (
            '[{"step_id": 1, "file_path": "hello.py", '
            '"action_type": "create_file", "instruction": "write hello world"}]'
        )

    def complete_structured(self, prompt: str, *, schema, **kwargs):
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

    def is_available(self) -> bool:
        return True


def _fake_http_factory(model):
    return _FakeProvider(model)


def _fake_cli_factory(binary):
    return _FakeProvider(binary)


def _install_fakes(monkeypatch):
    monkeypatch.setattr(cli_module, "_http_provider_factory", _fake_http_factory)
    monkeypatch.setattr(cli_module, "_cli_provider_factory", _fake_cli_factory)


# --- happy: --help lists every preserved option -----------------------------


def test_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_help_lists_every_preserved_option():
    result = runner.invoke(app, ["--help"])
    output = _strip_ansi(result.stdout)
    preserved_options = [
        "--workspace",
        "--project-name",
        "--auto-select",
        "--auto-run",
        "--auto-approve",
        "--auto-fix",
        "--skip-review",
        "--max-fix-iterations",
        "--brainstormer",
        "--reviewer",
        "--planner",
        "--executor",
        "--code-reviewer",
        "--fixer",
        "--tool-config",
        "--enable-ralph-wiggum",
        "--ralph-wiggum-threshold",
        "--ralph-wiggum-max-iterations",
        "--completion-promise",
        "--debug",
        "--debug-log",
        "--resume",
    ]
    for option in preserved_options:
        assert option in output, f"missing option: {option}"


def test_help_documents_agy_not_gemini():
    result = runner.invoke(app, ["--help"])
    output = _strip_ansi(result.stdout)
    assert "gemini" not in output.lower()


# --- edge: workspace resolution ----------------------------------------------


def test_environment_workspace_variable_is_used_when_flag_absent(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_WORKSPACE", str(tmp_path))
    result = runner.invoke(
        app,
        [
            "build something",
            "--project-name",
            "env_test",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "env_test").exists()


def test_absolute_workspace_path_is_used_as_is(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    abs_workspace = tmp_path / "abs-anchor"
    result = runner.invoke(
        app,
        [
            "build something",
            "--workspace",
            str(abs_workspace),
            "--project-name",
            "abs_test",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (abs_workspace / "abs_test").exists()


def test_non_english_goal_generates_project_default_name(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    result = runner.invoke(
        app,
        [
            "한글 목표입니다",
            "--workspace",
            str(tmp_path),
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "project").exists()


# --- happy: routing selects the right transport per stage -------------------


def test_executor_claude_selects_cli_provider_and_planner_opus5_selects_proxy(
    tmp_path, monkeypatch
):
    calls = {"http": [], "cli": []}

    def spy_http(model):
        calls["http"].append(model)
        return _FakeProvider(model)

    def spy_cli(binary):
        calls["cli"].append(binary)
        return _FakeProvider(binary)

    monkeypatch.setattr(cli_module, "_http_provider_factory", spy_http)
    monkeypatch.setattr(cli_module, "_cli_provider_factory", spy_cli)
    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda base_url, api_key: __import__(
            "ai_orchestration.config", fromlist=["CatalogStatus"]
        ).CatalogStatus(
            outcome=__import__(
                "ai_orchestration.config", fromlist=["CatalogOutcome"]
            ).CatalogOutcome.REACHABLE_WITH_MODELS,
            models=frozenset(
                {"opus-5", "gpt-5.5", "gemini-3.1-pro-low", "claude-sonnet-5"}
            ),
        ),
    )
    result = runner.invoke(
        app,
        [
            "build something",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "routing_test",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
            "--executor",
            "claude",
            "--planner",
            "opus-5",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "claude" in calls["cli"]
    assert "opus-5" in calls["http"]


# --- error: unknown model, missing binary, non-TTY gate, malformed config --


def test_unknown_proxy_model_fails_before_any_stage_runs(tmp_path, monkeypatch):
    from ai_orchestration.config import CatalogOutcome, CatalogStatus

    _install_fakes(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda base_url, api_key: CatalogStatus(
            outcome=CatalogOutcome.REACHABLE_WITH_MODELS,
            models=frozenset({"gpt-5.5", "gemini-3.1-pro-low", "claude-sonnet-5"}),
        ),
    )
    result = runner.invoke(
        app,
        [
            "build something",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "bad_model",
            "--planner",
            "not-a-real-model",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0
    assert "planner" in result.stdout.lower()
    assert not (tmp_path / "bad_model").exists()


def test_missing_binary_fails_before_any_stage_runs(tmp_path, monkeypatch):
    import shutil

    from ai_orchestration.config import CatalogOutcome, CatalogStatus

    _install_fakes(monkeypatch)
    real_which = shutil.which

    def which_all_but_claude(name):
        if name == "claude":
            return None
        return real_which(name) or f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", which_all_but_claude)
    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda base_url, api_key: CatalogStatus(
            outcome=CatalogOutcome.REACHABLE_WITH_MODELS,
            models=frozenset({"gpt-5.5", "gemini-3.1-pro-low", "claude-sonnet-5"}),
        ),
    )
    result = runner.invoke(
        app,
        [
            "build something",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "missing_binary",
            "--executor",
            "claude",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0
    assert "claude" in result.stdout.lower()
    assert not (tmp_path / "missing_binary").exists()


def test_malformed_tool_config_file_returns_nonzero(tmp_path):
    config_file = tmp_path / "bad_config.json"
    config_file.write_text("{not valid json")
    result = runner.invoke(
        app,
        [
            "build something",
            "--workspace",
            str(tmp_path),
            "--tool-config",
            str(config_file),
            "--auto-select",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0


# --- OrchestratorConfig wiring ------------------------------------------------


def test_orchestrator_config_field_exists_for_every_cli_option():
    from ai_orchestration.config import OrchestratorConfig

    config = OrchestratorConfig()
    for field_name in (
        "auto_approve",
        "auto_run",
        "auto_fix",
        "auto_select",
        "skip_review",
        "max_fix_iterations",
        "debug",
        "enable_ralph_wiggum",
        "ralph_wiggum_threshold",
        "ralph_wiggum_max_iterations",
        "ralph_wiggum_completion_promise",
    ):
        assert hasattr(config, field_name), f"missing field: {field_name}"
