"""Validate sealed ``plan/v1`` documents without importing compound-loop."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ai_orchestration.compound_loop.source import ContractError

REQUIRED_FIELDS = ("schema", "title", "type", "status", "date", "execution")
_TYPES = frozenset({"feat", "fix", "refactor", "chore", "docs"})
_STATUSES = frozenset({"draft", "approved", "done", "superseded"})
_EXECUTIONS = frozenset({"code", "non-code", "ops"})
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_SEAL_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class PlanValidation:
    """The owned frontmatter fields and verified canonical body seal."""

    path: Path
    fields: dict[str, str]
    body_seal: str

    @property
    def schema(self) -> str:
        return self.fields["schema"]


def _parse_frontmatter(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line or line.startswith((" ", "\t")):
            raise ContractError("plan frontmatter contains an invalid line")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ContractError("plan frontmatter contains an empty field")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key] = value
    return fields


def validate_plan(path: Path) -> PlanValidation:
    """Validate a sealed approved plan using the canonical body extraction."""

    plan_path = Path(path)
    if not plan_path.exists() or not plan_path.is_file():
        raise ContractError(f"plan is not a regular file: {plan_path}")
    if plan_path.is_symlink():
        raise ContractError(f"plan is a symlink: {plan_path}")
    try:
        with plan_path.open("r", encoding="utf-8", newline=None) as handle:
            text = handle.read()
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"cannot read plan: {plan_path}") from exc

    parts = text.split("---", 2)
    if len(parts) != 3 or not text.startswith("---"):
        raise ContractError("plan frontmatter delimiters are invalid")
    fields = _parse_frontmatter(parts[1].splitlines())
    missing = [field for field in REQUIRED_FIELDS if not fields.get(field)]
    if missing:
        raise ContractError(f"plan required field is missing: {missing[0]}")
    if fields["schema"] != "plan/v1":
        raise ContractError(f"plan schema is unsupported: {fields['schema']}")
    if fields["type"] not in _TYPES:
        raise ContractError("plan type is unsupported")
    if fields["status"] != "approved":
        if fields["status"] not in _STATUSES:
            raise ContractError("plan status is unsupported")
        raise ContractError("plan status must be approved")
    if not _DATE_RE.fullmatch(fields["date"]):
        raise ContractError("plan date is invalid")
    if fields["execution"] not in _EXECUTIONS:
        raise ContractError("plan execution is unsupported")

    seal = fields.get("body_seal")
    if seal is None or not _SEAL_RE.fullmatch(seal):
        raise ContractError("plan body_seal is missing or invalid")
    actual = hashlib.sha256(parts[2].encode("utf-8")).hexdigest()
    if actual != seal:
        raise ContractError("plan body_seal mismatch")
    return PlanValidation(path=plan_path, fields=fields, body_seal=seal)
