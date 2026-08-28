#!/usr/bin/env python3
"""Offline-first command surface for the dogfood/v1 release contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any

from ai_orchestration.evals.dogfood import (
    ContractError,
    consume_receipt,
    load_eval_contract,
    validate_result,
)

LIVE_COMMANDS = {"pilot", "full", "grading", "authorize-invocation"}


class EvalArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ContractError(message)


def _parser() -> argparse.ArgumentParser:
    parser = EvalArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")

    preflight = commands.add_parser("preflight")
    _add(preflight, "--contract", "--baseline-manifest", "--candidate-manifest")

    local = commands.add_parser("local")
    _add(local, "--contract", "--build-manifest", "--output")

    approval = commands.add_parser("approval-packet")
    _add(
        approval,
        "--scope",
        "--contract",
        "--baseline-manifest",
        "--candidate-manifest",
        "--output",
    )

    authorize = commands.add_parser("authorize-invocation")
    _add(authorize, "--packet", "--receipt", "--state")

    grading_packet = commands.add_parser("grading-packet")
    _add(grading_packet, "--state", "--artifact-manifest", "--output")

    for name in ("pilot", "full", "grading"):
        command = commands.add_parser(name)
        _add(command, "--packet", "--receipt", "--state")
    return parser


def _add(parser: argparse.ArgumentParser, *names: str) -> None:
    for name in names:
        parser.add_argument(name)


def _required(args: argparse.Namespace, *names: str) -> list[Path]:
    paths: list[Path] = []
    for name in names:
        value = getattr(args, name, None)
        if not value:
            raise ContractError(f"{args.command} requires --{name.replace('_', '-')}")
        paths.append(Path(value))
    return paths


def _object(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ContractError(f"symlinked path is not allowed: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    return value


def _manifest(path: Path) -> dict[str, Any]:
    payload = _object(path)
    if set(payload) != {"version", "commit", "dirty"}:
        raise ContractError(f"build manifest has an unexpected shape: {path}")
    commit = payload["commit"]
    if (
        payload["version"] != "build-manifest/v1"
        or not isinstance(commit, str)
        or len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
        or payload["dirty"] is not False
    ):
        raise ContractError(f"build manifest is incomplete: {path}")
    return payload


def _private_write(path: Path, payload: dict[str, Any]) -> None:
    path = path.absolute()
    _reject_symlink_components(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ContractError(f"symlinked output root is not allowed: {current}")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    contract_path, baseline_path, candidate_path = _required(
        args, "contract", "baseline_manifest", "candidate_manifest"
    )
    contract = load_eval_contract(contract_path)
    baseline = _manifest(baseline_path)
    candidate = _manifest(candidate_path)
    return {
        "version": "dogfood-preflight/v1",
        "adapter_eligibility": "open",
        "contract": contract.version,
        "baseline_commit": baseline["commit"],
        "candidate_commit": candidate["commit"],
        "network_calls": 0,
    }


def _approval_packet(args: argparse.Namespace) -> dict[str, Any]:
    contract_path, baseline_path, candidate_path, output = _required(
        args, "contract", "baseline_manifest", "candidate_manifest", "output"
    )
    if args.scope not in {"pilot", "full"}:
        raise ContractError("approval-packet scope must be pilot or full")
    load_eval_contract(contract_path)
    baseline = _manifest(baseline_path)
    candidate = _manifest(candidate_path)
    packet = {
        "version": "approval-packet/v1",
        "scope": args.scope,
        "session_id": secrets.token_hex(16),
        "contract_sha256": _digest(contract_path / "dataset.json"),
        "baseline_commit": baseline["commit"],
        "candidate_commit": candidate["commit"],
        "adapter_eligibility": "open",
    }
    _private_write(output, packet)
    return packet


def _live(args: argparse.Namespace) -> dict[str, Any]:
    receipt_path, packet_path, _state_path = _required(
        args, "receipt", "packet", "state"
    )
    packet = _object(packet_path)
    expected_scope = (
        "invocation" if args.command == "authorize-invocation" else args.command
    )
    if packet.get("scope") != expected_scope:
        raise ContractError("packet scope does not match command")
    session_id = packet.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ContractError("packet does not contain a session identifier")
    consume_receipt(
        receipt_path,
        _digest(packet_path),
        session_id,
        expected_scope=expected_scope,
    )
    raise ContractError("adapter_eligibility is open; live model execution is disabled")


def _local(args: argparse.Namespace) -> dict[str, Any]:
    contract_path, manifest_path, output = _required(
        args, "contract", "build_manifest", "output"
    )
    contract = load_eval_contract(contract_path)
    manifest = _manifest(manifest_path)
    result = {
        "version": "dogfood-result/v1",
        "task_id": contract.tasks[0].task_id,
        "trial_id": contract.tasks[0].trial_ids[0],
        "build_commit": manifest["commit"],
        "provider_config_sha256": "0" * 64,
        "expected_terminal": contract.tasks[0].expected_terminal,
        "observed_terminal": "observed",
        "comparable": True,
        "stability_checks": {"synthetic": True},
        "test_passed": True,
        "rubric_scores": None,
        "elapsed_ms": 0,
        "usage": None,
        "cost_status": "not_measurable",
        "artifact_manifest": {},
        "artifact_checks": {"sanitized": True},
    }
    _private_write(output, result)
    validate_result(output)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "preflight":
            result = _preflight(args)
        elif args.command == "local":
            result = _local(args)
        elif args.command == "approval-packet":
            result = _approval_packet(args)
        elif args.command in LIVE_COMMANDS:
            result = _live(args)
        elif args.command == "grading-packet":
            raise ContractError("grading-packet requires completed blinded artifacts")
        else:
            raise ContractError("a dogfood eval command is required")
    except ContractError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            f"Error: dogfood eval I/O failed: {exc.__class__.__name__}", file=sys.stderr
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
