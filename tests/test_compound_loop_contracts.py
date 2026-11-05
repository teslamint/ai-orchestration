"""Contract-boundary tests for the embedded compound-loop adapter."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

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

REQUIRED_PATHS = (
    "skills/release-loop/references/progress-schema.md",
    "skills/planning/schemas/plan-schema.md",
    "schemas/headless-contract.md",
    "skills/planning/scripts/validate-plan-frontmatter.py",
)


def _make_source(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "compound loop source"
    contents: dict[str, str] = {}
    for index, relative in enumerate(REQUIRED_PATHS):
        content = f"fixture contract {index}\n"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        contents[relative] = content

    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Contract Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "fixture"], check=True
    )
    return root, contents


def _write_manifest(
    path: Path,
    root: Path,
    contents: dict[str, str],
    *,
    schema: str = "eval-contract/v1",
) -> None:
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    payload = {
        "schema": schema,
        "source_commit": commit,
        "source_paths": {
            relative: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for relative, content in contents.items()
        },
        "supported_literals": {
            "plan_schema": ["plan/v1"],
            "headless_contract": ["v1"],
            "progress_schema": ["release-loop/v1"],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sealed_plan(path: Path, *, body: str = "\n# Plan\n\nBody\n") -> None:
    frontmatter = """schema: plan/v1
