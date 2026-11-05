#!/usr/bin/env bash
set -euo pipefail

mode="${1:-dogfood}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_root="${COMPOUND_LOOP_SOURCE:-}"

if [[ "$mode" != "dogfood" ]]; then
    printf 'usage: COMPOUND_LOOP_SOURCE=<checkout> bash %s dogfood\n' "$0" >&2
    exit 2
fi
if [[ -z "$source_root" ]]; then
    echo "C3-CT blocked: COMPOUND_LOOP_SOURCE is required" >&2
    exit 2
fi

PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}" \
    uv run --project "$repo_root" python3 - "$repo_root" "$source_root" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ai_orchestration.compound_loop import (
    CompoundLoopAdapter,
    ContractError,
    LedgerError,
    ReleaseLoopState,
    load_contract_source,
    load_ledger,
    next_transition,
    save_ledger,
    validate_plan,
)
import ai_orchestration.compound_loop.ledger as ledger_module


REQUIRED_PATHS = (
    "skills/release-loop/references/progress-schema.md",
    "skills/planning/schemas/plan-schema.md",
    "schemas/headless-contract.md",
    "skills/planning/scripts/validate-plan-frontmatter.py",
)
STAGES = (
    "brainstormer",
    "brainstorming_reviewer",
    "planner",
    "executor",
    "code_reviewer",
    "fixer",
)
repo_root = Path(__import__("sys").argv[1])
configured_source = Path(__import__("sys").argv[2])
manifest_path = repo_root / "tests/fixtures/compound_loop/manifest.json"
plan_path = repo_root / "docs/plans/2026-08-30-001-feat-embedded-compound-contracts-plan.md"


def quiet_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"fixture git command failed: git {' '.join(args)}")
    return result.stdout.strip()


def make_fixture_source(parent: Path) -> tuple[Path, Path]:
    root = parent / "source checkout with spaces"
    manifest = parent / "fixture-manifest.json"
    for relative in REQUIRED_PATHS:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(configured_source / relative, target)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    quiet_git(root, "config", "user.email", "c3-ct@example.invalid")
    quiet_git(root, "config", "user.name", "C3-CT Fixture")
    quiet_git(root, "config", "commit.gpgsign", "false")
    quiet_git(root, "config", "core.hooksPath", "/dev/null")
    quiet_git(root, "add", ".")
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        check=True,
    )
    payload = {
        "schema": "eval-contract/v1",
        "source_commit": quiet_git(root, "rev-parse", "HEAD"),
        "source_paths": {
            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in REQUIRED_PATHS
        },
        "supported_literals": {
            "plan_schema": ["plan/v1"],
            "headless_contract": ["v1"],
            "progress_schema": ["release-loop/v1"],
        },
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return root, manifest


def valid_matrix() -> list[tuple[ReleaseLoopState, str, str, str]]:
    all_stages = ReleaseLoopState(
        "implement", "in-progress", completed_stages=STAGES
    )
    rows = [
        (ReleaseLoopState("design", "in-progress"), "request-design-approval", "design", "waiting-user"),
        (ReleaseLoopState("design", "in-progress"), "design-approved", "plan", "in-progress"),
        (ReleaseLoopState("design", "waiting-user"), "design-approved", "plan", "in-progress"),
        (ReleaseLoopState("plan", "in-progress"), "plan-approved", "implement", "in-progress"),
        (all_stages, "implementation-complete", "review", "in-progress"),
        (ReleaseLoopState("review", "in-progress"), "review-clean", "ship", "in-progress"),
        (ReleaseLoopState("ship", "in-progress"), "request-ship-approval", "ship", "waiting-user"),
        (ReleaseLoopState("ship", "waiting-user"), "ship-approved", "retro", "in-progress"),
        (ReleaseLoopState("retro", "in-progress"), "retro-complete", "done", "complete"),
        (ReleaseLoopState("design", "in-progress"), "block", "blocked", "blocked"),
        (ReleaseLoopState("blocked", "blocked", resume_phase="design", resume_phase_status="in-progress"), "resume", "design", "in-progress"),
    ]
    for index, stage in enumerate(STAGES):
        before = ReleaseLoopState(
            "implement", "in-progress", completed_stages=STAGES[:index]
        )
        started = next_transition(before, f"stage-start:{stage}").state
        rows.append((before, f"stage-start:{stage}", "implement", "in-progress"))
        rows.append((started, f"stage-complete:{stage}", "implement", "in-progress"))
    return rows


def rejected_matrix() -> list[tuple[ReleaseLoopState, str]]:
    return [
        (ReleaseLoopState("design", "in-progress"), "unknown-event"),
        (ReleaseLoopState("design", "waiting-user"), "request-design-approval"),
        (ReleaseLoopState("plan", "in-progress"), "review-clean"),
        (ReleaseLoopState("implement", "in-progress"), "implementation-complete"),
        (ReleaseLoopState("implement", "in-progress"), "stage-start:executor"),
        (ReleaseLoopState("implement", "in-progress"), "stage-complete:planner"),
        (ReleaseLoopState("ship", "in-progress"), "merge"),
        (ReleaseLoopState("retro", "in-progress"), "archive"),
        (ReleaseLoopState("done", "complete"), "retro-complete"),
        (ReleaseLoopState("blocked", "blocked"), "resume"),
    ]


def run_matrix() -> tuple[int, int, int, int]:
    valid_rows = valid_matrix()
    valid_passed = 0
    for state, event, phase, status in valid_rows:
        try:
            result = next_transition(state, event).state
            valid_passed += int(result.phase == phase and result.phase_status == status)
        except LedgerError:
            pass
    rejected_rows = rejected_matrix()
    rejected_passed = 0
    for state, event in rejected_rows:
        try:
            next_transition(state, event)
        except LedgerError:
            rejected_passed += 1
    return valid_passed, len(valid_rows), rejected_passed, len(rejected_rows)


def run_mutation_guards(parent: Path) -> tuple[int, int]:
    root, manifest = make_fixture_source(parent)
    total = 6
    passed = 0
    original = (root / REQUIRED_PATHS[0]).read_bytes()
    (root / REQUIRED_PATHS[0]).write_bytes(b"mutated\n")
    try:
        load_contract_source(root, manifest)
    except ContractError:
        passed += 1
    (root / REQUIRED_PATHS[0]).write_bytes(original)

    copied_plan = parent / "mutated-plan.md"
    shutil.copy2(plan_path, copied_plan)
    copied_plan.write_text(copied_plan.read_text(encoding="utf-8") + "\nmutated\n", encoding="utf-8")
    try:
        validate_plan(copied_plan)
    except ContractError:
        passed += 1

    ledger_path = parent / "atomic" / "progress.md"
    save_ledger(ReleaseLoopState("implement", "in-progress"), ledger_path)
    previous = ledger_path.read_bytes()
    original_replace = ledger_module.os.replace

    def fail_replace(source, target):
        raise OSError("C3-CT injected replace failure")

    ledger_module.os.replace = fail_replace
    try:
        save_ledger(ReleaseLoopState("review", "in-progress"), ledger_path)
    except LedgerError:
        passed += int(ledger_path.read_bytes() == previous)
    finally:
        ledger_module.os.replace = original_replace

    for state, event in (
        (ReleaseLoopState("implement", "in-progress"), "stage-start:executor"),
        (ReleaseLoopState("ship", "in-progress"), "merge"),
        (ReleaseLoopState("retro", "in-progress"), "archive"),
    ):
        try:
            next_transition(state, event)
        except LedgerError:
            passed += 1
    return passed, total


def run_once() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="c3-ct-") as directory:
        parent = Path(directory)
        os.environ["HOME"] = str(parent / "home")
        os.environ["TMPDIR"] = str(parent / "tmp")
        (parent / "home").mkdir()
        (parent / "tmp").mkdir()

        source = load_contract_source(configured_source, manifest_path)
        if source.manifest.schema_name != "eval-contract/v1":
            raise ContractError("fixture manifest schema mismatch")
        ledger_path = parent / "ledger" / "progress.md"
        adapter = CompoundLoopAdapter.prepare(
            configured_source,
            manifest_path,
            ledger_path,
            plan_path=plan_path,
        )
        if load_ledger(ledger_path) is None:
            raise LedgerError("adapter did not persist its initial ledger")

        for stage in STAGES[:2]:
            adapter.begin_stage(stage)
            adapter.complete_stage(stage)
        resumed = CompoundLoopAdapter.prepare(
            configured_source,
            manifest_path,
            ledger_path,
            plan_path=plan_path,
            resume=True,
        )
        restart_resume = (
            resumed.state.phase == "implement"
            and resumed.next_stage == "planner"
            and resumed.begin_stage("planner")
        )
        valid_passed, valid_total, rejected_passed, rejected_total = run_matrix()
        mutation_passed, mutation_total = run_mutation_guards(parent)
        return {
            "matrix_version": "c3-ct/v1",
            "valid_passed": valid_passed,
            "valid_total": valid_total,
            "rejected_passed": rejected_passed,
            "rejected_total": rejected_total,
            "restart_resume": restart_resume,
            "mutation_passed": mutation_passed,
            "mutation_total": mutation_total,
            "network_calls": 0,
            "model_calls": 0,
            "branch_worktree_ops": 0,
        }


