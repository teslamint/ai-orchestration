"""Prompt-template contracts for the orchestration stages.

Most templates are preserved byte-for-byte from committed `agent_prompts.py`
at `8ee3c4c`. U6 deletes that module, so hashes pin those templates without
re-importing the deleted module. The planner's Repository Notes intentionally
refer to the replacement extraction module and helpers, and its hash captures
that corrected contract.
"""

import hashlib

from ai_orchestration.prompts import stages as new_prompts

# MD5(system), MD5(user) captured from a live byte-for-byte comparison
# against the committed `agent_prompts.py` (at 8ee3c4c) before its deletion
# in U6's clean cutover.
_COMMITTED_PROMPT_HASHES = {
    "brainstormer": (
        "7937b65716398bb2ffafc13fe59bffc3",
        "69cb97db3acad258abbe9379805f8986",
    ),
    "brainstorming_reviewer": (
        "f896f68cdce53ee60e3dfe79d6c7e15f",
        "c0c1a6fc4d4fd05ecc19a7a5d2b10818",
    ),
    "planner": (
        "d19246bbac859a971777da6565c3570d",
        "20dae7e86d59211804305ea541b486c3",
    ),
    "executor": (
        "87acfa330509b04f03ef6a2fa7063ba7",
        "94bbbb31561df0bd9af21368810ebffa",
    ),
    "code_reviewer": (
        "73e59fdb58d07ac2eda568e150b710ec",
        "0d21c1096b42de75e9b055c1d1b5334c",
    ),
    "fixer": (
        "c34755c9b839991fe87cf7c892614abb",
        "20c411740c6b76d92cbddbf440d7a9f8",
    ),
    "ralph_wiggum_reviewer": (
        "cfb22732f0d41cbdd8b8b67feef424bb",
        "349d2d0a5b92bfa00e5757c62386835b",
    ),
}


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def test_agent_prompts_dict_has_all_seven_stage_keys():
    assert set(new_prompts.AGENT_PROMPTS) == set(_COMMITTED_PROMPT_HASHES)


def test_all_system_prompts_match_committed_hash():
    for stage_name, (system_hash, _user_hash) in _COMMITTED_PROMPT_HASHES.items():
        entry = new_prompts.AGENT_PROMPTS[stage_name]
        assert _md5(entry["system"]) == system_hash, stage_name


def test_all_user_templates_match_committed_hash():
    for stage_name, (_system_hash, user_hash) in _COMMITTED_PROMPT_HASHES.items():
        entry = new_prompts.AGENT_PROMPTS[stage_name]
        assert _md5(entry["user"]) == user_hash, stage_name


def test_brainstormer_prompt_formats_with_required_fields():
    formatted = new_prompts.AGENT_PROMPTS["brainstormer"]["user"].format(
        user_goal="Build a CLI todo app",
        tooling_context="uv",
    )
    assert "Build a CLI todo app" in formatted


def test_planner_prompt_formats_with_required_fields():
    fields = dict(
        user_goal="Build a CLI todo app",
        tooling_context="uv",
        brainstorming_ideas="- Approach 1: ...",
        selected_approach="Approach 1",
    )
    formatted = new_prompts.AGENT_PROMPTS["planner"]["user"].format(**fields)
    assert "Build a CLI todo app" in formatted
    assert "Approach 1" in formatted


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
    assert "main.py" in formatted
    assert "Create the entrypoint" in formatted


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
    assert "main.py" in formatted


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
    assert "off by one" in formatted


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
    assert "DONE" in formatted


def test_brainstorming_reviewer_prompt_formats_with_required_fields():
    fields = dict(
        user_goal="Build a CLI todo app",
        tooling_context="uv",
        brainstorming_ideas="- Approach 1: ...",
    )
    formatted = new_prompts.AGENT_PROMPTS["brainstorming_reviewer"]["user"].format(
        **fields
    )
    assert "Approach 1" in formatted
