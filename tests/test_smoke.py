"""Installed-entrypoint smoke tests (U5, integration).

Asserts the exact directory an installed `ai-orchestration` writes into and
that a default help invocation exits 0. Uses the venv-installed console
script directly (not `uv run`, which adds resolution overhead) and the
`AI_ORCHESTRATION_FAKE_PROVIDERS` offline switch so this stays a real
subprocess test without any live network I/O. Covers S1, S2, S3, S5, AE9
(component prerequisite for AE3, whose live proxy enforcement is U6).
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_INSTALLED_SCRIPT = REPO_ROOT / ".venv" / "bin" / "ai-orchestration"


def test_module_invocation_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "ai_orchestration.cli", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "--workspace" in result.stdout


def test_installed_console_script_help_exits_zero():
    assert _INSTALLED_SCRIPT.exists(), "run `uv sync` before this smoke test"
    result = subprocess.run(
        [str(_INSTALLED_SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "--workspace" in result.stdout


def test_installed_command_writes_exactly_beneath_workspace(tmp_path):
    env = dict(os.environ)
    env["AI_ORCHESTRATION_FAKE_PROVIDERS"] = "1"
    result = subprocess.run(
        [
            str(_INSTALLED_SCRIPT),
            "smoke test project",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "smoke",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    project_dir = tmp_path / "smoke"
    assert project_dir.exists()
    other_entries = [p for p in tmp_path.iterdir() if p != project_dir]
    assert other_entries == []
