"""C3-CT dogfood gate for the embedded compound-loop contracts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "test-compound-loop-contracts.sh"


def test_c3_ct_matrix_and_nine_run_gate():
    # The gate needs a compound-loop checkout at the manifest's pinned commit.
    # CI provides one; locally an absent source skips rather than guessing a path.
    source = os.environ.get("COMPOUND_LOOP_SOURCE")
    if not source:
        if os.environ.get("CI"):
            pytest.fail("CI must set COMPOUND_LOOP_SOURCE for the C3-CT gate")
        pytest.skip("set COMPOUND_LOOP_SOURCE to a compound-loop checkout")
    env = dict(os.environ)
    env["COMPOUND_LOOP_SOURCE"] = source
    result = subprocess.run(
        ["bash", str(SCRIPT), "dogfood"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "matrix: c3-ct/v1" in output
    assert "valid: passed=" in output
    assert "rejected: passed=" in output
    assert "restart-resume: pass" in output
    assert "mutation-guards: passed=6 total=6" in output
    assert "stability: runs=9 fingerprints=equal" in output
    assert "artifact-quality: passed=9 total=9 threshold=8" in output
    assert "network_calls=0 model_calls=0 branch_worktree_ops=0" in output
    assert "C3-CT PASS" in output
