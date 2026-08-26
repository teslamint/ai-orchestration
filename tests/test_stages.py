"""Stage registry, approach parsing, and CommandExecutor tests (U4).

Covers the behavior-preservation inventory rows for approach option parsing
(`parse_approach_options`) and command retry/audit logs (`CommandExecutor`,
`retries=1` -> two total attempts).
"""

import json

import pytest

from ai_orchestration.engine.stages import (
    STAGE_ORDER,
    CommandExecutor,
    parse_approach_options,
)
from ai_orchestration.errors import StateError

# --- Six-stage order ---------------------------------------------------------


def test_stage_order_has_exactly_six_stages_in_pipeline_order():
    assert STAGE_ORDER == (
        "brainstormer",
        "brainstorming_reviewer",
        "planner",
        "executor",
        "code_reviewer",
        "fixer",
    )


# --- parse_approach_options: option/placeholder/dedup ------------------------


def test_parse_approach_options_extracts_titled_approaches():
    text = (
        "- **Approach 1: Recursive**\n"
        "  - Summary: uses recursion\n"
        "- **Approach 2: Iterative**\n"
        "  - Summary: uses a loop\n"
    )
    options = parse_approach_options(text)
    assert len(options) == 2
    assert "Approach 1" in options[0]
    assert "Approach 2" in options[1]


def test_parse_approach_options_recognizes_markdown_headings():
    text = "### Approach 1: Recursive\nSome detail.\n### Approach 2: Iterative\nMore.\n"
    options = parse_approach_options(text)
    assert len(options) == 2


def test_parse_approach_options_rejects_template_placeholder_titles():
    # error: a heading whose title is entirely a placeholder (e.g. "[Name]")
    # must be excluded, matching the legacy placeholder-stripping check.
    text = "### Approach 1: [Name of Approach]\nDetail.\n### Approach 2: Real Name\nDetail.\n"
    options = parse_approach_options(text)
    assert len(options) == 1
    assert "Real Name" in options[0]


def test_parse_approach_options_deduplicates_identical_lines():
    text = (
        "- **Approach 1: Recursive**\n"
        "- **Approach 1: Recursive**\n"
        "- **Approach 2: Iterative**\n"
    )
    options = parse_approach_options(text)
    assert len(options) == 2


def test_parse_approach_options_returns_empty_list_for_no_matches():
    options = parse_approach_options("just some prose with no approach headings")
    assert options == []


def test_parse_approach_options_korean_heading_variant():
    text = "### 접근 방식 1: 재귀\n상세 설명.\n"
    options = parse_approach_options(text)
    assert len(options) == 1


def test_parse_approach_options_rejects_placeholder_bold_bullet_title():
    options = parse_approach_options("- **Approach 1: [Name of Approach]**")

    assert options == []


def test_parse_approach_options_rejects_placeholder_bullet_with_detail_suffix():
    options = parse_approach_options("- **Approach 1: [Name]**: details")

    assert options == []


# --- CommandExecutor: retries=1 -> two total attempts -----------------------


def test_command_executor_default_retries_is_one():
    executor = CommandExecutor(auto_approve=True, log_directory="/tmp/does-not-matter")
    assert executor.retries == 1


def test_command_executor_success_first_attempt_records_one_attempt(tmp_path):
    executor = CommandExecutor(auto_approve=True, log_directory=tmp_path)
    success, output, logs = executor.run("echo hello")
    assert success is True
    assert output == "hello"
    assert len(logs) == 1
    assert logs[0].attempt == 1


def test_command_executor_failure_retries_exactly_once_for_two_total_attempts(
    tmp_path,
):
    executor = CommandExecutor(auto_approve=True, log_directory=tmp_path, retries=1)
    success, output, logs = executor.run("false")
    assert success is False
    assert len(logs) == 2  # 1 + retries(1) = 2 total attempts
    assert logs[0].attempt == 1
    assert logs[1].attempt == 2


def test_command_executor_writes_json_audit_log_with_both_attempts(tmp_path):
    executor = CommandExecutor(auto_approve=True, log_directory=tmp_path, retries=1)
    executor.run("false")
    log_files = list(tmp_path.glob("*.json"))
    assert len(log_files) == 1
    data = json.loads(log_files[0].read_text())
    assert data["total_attempts"] == 2
    assert data["final_status"] == "failed"
    assert len(data["attempts"]) == 2


def test_command_executor_records_stderr_on_failure(tmp_path):
    executor = CommandExecutor(auto_approve=True, log_directory=tmp_path, retries=0)
    success, output, logs = executor.run("sh -c 'echo boom 1>&2; exit 1'")
    assert success is False
    assert "boom" in logs[0].stderr


def test_command_executor_skip_when_not_auto_approved_and_user_declines(
    tmp_path, monkeypatch
):
    import ai_orchestration.engine.stages as stages_module

    monkeypatch.setattr(stages_module, "_confirm", lambda _prompt: False)
    executor = CommandExecutor(auto_approve=False, log_directory=tmp_path)
    success, output, logs = executor.run("echo should-not-run")
    assert success is False
    assert logs == []
    assert "skipped" in output.lower()


def test_command_executor_command_id_includes_slug_and_is_unique(tmp_path):
    executor = CommandExecutor(auto_approve=True, log_directory=tmp_path)
    executor.run("echo one")
    executor.run("echo two")
    log_files = sorted(tmp_path.glob("*.json"))
    assert len(log_files) == 2
    assert log_files[0] != log_files[1]


