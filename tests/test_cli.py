"""CLI wiring tests: option parity, workspace resolution, routing, gates (U5).

Covers S1 (per-stage model routing), S2 (command approval), S3 (fresh
rerun/resume), S5 (proxy unreachable -> CLI fallback), and AE9 (non-TTY
gate diagnostics). Providers are faked via `ai_orchestration.cli` module
seams (`_http_provider_factory`, `_cli_provider_factory`,
`_probe_catalog_for_startup`, `_is_tty`); no test performs live network I/O.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest
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


def test_fixer_extracts_fenced_code_before_writing(tmp_path, monkeypatch):
    from ai_orchestration.config import StageConfig
    from ai_orchestration.models.context import (
        CodeReviewItem,
        OrchestrationContext,
        ReviewItemType,
        ReviewSeverity,
    )

    context = OrchestrationContext(
        project_name="fixer_test", user_goal="fix", workspace_path=tmp_path
    )
    item = CodeReviewItem(
        item_id=1,
        file_path="fixed.py",
        review_type=ReviewItemType.BUG,
        severity=ReviewSeverity.HIGH,
        description="broken",
        suggestion="fix it",
    )
    monkeypatch.setattr(
        cli_module,
        "_complete_stage_text",
        lambda *_args, **_kwargs: "```python\nvalue = 1\n```",
    )

    cli_module._run_fixer(context, StageConfig(model="codex"), item)
    assert (tmp_path / "fixed.py").read_text() == "value = 1"


def test_command_logs_are_created_inside_project_workspace(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    _install_reachable_catalog(monkeypatch)
    from ai_orchestration.models.context import ActionType, Task

    def plan_command(context, *_args, **_kwargs):
        context.implementation_plan = [
            Task(
                step_id=1,
                file_path=".",
                action_type=ActionType.RUN_COMMAND,
                instruction="echo logged",
            )
        ]

    monkeypatch.setattr(cli_module, "_run_planner", plan_command)

    result = runner.invoke(
        app,
        [
            "run a command",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "command_logs",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert list((tmp_path / "command_logs" / "execution_logs").glob("*.json"))


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

    import ai_orchestration.config as config_module

    monkeypatch.setattr(cli_module, "_http_provider_factory", spy_http)
    monkeypatch.setattr(cli_module, "_cli_provider_factory", spy_cli)
    monkeypatch.setattr(config_module, "_default_binary_exists", lambda _name: True)
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


# --- Phase-gate remediation regression tests --------------------------------


def _install_reachable_catalog(monkeypatch, models=None):
    from ai_orchestration.config import CatalogOutcome, CatalogStatus

    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda *a, **k: CatalogStatus(
            outcome=CatalogOutcome.REACHABLE_WITH_MODELS,
            models=frozenset(
                models or {"gpt-5.5", "opus-5", "gemini-3.1-pro-low", "claude-sonnet-5"}
            ),
        ),
    )


class _RunCommandProvider:
    """Always plans a single run_command task."""

    def __init__(self, name, plan_json=None):
        self.name = name
        self._plan_json = plan_json or (
            '[{"step_id": 1, "file_path": ".", '
            '"action_type": "run_command", "instruction": "echo hi"}]'
        )

    def complete(self, prompt, **kwargs):
        return self._plan_json

    def complete_structured(self, prompt, *, schema, **kwargs):
        values = {}
        for field_name, field_info in schema.model_fields.items():
            if not field_info.is_required():
                continue
            if field_name in ("step_id", "total_files_reviewed"):
                values[field_name] = 1
            elif field_name == "requires_fixes":
                values[field_name] = False
            else:
                ann = str(field_info.annotation)
                values[field_name] = (
                    []
                    if ann.startswith("typing.List") or ann.startswith("list")
                    else "x"
                )
        return schema(**values)

    def is_available(self):
        return True


# finding #2: --auto-run alone (no --auto-approve) must fail closed, not
# crash with EOFError/click.Abort.
def test_auto_run_without_auto_approve_fails_closed_not_eoferror(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli_module, "_http_provider_factory", lambda model: _RunCommandProvider(model)
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _RunCommandProvider(binary)
    )
    _install_reachable_catalog(monkeypatch)
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "auto_run_only",
            "--auto-select",
            "--auto-run",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.stdout
    assert "Aborted" not in result.stdout
    assert "--auto-approve" in result.stdout


# finding #1: a headless run with requires_fixes=True and no --auto-fix
# must fail closed (nonzero, names --auto-fix), never silently drop fixes.
def test_requires_fixes_without_auto_fix_headless_fails_closed(tmp_path, monkeypatch):
    class _ReviewStub(_FakeProvider):
        def complete_structured(self, prompt, *, schema, **kwargs):
            if schema.__name__ == "CodeReviewResult":
                return schema(
                    reviewed_at="t",
                    total_files_reviewed=1,
                    items=[
                        {
                            "item_id": 1,
                            "file_path": "hello.py",
                            "review_type": "bug",
                            "severity": "high",
                            "description": "x",
                            "suggestion": "y",
                        }
                    ],
                    overall_assessment="needs work",
                    requires_fixes=True,
                )
            return super().complete_structured(prompt, schema=schema, **kwargs)

    monkeypatch.setattr(
        cli_module, "_http_provider_factory", lambda model: _ReviewStub(model)
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _ReviewStub(binary)
    )
    _install_reachable_catalog(monkeypatch)
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    result = runner.invoke(
        app,
        [
            "build something",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "no_auto_fix",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
        ],
    )
    assert result.exit_code != 0
    assert "--auto-fix" in result.stdout


# finding #3: a custom --tool-config "provider" base_url/api_key must reach
# the real HttpProvider construction, not just startup catalog validation.
def test_custom_provider_endpoint_reaches_real_http_provider_factory(monkeypatch):
    captured = {}

    class _SpyHttpProvider:
        def __init__(self, *, model, base_url, api_key):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            self.model = model

        def complete(self, prompt, **kwargs):
            return "[]"

        def complete_structured(self, prompt, *, schema, **kwargs):
            return schema()

        def is_available(self):
            return True

    monkeypatch.setattr(cli_module, "HttpProvider", _SpyHttpProvider)
    monkeypatch.setattr(
        cli_module,
        "_active_provider_config",
        cli_module.ProviderConfig(
            base_url="http://custom-endpoint:1234/v1", api_key="custom-key"
        ),
    )
    provider = cli_module._http_provider_factory("gpt-5.5")
    assert captured["base_url"] == "http://custom-endpoint:1234/v1"
    assert captured["api_key"] == "custom-key"
    assert isinstance(provider, _SpyHttpProvider)


# finding #4: --resume must refuse to continue when a stage's model
# differs from what was persisted in a prior invocation's config_snapshot.
def test_resume_detects_stage_model_drift_and_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli_module, "_http_provider_factory", lambda model: _RunCommandProvider(model)
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _RunCommandProvider(binary)
    )
    _install_reachable_catalog(monkeypatch)
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    # First invocation pauses at the executor's command gate, persisting a
    # config_snapshot naming the default planner model.
    first = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "drift_test",
            "--auto-select",
            "--auto-run",
            "--skip-review",
        ],
    )
    assert first.exit_code != 0

    # Resuming with a different --planner model must be refused, not
    # silently continued under the new model.
    second = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "drift_test",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
            "--resume",
            "--planner",
            "opus-5",
        ],
    )
    assert second.exit_code != 0
    assert "--resume" in second.stdout
    assert "planner" in second.stdout


# finding #7: --project-name must not escape --workspace via traversal or
# an absolute path.
def test_project_name_path_traversal_is_rejected(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    _install_reachable_catalog(monkeypatch)
    result = runner.invoke(
        app,
        [
            "build something",
            "--workspace",
            str(tmp_path / "anchor"),
            "--project-name",
            "../../etc",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0
    assert not (tmp_path.parent / "etc").exists()


def test_project_name_absolute_path_is_rejected(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    _install_reachable_catalog(monkeypatch)
    escape_target = tmp_path / "outside"
    result = runner.invoke(
        app,
        [
            "build something",
            "--workspace",
            str(tmp_path / "anchor"),
            "--project-name",
            str(escape_target),
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0
    assert not escape_target.exists()


# finding #8: a failing run_command task must fail the run, not report
# pipeline success.
def test_run_command_task_failure_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli_module,
        "_http_provider_factory",
        lambda model: _RunCommandProvider(
            model,
            plan_json=(
                '[{"step_id": 1, "file_path": ".", '
                '"action_type": "run_command", "instruction": "false"}]'
            ),
        ),
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _RunCommandProvider(binary)
    )
    _install_reachable_catalog(monkeypatch)

    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "failing_command",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0


# finding #9: --ralph-wiggum-max-iterations must reach the context's
# IterationMetadata.max_attempts, not the hardcoded default of 3.
def test_ralph_wiggum_max_iterations_flag_reaches_iteration_metadata(
    tmp_path, monkeypatch
):
    # Mutation-guarded: if --ralph-wiggum-max-iterations is not wired into
    # IterationMetadata, the loop always caps at the hardcoded default of
    # 3 regardless of the flag. Setting max-iterations=5 and never
    # accepting must produce exactly 5 review calls, not 3.
    review_calls = {"n": 0}

    class _NeverAcceptsProvider(_FakeProvider):
        def complete_structured(self, prompt, *, schema, **kwargs):
            if schema.__name__ == "RalphWiggumFeedback":
                review_calls["n"] += 1
                return schema(
                    decision="needs_revision",
                    confidence_score=0.1,
                    comments=[],
                    suggestions=[],
                )
            return super().complete_structured(prompt, schema=schema, **kwargs)

    monkeypatch.setattr(
        cli_module, "_http_provider_factory", lambda model: _NeverAcceptsProvider(model)
    )
    monkeypatch.setattr(
        cli_module,
        "_cli_provider_factory",
        lambda binary: _NeverAcceptsProvider(binary),
    )
    _install_reachable_catalog(monkeypatch)

    runner.invoke(
        app,
        [
            "build something",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "ralph_max_iter",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
            "--enable-ralph-wiggum",
            "--ralph-wiggum-max-iterations",
            "5",
            "--no-ralph-wiggum-state-file",
        ],
    )
    assert review_calls["n"] == 5


# finding #12: fresh CLI flags on a resuming invocation must win over the
# persisted context snapshot's stale values.
def test_resume_fresh_flags_override_persisted_context_values(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli_module, "_http_provider_factory", lambda model: _RunCommandProvider(model)
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _RunCommandProvider(binary)
    )
    _install_reachable_catalog(monkeypatch)
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    first = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "flag_override",
            "--auto-select",
            "--auto-run",
            "--skip-review",
            "--completion-promise",
            "OLD",
        ],
    )
    assert first.exit_code != 0

    from ai_orchestration.engine.state import load_state

    state_path = tmp_path / "flag_override" / ".ai_orchestration" / "run_state.json"

    # Resume with a NEW completion promise; the persisted context's OLD
    # value must not silently win.
    monkeypatch.setattr(cli_module, "_is_tty", lambda: True)
    monkeypatch.setattr(cli_module, "_confirm_interactively", lambda prompt: True)
    runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "flag_override",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
            "--resume",
            "--completion-promise",
            "NEW",
        ],
    )
    final_state = load_state(state_path)
    assert final_state.outputs["context"]["ralph_wiggum_completion_promise"] == "NEW"


# finding #13: the executor prompt must contain actual source content, and the
# executor's response must update the target file.
def test_edit_file_task_receives_real_existing_code(tmp_path, monkeypatch):
    observed = {"executor_prompt": None}

    class _EditProvider:
        def __init__(self, name):
            self.name = name

        def complete(self, prompt, **kwargs):
            if "Now, generate the JSON `implementation_plan`" in prompt:
                return (
                    '[{"step_id": 1, "file_path": "existing.py", '
                    '"action_type": "edit_file", '
                    '"instruction": "EXECUTOR_WRITE_MARKER"}]'
                )
            if "EXECUTOR_WRITE_MARKER" in prompt:
                observed["executor_prompt"] = prompt
                return "UPDATED_SOURCE_MARKER = True\n"
            return "### Approach 1: EDIT_SOURCE_MARKER\n"

        def complete_structured(self, prompt, *, schema, **kwargs):
            values = {}
            for field_name, field_info in schema.model_fields.items():
                if not field_info.is_required():
                    continue
                if field_name in ("step_id", "total_files_reviewed"):
                    values[field_name] = 1
                elif field_name == "requires_fixes":
                    values[field_name] = False
                else:
                    ann = str(field_info.annotation)
                    values[field_name] = (
                        []
                        if ann.startswith("typing.List") or ann.startswith("list")
                        else "x"
                    )
            return schema(**values)

        def is_available(self):
            return True

    monkeypatch.setattr(
        cli_module, "_http_provider_factory", lambda model: _EditProvider(model)
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _EditProvider(binary)
    )
    _install_reachable_catalog(monkeypatch)

    project_dir = tmp_path / "edit_test"
    project_dir.mkdir()
    original_source = "ORIGINAL_SOURCE_MARKER = True\n"
    target = project_dir / "existing.py"
    target.write_text(original_source)

    result = runner.invoke(
        app,
        [
            "edit a file",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "edit_test",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert observed["executor_prompt"] is not None
    assert original_source in observed["executor_prompt"]
    assert target.read_text() == "UPDATED_SOURCE_MARKER = True"


# `--auto-select` controls interactive approach selection; headless runs keep
# the safe deterministic default without creating another approval gate.
def test_non_tty_approach_selection_uses_default_without_auto_select(
    tmp_path, monkeypatch
):
    _install_fakes(monkeypatch)
    _install_reachable_catalog(monkeypatch)
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "selection_default",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
    )
    assert result.exit_code == 0, result.stdout


def test_tty_approach_selection_uses_prompt_without_auto_select(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    _install_reachable_catalog(monkeypatch)
    monkeypatch.setattr(cli_module, "_is_tty", lambda: True)
    monkeypatch.setattr(cli_module, "_prompt_choice", lambda: 1, raising=False)
    monkeypatch.setattr(
        cli_module.typer,
        "prompt",
        lambda *args, **kwargs: 1 if kwargs.get("type") is int else "custom",
    )

    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "selection_tty",
            "--skip-review",
        ],
    )
    assert result.exit_code == 0, result.stdout


# round-2 P3 #10: control bytes must fail through the regular clean error path.
def test_project_name_with_control_character_is_rejected_before_resolve(
    tmp_path, monkeypatch
):
    _install_fakes(monkeypatch)
    _install_reachable_catalog(monkeypatch)

    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "bad\x00name",
            "--auto-select",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0
    assert "--project-name" in result.stdout
    assert "Traceback" not in result.stdout


# round-2 P0 #4: persist completed executor tasks before a later failure so
# resume never repeats an already-successful non-idempotent command.
def test_resume_skips_only_checkpointed_executor_tasks_after_later_failure(
    tmp_path, monkeypatch
):
    marker = tmp_path / "executor_runs.txt"
    command = (
        f'{sys.executable} -c "from pathlib import Path; '
        f"Path(r'{marker}').open('a').write('done\\n')\""
    )
    plan = json.dumps(
        [
            {
                "step_id": 1,
                "file_path": ".",
                "action_type": "run_command",
                "instruction": command,
            },
            {
                "step_id": 2,
                "file_path": ".",
                "action_type": "run_command",
                "instruction": "false",
            },
        ]
    )
    monkeypatch.setattr(
        cli_module,
        "_http_provider_factory",
        lambda model: _RunCommandProvider(model, plan_json=plan),
    )
    monkeypatch.setattr(
        cli_module,
        "_cli_provider_factory",
        lambda binary: _RunCommandProvider(binary, plan_json=plan),
    )
    _install_reachable_catalog(monkeypatch)

    args = [
        "x",
        "--workspace",
        str(tmp_path),
        "--project-name",
        "executor_checkpoint",
        "--auto-select",
        "--auto-run",
        "--auto-approve",
        "--skip-review",
    ]
    first = runner.invoke(app, args)
    assert first.exit_code != 0
    assert marker.read_text() == "done\n"

    from ai_orchestration.engine.state import load_state

    state_path = (
        tmp_path / "executor_checkpoint" / ".ai_orchestration" / "run_state.json"
    )
    saved = load_state(state_path)
    assert saved.outputs["context"]["completed_executor_task_ids"] == [1]
    assert "executor" not in saved.completed_stages

    resumed = runner.invoke(app, args + ["--resume"])
    assert resumed.exit_code != 0
    assert marker.read_text() == "done\n"


# finding #18: a malformed provider block in --tool-config must raise the
# clean one-line ConfigError diagnostic, not an unhandled traceback.
def test_malformed_provider_block_in_tool_config_returns_clean_diagnostic(tmp_path):
    import json as _json

    config_file = tmp_path / "cfg.json"
    config_file.write_text(_json.dumps({"provider": {"base_url": 5}}))
    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "bad_provider",
            "--tool-config",
            str(config_file),
            "--auto-select",
            "--skip-review",
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.stdout
    assert "base_url" in result.stdout


# Persisted state is a trust boundary through resume-time context restoration
# and config-drift checks too, not only through RunState's top-level fields.
@pytest.mark.parametrize(
    ("outputs", "config_snapshot", "workspace_path_is_state_file"),
    [
        (
            {"context": {"user_goal": 12345}},
            {"stages": {}},
            False,
        ),
        (
            {
                "context": {
                    "project_name": "nested_state",
                    "user_goal": "x",
                    "workspace_path": "/tmp",
                }
            },
            {"stages": ["not", "a", "mapping"]},
            False,
        ),
        (
            {
                "context": {
                    "project_name": "nested_state",
                    "user_goal": "x",
                }
            },
            {"stages": {}},
            True,
        ),
    ],
)
def test_resume_rejects_malformed_nested_state_without_secret_leak(
    tmp_path, monkeypatch, outputs, config_snapshot, workspace_path_is_state_file
):
    _install_fakes(monkeypatch)
    _install_reachable_catalog(monkeypatch)
    state_dir = tmp_path / "nested_state" / ".ai_orchestration"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "run_state.json"
    if workspace_path_is_state_file:
        outputs["context"]["workspace_path"] = str(state_path)
    state_path.write_text(
        json.dumps(
            {
                "goal": "x",
                "project_name": "nested_state",
                "config_snapshot": config_snapshot,
                "completed_stages": ["brainstormer"],
                "outputs": outputs,
            }
        )
    )
    monkeypatch.setenv("CLIPROXYAPI_KEY", "SECRET_NESTED_STATE_VALUE")

    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "nested_state",
            "--auto-select",
            "--skip-review",
            "--resume",
        ],
    )

    assert result.exit_code != 0
    assert not isinstance(result.exception, OSError)
    assert "Traceback" not in result.stdout
    assert "SECRET_NESTED_STATE_VALUE" not in result.stdout
    expected = "workspace directory" if workspace_path_is_state_file else "run state"
    assert expected in result.stdout


# finding #20: a corrupt run_state.json plus --resume must fail cleanly,
# not crash with a raw traceback.
def test_corrupt_run_state_with_resume_fails_cleanly(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    _install_reachable_catalog(monkeypatch)

    state_dir = tmp_path / "corrupt_test" / ".ai_orchestration"
    state_dir.mkdir(parents=True)
    (state_dir / "run_state.json").write_text("{not valid json")

    result = runner.invoke(
        app,
        [
            "x",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "corrupt_test",
            "--auto-select",
            "--skip-review",
            "--resume",
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.stdout


# finding #22: non-auto-select mode must restore the interactive numbered
# menu, not silently pick the first option like auto-select.
def test_select_approach_interactive_menu_lets_user_choose_non_default():
    from ai_orchestration.cli import _select_approach
    from ai_orchestration.models.context import OrchestrationContext

    ctx = OrchestrationContext(
        project_name="p",
        user_goal="g",
        workspace_path="/tmp",
        brainstorming_ideas=(
            "### Approach 1: Recursive\ndetail\n### Approach 2: Iterative\ndetail\n"
        ),
    )
    _select_approach(
        ctx,
        auto_select=False,
        prompt_choice=lambda: 2,
        prompt_custom=lambda: "unused",
    )
    assert "Approach 2" in ctx.selected_approach


def test_select_approach_interactive_menu_supports_custom_entry():
    from ai_orchestration.cli import _select_approach
    from ai_orchestration.models.context import OrchestrationContext

    ctx = OrchestrationContext(
        project_name="p",
        user_goal="g",
        workspace_path="/tmp",
        brainstorming_ideas="### Approach 1: A\n### Approach 2: B\n",
    )
    _select_approach(
        ctx,
        auto_select=False,
        prompt_choice=lambda: 3,
        prompt_custom=lambda: "my custom approach",
    )
    assert ctx.selected_approach == "my custom approach"


# finding #14: --debug/--debug-log must write real per-stage content, not
# just a startup summary print.
def test_resolve_debug_log_path_disabled_returns_none():
    from ai_orchestration.cli import _resolve_debug_log_path

    assert _resolve_debug_log_path(False, "./anything") is None


def test_resolve_debug_log_path_directory_appends_timestamped_filename(tmp_path):
    from ai_orchestration.cli import _resolve_debug_log_path

    result = _resolve_debug_log_path(True, str(tmp_path))
    assert result is not None
    assert result.parent == tmp_path
    assert result.name.startswith("orchestrator_debug-")
    assert result.name.endswith(".log")


def test_resolve_debug_log_path_file_suffix_gets_timestamp_inserted(tmp_path):
    from ai_orchestration.cli import _resolve_debug_log_path

    target = tmp_path / "mylog.txt"
    result = _resolve_debug_log_path(True, str(target))
    assert result is not None
    assert result.parent == tmp_path
    assert result.name.startswith("mylog-")
    assert result.name.endswith(".txt")


def test_write_debug_log_appends_stage_header_and_content(tmp_path):
    from ai_orchestration.cli import _write_debug_log

    log_path = tmp_path / "debug.log"
    _write_debug_log(True, log_path, "brainstormer raw output", "first output")
    _write_debug_log(True, log_path, "planner raw output", "second output")
    content = log_path.read_text(encoding="utf-8")
    assert "==== brainstormer raw output ====" in content
    assert "first output" in content
    assert "==== planner raw output ====" in content
    assert "second output" in content


def test_write_debug_log_noop_when_debug_disabled(tmp_path):
    from ai_orchestration.cli import _write_debug_log

    log_path = tmp_path / "debug.log"
    _write_debug_log(False, log_path, "stage", "content")
    assert not log_path.exists()


def test_full_run_with_debug_writes_per_stage_content_to_log_file(
    tmp_path, monkeypatch
):
    _install_fakes(monkeypatch)
    _install_reachable_catalog(monkeypatch)

    log_dir = tmp_path / "debug_logs"
    result = runner.invoke(
        app,
        [
            "build something",
            "--workspace",
            str(tmp_path / "ws"),
            "--project-name",
            "debug_run",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
            "--debug",
            "--debug-log",
            str(log_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout
    log_files = list(log_dir.glob("*.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "brainstormer raw output" in content
    assert "planner raw output" in content


def test_planner_rejects_duplicate_task_step_ids(monkeypatch, tmp_path):
    context = cli_module.OrchestrationContext(
        project_name="duplicate_steps", user_goal="x", workspace_path=tmp_path
    )
    response = json.dumps(
        [
            {
                "step_id": 1,
                "file_path": "a.py",
                "action_type": "create_file",
                "instruction": "a",
            },
            {
                "step_id": 1,
                "file_path": "b.py",
                "action_type": "create_file",
                "instruction": "b",
            },
        ]
    )
    monkeypatch.setattr(
        cli_module, "_complete_stage_text", lambda *args, **kwargs: response
    )

    with pytest.raises(cli_module.StateError, match="duplicate task step_id 1"):
        cli_module._run_planner(context, cli_module.StageConfig(model="gpt-5.5"))


def test_resume_rejects_completed_state_missing_context(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    _install_reachable_catalog(monkeypatch)
    state_dir = tmp_path / "missing_context" / ".ai_orchestration"
    state_dir.mkdir(parents=True)
    (state_dir / "run_state.json").write_text(
        json.dumps(
            {
                "goal": "x",
                "project_name": "missing_context",
                "config_snapshot": {"stages": {}},
                "completed_stages": ["brainstormer"],
                "outputs": {},
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
            "missing_context",
            "--resume",
        ],
    )
    assert result.exit_code != 0
    assert "outputs.context" in result.stdout


def test_real_cli_subprocess_hides_secret_from_uncaught_exception(tmp_path):
    secret = "SECRET_UNCAUGHT_EXCEPTION_VALUE"
    script = (
        "import sys\n"
        "import ai_orchestration.cli as cli\n"
        "cli._probe_catalog_for_startup = lambda *args: (_ for _ in ()).throw(RuntimeError('boom'))\n"
        "sys.argv = ['ai-orchestration', 'x', '--workspace', sys.argv[1], '--project-name', 'subprocess_secret']\n"
        "cli.app()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        env={**os.environ, "CLIPROXYAPI_KEY": secret},
    )
    assert result.returncode != 0
    assert secret not in result.stdout
    assert secret not in result.stderr
