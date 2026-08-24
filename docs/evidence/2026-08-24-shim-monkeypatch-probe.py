#!/usr/bin/env python3
"""Evidence for spec v2 assumption A4: re-export shims break monkeypatch seams.

Self-contained and repo-independent. Run:

    uv run --no-project --python 3.13 --with pytest \
        python docs/evidence/2026-08-24-shim-monkeypatch-probe.py

Exit 0 means the finding reproduces: patching the name on a *shim* module does
not affect the already-bound callee, while patching it on the module that
*owns* both function and caller does.

Why this matters: tests/test_orchestrator_cli.py drives the ralph-loop CLI with
`monkeypatch.setattr(orchestrator_cli, "_run_ralph_loop_review", fake)` so no
real LLM is invoked. Under a shim the patch silently misses and the command
calls the real implementation -- the test would appear to pass while making
live calls. That is why the rewrite deletes root modules instead of shimming
them (spec decision 5).
"""

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

CASE = {
    "pkg/__init__.py": "",
    "pkg/impl.py": textwrap.dedent("""
        def _run_review(**kwargs):
            return "REAL"

        def command():
            # Resolved in pkg.impl's globals, not in whatever module re-exports it.
            return _run_review()
    """),
    # Style A: root module only re-exports (the rejected design).
    "shim_root.py": "from pkg.impl import _run_review, command\n",
    # Style B: root module owns function and caller (today's layout).
    "owned_root.py": textwrap.dedent("""
        def _run_review(**kwargs):
            return "REAL"

        def command():
            return _run_review()
    """),
    "test_seam.py": textwrap.dedent("""
        import owned_root
        import shim_root


        def test_shim_loses_the_patch(monkeypatch):
            monkeypatch.setattr(shim_root, "_run_review", lambda **k: "FAKE")
            assert shim_root.command() == "REAL", "expected the shim to lose the patch"


        def test_owned_module_keeps_the_patch(monkeypatch):
            monkeypatch.setattr(owned_root, "_run_review", lambda **k: "FAKE")
            assert owned_root.command() == "FAKE"
    """),
}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, body in CASE.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "test_seam.py"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        print(result.stdout.strip())
        if result.returncode == 0:
            print("\nA4 REPRODUCED: shim loses the patch; owning module keeps it.")
        else:
            print("\nA4 DID NOT REPRODUCE -- investigate before trusting decision 5.")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