# --- execute_stage / run_pipeline -------------------------------------------


def test_execute_stage_calls_handler_and_appends_to_completed():
    from ai_orchestration.engine.stages import execute_stage

    calls = []
    completed = []
    execute_stage(
        "brainstormer",
        handler=lambda: calls.append("brainstormer"),
        completed_stages=completed,
    )
    assert calls == ["brainstormer"]
    assert completed == ["brainstormer"]


def test_run_pipeline_executes_all_six_stages_in_order():
    from ai_orchestration.engine.stages import run_pipeline

    order = []
    handlers = {name: (lambda n=name: order.append(n)) for name in STAGE_ORDER}
    completed = run_pipeline(handlers=handlers, completed_stages=[], start_stage=None)
    assert order == list(STAGE_ORDER)
    assert completed == list(STAGE_ORDER)


def test_run_pipeline_resume_skips_already_completed_stages():
    from ai_orchestration.engine.stages import run_pipeline

    order = []
    handlers = {name: (lambda n=name: order.append(n)) for name in STAGE_ORDER}
    completed = run_pipeline(
        handlers=handlers,
        completed_stages=["brainstormer", "brainstorming_reviewer"],
        start_stage="planner",
    )
    assert order == ["planner", "executor", "code_reviewer", "fixer"]
    assert completed == list(STAGE_ORDER)


def test_run_pipeline_start_stage_none_still_skips_stages_already_in_completed():
    # Distinguishes the loop-body skip guard from start_index slicing: here
    # start_stage is None, so order[start_index:] is the full six stages,
    # and only the in-loop `if stage_name in completed_stages` check can
    # prevent brainstormer's handler from re-running.
    from ai_orchestration.engine.stages import run_pipeline

    order = []
    handlers = {name: (lambda n=name: order.append(n)) for name in STAGE_ORDER}
    completed = run_pipeline(
        handlers=handlers,
        completed_stages=["brainstormer"],
        start_stage=None,
    )
    assert "brainstormer" not in order
    assert order == [
        "brainstorming_reviewer",
        "planner",
        "executor",
        "code_reviewer",
        "fixer",
    ]
    assert completed == list(STAGE_ORDER)


def test_run_pipeline_skip_review_omits_code_reviewer_and_fixer():
    from ai_orchestration.engine.stages import run_pipeline

    order = []
    handlers = {name: (lambda n=name: order.append(n)) for name in STAGE_ORDER}
    completed = run_pipeline(
        handlers=handlers, completed_stages=[], start_stage=None, skip_review=True
    )
    assert order == ["brainstormer", "brainstorming_reviewer", "planner", "executor"]
    assert "code_reviewer" not in completed
    assert "fixer" not in completed


# --- run_pipeline: unavailable start_stage (CodeRabbit finding) -------------


def test_run_pipeline_unknown_start_stage_raises_state_error():
    from ai_orchestration.engine.stages import run_pipeline

    handlers = {name: (lambda: None) for name in STAGE_ORDER}
    with pytest.raises(StateError):
        run_pipeline(
            handlers=handlers, completed_stages=[], start_stage="not_a_real_stage"
        )


def test_run_pipeline_start_stage_excluded_by_skip_review_raises_state_error():
    # "code_reviewer" is a real stage name, but skip_review removes it from
    # the active order, so resuming at it is unavailable, not silently
    # resolvable to some other index.
    from ai_orchestration.engine.stages import run_pipeline

    handlers = {name: (lambda: None) for name in STAGE_ORDER}
    with pytest.raises(StateError):
        run_pipeline(
            handlers=handlers,
            completed_stages=[],
            start_stage="code_reviewer",
            skip_review=True,
        )


# --- CommandExecutor: finite subprocess timeout (CodeRabbit finding) --------


def test_command_executor_has_finite_default_timeout(tmp_path):
    executor = CommandExecutor(auto_approve=True, log_directory=tmp_path)
    assert executor.timeout is not None
    assert executor.timeout > 0


def test_command_executor_timeout_expired_records_failed_attempt(tmp_path, monkeypatch):
    import subprocess

    import ai_orchestration.engine.stages as stages_module

    def _raise_timeout(command_args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command_args, timeout=kwargs["timeout"])

    monkeypatch.setattr(stages_module.subprocess, "run", _raise_timeout)
    executor = CommandExecutor(
        auto_approve=True, log_directory=tmp_path, retries=0, timeout=5
    )
    success, output, logs = executor.run("sleep 100")
    assert success is False
    assert len(logs) == 1
    assert logs[0].exit_code == -1
    assert "timed out" in logs[0].stderr.lower()


def test_command_executor_passes_configured_timeout_to_subprocess_run(
    tmp_path, monkeypatch
):
    import ai_orchestration.engine.stages as stages_module

    captured = {}
    real_run = stages_module.subprocess.run

    def _capturing_run(command_args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return real_run(command_args, **kwargs)

    monkeypatch.setattr(stages_module.subprocess, "run", _capturing_run)
    executor = CommandExecutor(auto_approve=True, log_directory=tmp_path, timeout=42)
    executor.run("echo hi")
    assert captured["timeout"] == 42