title: Fixture plan
type: feat
status: approved
date: 2026-08-30
execution: code
"""
    seal = hashlib.sha256(body.encode("utf-8")).hexdigest()
    path.write_text(f"---\n{frontmatter}body_seal: {seal}\n---{body}", encoding="utf-8")


def test_load_contract_source_validates_commit_paths_literals_and_digests(tmp_path):
    root, contents = _make_source(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, root, contents)

    source = load_contract_source(root, manifest)

    assert source.manifest.source_commit
    assert source.manifest.source_paths.keys() == set(REQUIRED_PATHS)
    assert source.files[REQUIRED_PATHS[0]] == contents[REQUIRED_PATHS[0]].encode()


def test_load_contract_source_fails_for_absent_or_changed_contract(tmp_path):
    root, contents = _make_source(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, root, contents)
    (root / REQUIRED_PATHS[0]).write_text("changed\n", encoding="utf-8")

    with pytest.raises(ContractError, match="digest mismatch"):
        load_contract_source(root, manifest)

    with pytest.raises(ContractError, match="source root"):
        load_contract_source(tmp_path / "missing", manifest)


def test_load_contract_source_rejects_symlinked_contract(tmp_path):
    root, contents = _make_source(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, root, contents)
    target = root / REQUIRED_PATHS[0]
    target.unlink()
    target.symlink_to(tmp_path / "outside.md")
    (tmp_path / "outside.md").write_text("fixture contract 0\n", encoding="utf-8")

    with pytest.raises(ContractError, match="symlink"):
        load_contract_source(root, manifest)


def test_load_contract_source_rejects_unknown_manifest_schema(tmp_path):
    root, contents = _make_source(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, root, contents, schema="eval-contract/v2")

    with pytest.raises(ContractError, match="manifest schema"):
        load_contract_source(root, manifest)


def test_validate_plan_checks_canonical_body_seal(tmp_path):
    plan = tmp_path / "plan.md"
    _sealed_plan(plan)

    result = validate_plan(plan)

    assert result.schema == "plan/v1"
    assert result.fields["status"] == "approved"
    assert result.body_seal == hashlib.sha256(b"\n# Plan\n\nBody\n").hexdigest()


def test_validate_plan_rejects_bad_seal_and_frontmatter(tmp_path):
    plan = tmp_path / "plan.md"
    _sealed_plan(plan, body="\n# Changed\n")
    text = plan.read_text(encoding="utf-8").replace("# Changed", "# Mutated")
    plan.write_text(text, encoding="utf-8")

    with pytest.raises(ContractError, match="body_seal"):
        validate_plan(plan)

    plan.write_text(
        "---\n"
        "schema: plan/v2\n"
        "title: Fixture plan\n"
        "type: feat\n"
        "status: approved\n"
        "date: 2026-08-30\n"
        "execution: code\n"
        "body_seal: " + "0" * 64 + "\n---\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="plan schema"):
        validate_plan(plan)


def test_validate_plan_rejects_missing_required_field(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("---\nschema: plan/v1\n---\nbody\n", encoding="utf-8")

    with pytest.raises(ContractError, match="required field"):
        validate_plan(plan)


def test_next_transition_covers_phase_boundaries_and_user_gates():
    transitions = (
        (ReleaseLoopState("design", "in-progress"), "design-approved", "plan"),
        (ReleaseLoopState("plan", "in-progress"), "plan-approved", "implement"),
        (
            ReleaseLoopState(
                "implement",
                "in-progress",
                completed_stages=(
                    "brainstormer",
                    "brainstorming_reviewer",
                    "planner",
                    "executor",
                    "code_reviewer",
                    "fixer",
                ),
            ),
            "implementation-complete",
            "review",
        ),
        (ReleaseLoopState("review", "in-progress"), "review-clean", "ship"),
        (ReleaseLoopState("ship", "waiting-user"), "ship-approved", "retro"),
        (ReleaseLoopState("retro", "in-progress"), "retro-complete", "done"),
    )

    for state, event, expected_phase in transitions:
        result = next_transition(state, event)
        assert result.state.phase == expected_phase
        assert result.state.phase_status in {"in-progress", "complete"}

    waiting = next_transition(
        ReleaseLoopState("ship", "in-progress"), "request-ship-approval"
    )
    assert waiting.state.phase_status == "waiting-user"


def test_stage_markers_are_sequential_and_resume_to_the_next_stage():
    state = ReleaseLoopState(
        "implement",
        "in-progress",
        completed_stages=("brainstormer", "brainstorming_reviewer"),
    )

    started = next_transition(state, "stage-start:planner")
    completed = next_transition(started.state, "stage-complete:planner")

    assert completed.state.current_stage is None
    assert completed.state.completed_stages[-1] == "planner"
    assert completed.state.next_stage == "executor"

    with pytest.raises(LedgerError, match="stage order"):
        next_transition(completed.state, "stage-start:fixer")


def test_ledger_round_trip_preserves_body_text(tmp_path):
    path = tmp_path / ".release-loop" / "progress.md"
    body = "\n# User notes\n\nDo not discard this text.\n"
    state = ReleaseLoopState("implement", "in-progress")
    save_ledger(state, path)
    path.write_text(path.read_text(encoding="utf-8") + body, encoding="utf-8")

    previous = path.read_bytes()
    loaded = load_ledger(path)
    assert loaded == ReleaseLoopState("implement", "in-progress", body=body)
    assert loaded is not None
    save_ledger(loaded, path)

    assert path.read_bytes() == previous
    assert path.read_text(encoding="utf-8").endswith(body)


def test_ledger_round_trip_is_atomic_and_preserves_previous_bytes(
    tmp_path, monkeypatch
):
    path = tmp_path / ".release-loop" / "progress.md"
    initial = ReleaseLoopState(
        "implement",
        "in-progress",
        current_stage="planner",
        completed_stages=("brainstormer", "brainstorming_reviewer"),
    )
    save_ledger(initial, path)
    previous = path.read_bytes()

    loaded = load_ledger(path)
    assert loaded == initial

    def fail_replace(source, target):
        raise OSError("injected replace failure")

    monkeypatch.setattr(
        "ai_orchestration.compound_loop.ledger.os.replace", fail_replace
    )
    with pytest.raises(LedgerError, match="atomic"):
        save_ledger(ReleaseLoopState("review", "in-progress"), path)
    assert path.read_bytes() == previous


def test_ledger_rejects_malformed_state_and_ship_retro_actions(tmp_path):
    path = tmp_path / "progress.md"
    path.write_text("---\nschema: unknown/v1\n---\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="schema"):
        load_ledger(path)
    with pytest.raises(LedgerError, match="external"):
        next_transition(ReleaseLoopState("ship", "in-progress"), "merge")
    with pytest.raises(LedgerError, match="external"):
        next_transition(ReleaseLoopState("retro", "in-progress"), "archive")


class _CliProvider:
    def complete(self, prompt, *, system=None, **kwargs):
        return (
            '[{"step_id": 1, "file_path": "hello.py", '
            '"action_type": "create_file", "instruction": "write hello"}]'
        )

    def complete_structured(self, prompt, *, schema, **kwargs):
        values = {}
        for field_name, field_info in schema.model_fields.items():
            if not field_info.is_required():
                continue
            annotation = str(field_info.annotation)
            if field_name in {"step_id", "total_files_reviewed"}:
                values[field_name] = 1
            elif field_name == "requires_fixes":
                values[field_name] = False
            elif annotation.startswith("typing.List") or annotation.startswith("list"):
                values[field_name] = []
            else:
                values[field_name] = "x"
        return schema(**values)

    def is_available(self):
        return True


def test_adapter_resume_preserves_next_model_stage(tmp_path):
    root, contents = _make_source(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, root, contents)
    ledger = tmp_path / ".release-loop" / "progress.md"

    adapter = CompoundLoopAdapter.prepare(root, manifest, ledger)
    for stage in ("brainstormer", "brainstorming_reviewer"):
        assert adapter.begin_stage(stage)
        adapter.complete_stage(stage)

    resumed = CompoundLoopAdapter.prepare(root, manifest, ledger, resume=True)

    assert resumed.next_stage == "planner"
    assert not resumed.begin_stage("brainstormer")
    assert resumed.begin_stage("planner")


def test_cli_opt_in_preflight_persists_phase_without_changing_stage_order(
    tmp_path, monkeypatch
):
    from typer.testing import CliRunner

    import ai_orchestration.cli as cli_module
    from ai_orchestration.config import CatalogOutcome, CatalogStatus

    root, contents = _make_source(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, root, contents)
    plan = tmp_path / "plan.md"
    _sealed_plan(plan)

    monkeypatch.setattr(
        cli_module, "_http_provider_factory", lambda model: _CliProvider()
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _CliProvider()
    )
    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda *args, **kwargs: CatalogStatus(
            outcome=CatalogOutcome.REACHABLE_WITH_MODELS,
            models=frozenset({"gemini-3.1-pro-low", "gpt-5.5", "claude-sonnet-5"}),
        ),
    )
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "build hello",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "compound_cli",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
            "--compound-loop-path",
            str(root),
            "--compound-loop-manifest",
            str(manifest),
            "--compound-loop-plan",
            str(plan),
        ],
    )

    assert result.exit_code == 0, result.stdout
    state = load_ledger(tmp_path / "compound_cli" / ".release-loop" / "progress.md")
    assert state is not None
    assert state.phase == "implement"
    assert state.completed_stages == (
        "brainstormer",
        "brainstorming_reviewer",
        "planner",
        "executor",
    )
    assert state.next_stage == "code_reviewer"


def test_cli_opt_in_rejects_missing_source_before_release_ledger_write(tmp_path):
    from typer.testing import CliRunner

    import ai_orchestration.cli as cli_module

    result = CliRunner().invoke(
        cli_module.app,
        [
            "build hello",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "compound_missing",
            "--compound-loop-path",
            str(tmp_path / "missing-source"),
            "--compound-loop-manifest",
            str(tmp_path / "manifest.json"),
        ],
    )

    assert result.exit_code != 0
    assert "contract source root" in result.stdout
    assert not (
        tmp_path / "compound_missing" / ".release-loop" / "progress.md"
    ).exists()


def test_cli_resume_requires_existing_run_state_for_existing_ledger(
    tmp_path, monkeypatch
):
    from typer.testing import CliRunner

    import ai_orchestration.cli as cli_module
    from ai_orchestration.config import CatalogOutcome, CatalogStatus

    root, contents = _make_source(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, root, contents)
    project = tmp_path / "compound_resume"
    ledger_path = project / ".release-loop" / "progress.md"
    save_ledger(ReleaseLoopState("implement", "in-progress"), ledger_path)
    monkeypatch.setattr(
        cli_module, "_http_provider_factory", lambda model: _CliProvider()
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _CliProvider()
    )
    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda *args, **kwargs: CatalogStatus(
            outcome=CatalogOutcome.REACHABLE_WITH_MODELS,
            models=frozenset({"gemini-3.1-pro-low", "gpt-5.5", "claude-sonnet-5"}),
        ),
    )
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "build hello",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "compound_resume",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--skip-review",
            "--resume",
            "--compound-loop-path",
            str(root),
            "--compound-loop-manifest",
            str(manifest),
        ],
    )

    assert result.exit_code != 0
    assert "existing release-loop ledger requires --resume" in result.stdout


def test_cli_zero_fix_iterations_marks_review_stage_optional(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import ai_orchestration.cli as cli_module
    from ai_orchestration.config import CatalogOutcome, CatalogStatus

    root, contents = _make_source(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, root, contents)
    plan = tmp_path / "plan.md"
    _sealed_plan(plan)
    monkeypatch.setattr(
        cli_module, "_http_provider_factory", lambda model: _CliProvider()
    )
    monkeypatch.setattr(
        cli_module, "_cli_provider_factory", lambda binary: _CliProvider()
    )
    monkeypatch.setattr(
        cli_module,
        "_probe_catalog_for_startup",
        lambda *args, **kwargs: CatalogStatus(
            outcome=CatalogOutcome.REACHABLE_WITH_MODELS,
            models=frozenset({"gemini-3.1-pro-low", "gpt-5.5", "claude-sonnet-5"}),
        ),
    )
    monkeypatch.setattr(cli_module, "_is_tty", lambda: False)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "build hello",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "compound_zero_fixes",
            "--auto-select",
            "--auto-run",
            "--auto-approve",
            "--max-fix-iterations",
            "0",
            "--compound-loop-path",
            str(root),
            "--compound-loop-manifest",
            str(manifest),
            "--compound-loop-plan",
            str(plan),
        ],
    )

    assert result.exit_code == 0, result.stdout
    state = load_ledger(
        tmp_path / "compound_zero_fixes" / ".release-loop" / "progress.md"
    )
    assert state is not None
    assert "code_reviewer" in state.completed_stages
    assert "fixer" in state.completed_stages