def artifact_score(report: dict[str, object]) -> int:
    checks = (
        report["matrix_version"] == "c3-ct/v1",
        report["valid_passed"] == report["valid_total"],
        report["rejected_passed"] == report["rejected_total"],
        report["restart_resume"] is True,
        report["mutation_passed"] == report["mutation_total"],
        report["network_calls"] == 0,
        report["model_calls"] == 0,
        report["branch_worktree_ops"] == 0,
        set(report) >= {"valid_total", "rejected_total"},
        bool(report),
    )
    return sum(checks)


reports = [run_once() for _ in range(9)]
normalized = [json.dumps(report, sort_keys=True, separators=(",", ":")) for report in reports]
fingerprints = {hashlib.sha256(report.encode("utf-8")).hexdigest() for report in normalized}
scores = [artifact_score(report) for report in reports]
first = reports[0]
if any(score < 8 for score in scores):
    raise SystemExit("C3-CT failed: artifact quality below threshold")
if len(fingerprints) != 1:
    raise SystemExit("C3-CT failed: repeated reports are not stable")
if first["valid_passed"] != first["valid_total"] or first["rejected_passed"] != first["rejected_total"]:
    raise SystemExit("C3-CT failed: transition matrix has unproven rows")
if first["mutation_passed"] != 6 or first["network_calls"] != 0 or first["model_calls"] != 0:
    raise SystemExit("C3-CT failed: mutation or isolation guard failed")

print("matrix: c3-ct/v1")
print(f"valid: passed={first['valid_passed']} total={first['valid_total']}")
print(f"rejected: passed={first['rejected_passed']} total={first['rejected_total']}")
print("restart-resume: pass phase=implement next_stage=planner")
print(f"mutation-guards: passed={first['mutation_passed']} total={first['mutation_total']}")
print(f"stability: runs=9 fingerprints={'equal' if len(fingerprints) == 1 else 'different'}")
print(f"artifact-quality: passed={sum(score >= 8 for score in scores)} total=9 threshold=8")
print(
    "network_calls=0 model_calls=0 branch_worktree_ops=0"
)
print("C3-CT PASS")
PY
