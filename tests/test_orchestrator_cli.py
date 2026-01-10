"""Tests for orchestrator_cli helpers."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator_cli import _extract_code_content, _extract_json_list, _generate_diff


def test_extract_json_list_from_embedded_text():
    text = 'prefix [{"step_id": 1, "name": "alpha"}] suffix'
    assert _extract_json_list(text) == [{"step_id": 1, "name": "alpha"}]


def test_extract_json_list_no_json_returns_empty_list():
    text = "there is no json list here"
    assert _extract_json_list(text) == []


def test_extract_code_content_from_fenced_block():
    text = """Here is code:
```python
print("hi")
```
done."""
    assert _extract_code_content(text) == 'print("hi")'


def test_extract_code_content_no_fence_returns_stripped_text():
    text = "  plain text  "
    assert _extract_code_content(text) == "plain text"


def test_generate_diff_new_file():
    old = ""
    new = "print('hello')\n"
    diff = _generate_diff(old, new, "test.py")
    assert "+print('hello')" in diff
    assert "a/test.py" in diff
    assert "b/test.py" in diff


def test_generate_diff_modified_file():
    old = "print('hello')\n"
    new = "print('world')\n"
    diff = _generate_diff(old, new, "test.py")
    assert "-print('hello')" in diff
    assert "+print('world')" in diff


def test_generate_diff_no_changes():
    content = "print('hello')\n"
    diff = _generate_diff(content, content, "test.py")
    assert diff == ""


def test_main_has_auto_select_option():
    """Verify --auto-select option is defined in main function."""
    import inspect
    from orchestrator_cli import main

    sig = inspect.signature(main)
    assert "auto_select" in sig.parameters
    param = sig.parameters["auto_select"]
    assert param.default.default is False


def test_cli_auto_select_option_help():
    """Verify --auto-select appears in CLI help."""
    from typer.testing import CliRunner
    from orchestrator_cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert "--auto-select" in result.output
    assert "접근 방식 자동 선택" in result.output
