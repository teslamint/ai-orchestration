"""Validate a configured compound-loop checkout as read-only contract data."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_orchestration.errors import OrchestrationError


class ContractError(OrchestrationError):
    """A configured compound-loop contract is absent or incompatible."""


REQUIRED_SOURCE_PATHS = (
    "skills/release-loop/references/progress-schema.md",
    "skills/planning/schemas/plan-schema.md",
    "schemas/headless-contract.md",
    "skills/planning/scripts/validate-plan-frontmatter.py",
)
REQUIRED_LITERAL_SETS = {
    "plan_schema": frozenset({"plan/v1"}),
    "headless_contract": frozenset({"v1"}),
    "progress_schema": frozenset({"release-loop/v1"}),
}
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


class ContractManifest(BaseModel):
    """Pinned source identity and literal vocabulary for the adapter."""

    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["eval-contract/v1"] = Field(alias="schema")
    source_commit: str = Field(min_length=40, max_length=64)
    source_paths: dict[str, str]
    supported_literals: dict[str, list[str]]


@dataclass(frozen=True)
class ContractSource:
    """Validated source files, retained as bytes for deterministic consumers."""

    root: Path
    manifest: ContractManifest
    files: dict[str, bytes]


def _read_manifest(path: Path) -> ContractManifest:
    if not path.exists() or not path.is_file():
        raise ContractError(f"contract manifest is not a regular file: {path}")
    if path.is_symlink():
        raise ContractError(f"contract manifest is a symlink: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("schema") != "eval-contract/v1":
            raise ContractError("manifest schema is unsupported")
        return ContractManifest.model_validate(payload)
    except ContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ContractError(f"invalid contract manifest: {path}") from exc


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ContractError(f"contract source commit is unavailable: {root}")
    commit = result.stdout.strip()
    if not _COMMIT_RE.fullmatch(commit):
        raise ContractError("contract source commit is not a full object id")
    return commit


def _validate_relative_source_path(relative: str) -> Path:
    path = Path(relative)
    if (
        not relative
        or path.is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(char) < 32 or ord(char) == 127 for char in relative)
    ):
        raise ContractError(f"invalid contract source path: {relative}")
    return path


def _reject_symlinked_components(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ContractError(f"contract source path is a symlink: {current}")
    return current


def load_contract_source(source_root: Path, manifest_path: Path) -> ContractSource:
    """Load and verify the pinned contract files without importing the source."""

    root = Path(source_root)
    if not root.exists() or not root.is_dir():
        raise ContractError(f"contract source root is unavailable: {root}")
    if root.is_symlink():
        raise ContractError(f"contract source root is a symlink: {root}")

    manifest = _read_manifest(Path(manifest_path))
    if not _COMMIT_RE.fullmatch(manifest.source_commit):
        raise ContractError("manifest source_commit is not a full object id")
    if _git_head(root) != manifest.source_commit:
        raise ContractError("contract source commit mismatch")

    if set(manifest.source_paths) != set(REQUIRED_SOURCE_PATHS):
        raise ContractError("manifest source_paths do not match required contracts")
    if set(manifest.supported_literals) != set(REQUIRED_LITERAL_SETS):
        raise ContractError("manifest supported_literals do not match contract keys")
    for key, required in REQUIRED_LITERAL_SETS.items():
        values = manifest.supported_literals[key]
        if set(values) != required or len(values) != len(set(values)):
            raise ContractError(f"manifest supported_literals mismatch: {key}")

    files: dict[str, bytes] = {}
    for relative in REQUIRED_SOURCE_PATHS:
        relative_path = _validate_relative_source_path(relative)
        path = _reject_symlinked_components(root, relative_path)
        if not path.exists() or not path.is_file():
            raise ContractError(f"required contract file is unavailable: {relative}")
        expected = manifest.source_paths[relative]
        if not _DIGEST_RE.fullmatch(expected):
            raise ContractError(f"invalid digest in manifest: {relative}")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ContractError(
                f"cannot read required contract file: {relative}"
            ) from exc
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise ContractError(f"contract file digest mismatch: {relative}")
        files[relative] = content

    return ContractSource(root=root, manifest=manifest, files=files)
