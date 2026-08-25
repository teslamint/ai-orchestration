"""Characterization tests for pure utility helpers (U2).

Ports the ten legacy `orchestrator_cli.py` helper assertions plus edge/error
fixtures that were never exercised by the legacy suite; see
docs/evidence/legacy-successor-inventory.md for the row mapping.
"""

from ai_orchestration.utils.diff import generate_diff
from ai_orchestration.utils.extract import (
    extract_code_content,
    extract_json_list,
)
from ai_orchestration.utils.slug import generate_command_slug, generate_project_name

# --- extract_json_list ---------------------------------------------------


def test_extract_json_list_from_embedded_text():
    text = 'prefix [{"step_id": 1, "name": "alpha"}] suffix'
    assert extract_json_list(text) == [{"step_id": 1, "name": "alpha"}]


def test_extract_json_list_no_json_returns_empty_list():
    text = "there is no json list here"
    assert extract_json_list(text) == []


def test_extract_json_list_direct_json():
    text = '[{"step_id": 1}]'
    assert extract_json_list(text) == [{"step_id": 1}]


def test_extract_json_list_takes_last_candidate_when_multiple_present():
    # edge: embedded prose with two bracketed lists; legacy behavior keeps
    # the last fully-parseable list-of-dicts candidate.
    text = 'ignore [{"step_id": 0}] then use [{"step_id": 1}, {"step_id": 2}]'
    assert extract_json_list(text) == [{"step_id": 1}, {"step_id": 2}]


def test_extract_json_list_ignores_list_of_non_dicts():
    # error: a bracketed list whose items are not dicts is not a valid
    # candidate and must not be returned.
    text = "here is a list [1, 2, 3] with no valid task list"
    assert extract_json_list(text) == []


# --- extract_code_content -------------------------------------------------


def test_extract_code_content_from_fenced_block():
    text = 'Here is code:\n```python\nprint("hi")\n```\n'
    assert extract_code_content(text) == 'print("hi")'


def test_extract_code_content_fenced_block_strips_surrounding_whitespace():
    text = '```python\n  print("hi")  \n```'
    assert extract_code_content(text) == 'print("hi")'


def test_extract_code_content_no_fence_returns_stripped_text():
    text = "  plain text  "
    assert extract_code_content(text) == "plain text"


def test_extract_code_content_unterminated_fence_returns_from_start():
    # edge: an opening fence with no closing fence still returns content
    # starting after the opener, matching legacy rfind-based fallback.
    text = "```python\nprint('hi')\n"
    assert extract_code_content(text) == "print('hi')"


def test_extract_code_content_language_tag_is_skipped():
    text = "```python\nx = 1\n```"
    assert extract_code_content(text) == "x = 1"


# --- generate_diff --------------------------------------------------------


def test_generate_diff_new_file():
    old = ""
    new = "print('hello')\n"
    diff = generate_diff(old, new, "test.py")
    assert "+print('hello')" in diff
    assert "b/test.py" in diff


def test_generate_diff_modified_file():
    old = "print('hello')\n"
    new = "print('world')\n"
    diff = generate_diff(old, new, "test.py")
    assert "+print('world')" in diff


def test_generate_diff_no_changes():
    content = "print('hello')\n"
    diff = generate_diff(content, content, "test.py")
    assert diff == ""


def test_generate_diff_unchanged_file_case_is_empty_string_not_none():
    # error/edge boundary: legacy returns "" (falsy but not None) when both
    # inputs are identical, distinguishing "no diff" from "diff unavailable".
    diff = generate_diff("same\n", "same\n", "a.py")
    assert diff == ""
    assert diff is not None


# --- generate_project_name / generate_command_slug -------------------------


def test_generate_project_name_from_goal():
    assert generate_project_name("Hello World") == "hello_world"


def test_generate_project_name_non_english():
    assert generate_project_name("한글 테스트") == "project"


def test_generate_project_name_length_limit():
    result = generate_project_name("a" * 50)
    assert result == "a" * 30


def test_generate_project_name_empty_string_falls_back_to_default():
    # edge: never asserted by the legacy suite; an all-punctuation goal
    # strips to nothing and must fall back to the "project" default.
    assert generate_project_name("!!!???") == "project"


def test_generate_command_slug_truncates_and_strips_trailing_underscore():
    slug = generate_command_slug("uv run pytest -v --some-very-long-flag-name")
    assert len(slug) <= 30
    assert not slug.endswith("_")


def test_generate_command_slug_empty_command_falls_back_to_cmd():
    assert generate_command_slug("!!!") == "cmd"
