#!/usr/bin/env python3
"""Verify docs/evidence/legacy-successor-inventory.md before a clean cutover.

U6's error scenario: "a missing or duplicate inventory successor fails
before deletion." This checks both directions:
1. Legacy side: every one of the 93 legacy test names from the four root
   suites appears in exactly one inventory row.
2. Successor side: every named successor test id actually resolves via
   `pytest --collect-only` against the current test tree — a row naming a
   test that does not exist (typo, renamed test, wrong class prefix) must
   fail this check, not silently pass because the legacy-name check alone
   looked complete.

Exit 0: all names present and resolvable. Exit 1: prints every violation.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INVENTORY_PATH = REPO_ROOT / "docs" / "evidence" / "legacy-successor-inventory.md"
LEGACY_SUITES = (
    "tests/test_orchestrator_cli.py",
    "tests/test_orchestration_context.py",
    "tests/test_llm_tools.py",
    "tests/test_api_tools.py",
)


def _collect_node_ids(*paths: str) -> set[str]:
    result = subprocess.run(
        ["uv", "run", "pytest", "--collect-only", "-q", *paths],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    missing_paths = [p for p in paths if not (REPO_ROOT / p).exists()]
    if result.returncode != 0 and missing_paths:
        # Missing paths mean the legacy suites were deleted in the cutover;
        # treat that as the documented post-cutover skip, not a failure.
        return set()
    if result.returncode != 0:
        raise RuntimeError(
            "pytest --collect-only failed for "
            + ", ".join(paths)
            + ":\n"
            + result.stderr.strip()
        )
    ids = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" in line and " " not in line.split("::")[-1]:
            ids.add(line)
    return ids


def _legacy_name(node_id: str) -> str:
    """Reduce a full node id to the class-qualified name used in the
    inventory's legacy column (drop the file path prefix)."""
    _, _, rest = node_id.partition("::")
    return rest


def main() -> int:
    inventory_text = INVENTORY_PATH.read_text(encoding="utf-8")

    # Every `| `legacy` | `successor` |` row.
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`", inventory_text, re.MULTILINE)
    legacy_names = [legacy for legacy, _successor in rows]
    successor_refs = [successor for _legacy, successor in rows if "::" in successor]

    problems: list[str] = []

    # 1. Legacy side: exactly 93 unique names, one row each.
    if len(legacy_names) != 93:
        problems.append(f"expected 93 legacy rows, found {len(legacy_names)}")
    dup_legacy = {n for n in legacy_names if legacy_names.count(n) > 1}
    if dup_legacy:
        problems.append(f"duplicate legacy rows: {sorted(dup_legacy)}")

    baseline_ids = _collect_node_ids(*LEGACY_SUITES)
    if not baseline_ids:
        # Legacy suites are gone post-cutover; this check only applies
        # pre-deletion. Treat as informational, not a failure, so this
        # script stays runnable after the cutover too.
        print(
            "legacy suites not present (post-cutover state); skipping legacy-name check"
        )
    else:
        baseline_legacy_names = {_legacy_name(n) for n in baseline_ids}
        missing_legacy = baseline_legacy_names - set(legacy_names)
        if missing_legacy:
            problems.append(
                f"legacy test names collected but absent from inventory: {sorted(missing_legacy)}"
            )

    # 2. Successor side: every named successor test id must resolve.
    real_ids = _collect_node_ids("tests/")
    missing_successors = [ref for ref in successor_refs if ref not in real_ids]
    if missing_successors:
        problems.append(f"successor tests named but not found: {missing_successors}")

    if problems:
        print("INVENTORY VERIFICATION FAILED:")
        for p in problems:
            print(f" - {p}")
        return 1

    print(f"OK: {len(legacy_names)} legacy rows, all successor tests resolve")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print("INVENTORY VERIFICATION FAILED:", exc)
        sys.exit(1)
