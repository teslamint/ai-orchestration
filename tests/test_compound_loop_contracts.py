"""Contract-boundary tests for the embedded compound-loop adapter."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from ai_orchestration.compound_loop import (
    ContractError,
    load_contract_source,
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
