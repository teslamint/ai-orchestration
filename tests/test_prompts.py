"""Characterization tests for ported prompt templates (U2).

Asserts prompt text is unchanged and templates format with all required
fields to produce byte-equivalent output to the committed constants
(Covers S4, Covers AE1).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent_prompts as legacy_prompts
from ai_orchestration.prompts import stages as new_prompts


def test_agent_prompts_dict_has_same_stage_keys():
    # ai_orchestration owns the routing enum via StageConfig; the prompt
    # dict itself is a pure data structure ported verbatim.
    assert set(new_prompts.AGENT_PROMPTS) == set(legacy_prompts.AGENT_PROMPTS)


def test_all_system_prompts_are_byte_identical():
    for stage_name, legacy_entry in legacy_prompts.AGENT_PROMPTS.items():
        new_entry = new_prompts.AGENT_PROMPTS[stage_name]
        assert new_entry["system"] == legacy_entry["system"], stage_name


def test_all_user_templates_are_byte_identical():
    for stage_name, legacy_entry in legacy_prompts.AGENT_PROMPTS.items():
        new_entry = new_prompts.AGENT_PROMPTS[stage_name]
        assert new_entry["user"] == legacy_entry["user"], stage_name


def test_brainstormer_prompt_formats_with_required_fields():
    formatted = new_prompts.AGENT_PROMPTS["brainstormer"]["user"].format(
        user_goal="Build a CLI todo app",
        tooling_context="uv",
    )
    legacy_formatted = legacy_prompts.AGENT_PROMPTS["brainstormer"]["user"].format(
        user_goal="Build a CLI todo app",
        tooling_context="uv",
    )
    assert formatted == legacy_formatted
    assert "Build a CLI todo app" in formatted


def test_planner_prompt_formats_with_required_fields():
    fields = dict(
        user_goal="Build a CLI todo app",
        tooling_context="uv",
        brainstorming_ideas="- Approach 1: ...",
        selected_approach="Approach 1",
    )
    formatted = new_prompts.AGENT_PROMPTS["planner"]["user"].format(**fields)
    legacy_formatted = legacy_prompts.AGENT_PROMPTS["planner"]["user"].format(**fields)
    assert formatted == legacy_formatted


def test_executor_prompt_formats_with_required_fields():
    fields = dict(
        user_goal="Build a CLI todo app",
        step_id=1,
        action_type="create_file",
        file_path="main.py",
        instruction="Create the entrypoint",
        existing_code="",
    )
    formatted = new_prompts.AGENT_PROMPTS["executor"]["user"].format(**fields)
    legacy_formatted = legacy_prompts.AGENT_PROMPTS["executor"]["user"].format(**fields)
    assert formatted == legacy_formatted


def test_code_reviewer_prompt_formats_with_required_fields():
    fields = dict(
        user_goal="Build a CLI todo app",
        plan_summary="1 step",
        file_list="main.py",
        execution_summary="ok",
        code_diffs="+print()",
        file_contents="print()",
    )
    formatted = new_prompts.AGENT_PROMPTS["code_reviewer"]["user"].format(**fields)
    legacy_formatted = legacy_prompts.AGENT_PROMPTS["code_reviewer"]["user"].format(
        **fields
    )
    assert formatted == legacy_formatted


def test_fixer_prompt_formats_with_required_fields():
    fields = dict(
        user_goal="Build a CLI todo app",
        file_path="main.py",
        current_code="print('x')",
        review_type="bug",
        severity="high",
        description="off by one",
        suggestion="fix range",
        line_range="1-3",
        code_snippet="print('x')",
    )
    formatted = new_prompts.AGENT_PROMPTS["fixer"]["user"].format(**fields)
    legacy_formatted = legacy_prompts.AGENT_PROMPTS["fixer"]["user"].format(**fields)
    assert formatted == legacy_formatted


def test_ralph_wiggum_reviewer_prompt_formats_with_required_fields():
    fields = dict(
        user_goal="Build a CLI todo app",
        self_reference_context="",
        worker_output="done",
        file_list="main.py",
        completion_promise="DONE",
    )
    formatted = new_prompts.AGENT_PROMPTS["ralph_wiggum_reviewer"]["user"].format(
        **fields
    )
    legacy_formatted = legacy_prompts.AGENT_PROMPTS["ralph_wiggum_reviewer"][
        "user"
    ].format(**fields)
    assert formatted == legacy_formatted


def test_brainstorming_reviewer_prompt_formats_with_required_fields():
    fields = dict(
        user_goal="Build a CLI todo app",
        tooling_context="uv",
        brainstorming_ideas="- Approach 1: ...",
    )
    formatted = new_prompts.AGENT_PROMPTS["brainstorming_reviewer"]["user"].format(
        **fields
    )
    legacy_formatted = legacy_prompts.AGENT_PROMPTS["brainstorming_reviewer"][
        "user"
    ].format(**fields)
    assert formatted == legacy_formatted
