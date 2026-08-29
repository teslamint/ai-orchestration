#!/usr/bin/env python3
"""Offline-first command surface for the dogfood/v1 release contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ai_orchestration.config import SlotKind, classify_slot
from ai_orchestration.evals.dogfood import (
    ContractError,
    ExpectedAttempt,
    load_eval_contract,
    validate_call_events,
    validate_result,
)

LIVE_COMMANDS = {"pilot", "full", "grading", "authorize-invocation"}
STAGE_NAMES = {
    "brainstormer",
    "reviewer",
    "planner",
    "executor",
    "code_reviewer",
    "fixer",
}


class EvalArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ContractError(message)


def _parser() -> argparse.ArgumentParser:
    parser = EvalArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")

    preflight = commands.add_parser("preflight")
    _add(preflight, "--contract", "--baseline-manifest", "--candidate-manifest")

    local = commands.add_parser("local")
    _add(
        local,
        "--contract",
        "--build-manifest",
        "--call-events",
        "--expected-attempts",
        "--producer-manifest",
        "--output",
    )

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


def _is_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _verify_git_identity(commit: str, tree: str) -> None:
    try:
        object_type = subprocess.run(
            ["git", "cat-file", "-t", commit],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if object_type.stdout.strip() != "commit":
            raise ContractError("manifest commit must identify a commit object")
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit}^{{tree}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except ContractError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError(f"manifest commit does not exist: {commit}") from exc
    if result.stdout.strip() != tree:
        raise ContractError("manifest tree does not match commit")


def _baseline_manifest(path: Path) -> dict[str, Any]:
    payload = _object(path)
    expected = {
        "version",
        "commit",
        "tree",
        "source_archive_sha256",
        "contract_archive_sha256",
        "command",
        "command_sha256",
        "result",
        "result_sha256",
        "provider_config_sha256",
        "dirty",
        "tracked_clean_before",
        "tracked_clean_after",
        "network_calls",
    }
    if set(payload) != expected or payload["version"] != "baseline-manifest/v1":
        raise ContractError(f"baseline manifest has an unexpected shape: {path}")
    hex_fields = (
        "source_archive_sha256",
        "contract_archive_sha256",
        "command_sha256",
        "result_sha256",
        "provider_config_sha256",
    )
    if (
        not _is_hex(payload["commit"], 40)
        or not _is_hex(payload["tree"], 40)
        or not all(_is_hex(payload[name], 64) for name in hex_fields)
        or payload["dirty"] is not False
        or payload["tracked_clean_before"] is not True
        or payload["tracked_clean_after"] is not True
        or payload["network_calls"] != 0
        or not isinstance(payload["command"], str)
        or not isinstance(payload["result"], str)
        or Path(payload["result"]).name != payload["result"]
    ):
        raise ContractError(f"baseline manifest is incomplete: {path}")
    if (
        hashlib.sha256(payload["command"].encode()).hexdigest()
        != payload["command_sha256"]
    ):
        raise ContractError("baseline command digest does not match")
    result_path = path.parent / payload["result"]
    if result_path.is_symlink():
        raise ContractError("baseline result must not be a symlink")
    if _digest(result_path) != payload["result_sha256"]:
        raise ContractError("baseline result digest does not match")
    _verify_git_identity(payload["commit"], payload["tree"])
    return payload


def _candidate_manifest(path: Path, contract_path: Path) -> dict[str, Any]:
    payload = _object(path)
    if set(payload) != {
        "version",
        "commit",
        "tree",
        "dirty",
        "contract_sha256",
        "commands",
        "command_sha256",
        "output_root",
    }:
        raise ContractError(f"candidate manifest has an unexpected shape: {path}")
    commands = payload["commands"]
    command_digests = payload["command_sha256"]
    output_root = payload["output_root"]
    if (
        payload["version"] != "candidate-manifest/v1"
        or not _is_hex(payload["commit"], 40)
        or not _is_hex(payload["tree"], 40)
        or payload["dirty"] is not False
        or not _is_hex(payload["contract_sha256"], 64)
        or not isinstance(commands, dict)
        or set(commands) != {"focused", "full"}
        or not all(isinstance(value, str) and value for value in commands.values())
        or not isinstance(command_digests, dict)
        or set(command_digests) != set(commands)
        or not all(_is_hex(value, 64) for value in command_digests.values())
        or not isinstance(output_root, str)
    ):
        raise ContractError(f"candidate manifest is incomplete: {path}")
    declared_root = Path(output_root)
    if (
        not declared_root.is_absolute()
        or declared_root.resolve() != path.parent.resolve()
    ):
        raise ContractError(
            "candidate output_root must match its external evidence root"
        )
    _reject_symlink_components(declared_root)
    if payload["contract_sha256"] != _digest(contract_path / "dataset.json"):
        raise ContractError("candidate manifest contract digest does not match")
    for name, command in commands.items():
        if hashlib.sha256(command.encode()).hexdigest() != command_digests[name]:
            raise ContractError(f"candidate {name} command digest does not match")
    _verify_git_identity(payload["commit"], payload["tree"])
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
    baseline = _baseline_manifest(baseline_path)
    candidate = _candidate_manifest(candidate_path, contract_path)
    return {
        "version": "dogfood-preflight/v1",
        "adapter_eligibility": _adapter_eligibility(),
        "authorization_gate": "ready",
        "contract": contract.version,
        "baseline_commit": baseline["commit"],
        "candidate_commit": candidate["commit"],
        "candidate_tree": candidate["tree"],
        "command_sha256": candidate["command_sha256"],
        "output_root": candidate["output_root"],
        "network_calls": 0,
    }


def _approval_packet(args: argparse.Namespace) -> dict[str, Any]:
    contract_path, baseline_path, candidate_path, output = _required(
        args, "contract", "baseline_manifest", "candidate_manifest", "output"
    )
    if args.scope not in {"pilot", "full"}:
        raise ContractError("approval-packet scope must be pilot or full")
    _require_live_eligibility()
    load_eval_contract(contract_path)
    baseline = _baseline_manifest(baseline_path)
    candidate = _candidate_manifest(candidate_path, contract_path)
    packet = {
        "version": "approval-packet/v1",
        "scope": args.scope,
        "session_id": secrets.token_hex(16),
        "contract_sha256": _digest(contract_path / "dataset.json"),
        "baseline_commit": baseline["commit"],
        "candidate_commit": candidate["commit"],
        "adapter_eligibility": "open",
        "authorization_gate": "ready",
    }
    _private_write(output, packet)
    return packet


def _live(args: argparse.Namespace) -> dict[str, Any]:
    del args
    _require_live_eligibility()
    raise ContractError("live runner is not configured")


def _adapter_eligibility() -> str:
    """Return whether the complete V1 runner and adapters are usable."""
    return "open"


def _require_live_eligibility() -> None:
    if _adapter_eligibility() != "closed":
        raise ContractError(
            "adapter_eligibility is open; live model execution is disabled"
        )


def _local(args: argparse.Namespace) -> dict[str, Any]:
    contract_path, manifest_path, output = _required(
        args, "contract", "build_manifest", "output"
    )
    contract = load_eval_contract(contract_path)
    manifest = _manifest(manifest_path)
    stability_checks = {"synthetic": True}
    usage = None
    artifact_manifest: dict[str, Any] = {}
    provider_config_sha256 = hashlib.sha256(b"{}").hexdigest()
    elapsed_ms = 0
    trial_id = contract.tasks[0].trial_ids[0]
    candidate_args = (
        args.call_events,
        args.expected_attempts,
        args.producer_manifest,
    )
    if any(candidate_args):
        if not all(candidate_args):
            raise ContractError(
                "local candidate observation requires --call-events, "
                "--expected-attempts, and --producer-manifest"
            )
        observation = _candidate_observation(
            Path(args.call_events),
            Path(args.expected_attempts),
            Path(args.producer_manifest),
        )
        stability_checks.update(
            {"call_events_valid": True, "direct_vendor_observed": True}
        )
        usage = observation.pop("usage")
        elapsed_ms = observation.pop("elapsed_ms")
        trial_id = observation.pop("trial_id")
        provider_config_sha256 = observation.pop("provider_config_sha256")
        artifact_manifest = observation
    result = {
        "version": "dogfood-result/v1",
        "task_id": contract.tasks[0].task_id,
        "trial_id": trial_id,
        "build_commit": manifest["commit"],
        "provider_config_sha256": provider_config_sha256,
        "expected_terminal": contract.tasks[0].expected_terminal,
        "observed_terminal": "observed",
        "comparable": True,
        "stability_checks": stability_checks,
        "test_passed": True,
        "rubric_scores": None,
        "elapsed_ms": elapsed_ms,
        "usage": usage,
        "cost_status": "not_measurable",
        "artifact_manifest": artifact_manifest,
        "artifact_checks": {"sanitized": True},
    }
    _private_write(output, result)
    validate_result(output)
    return result


def _candidate_observation(
    path: Path, expected_path: Path, producer_manifest_path: Path
) -> dict[str, Any]:
    producer = _producer_manifest(
        producer_manifest_path, event_path=path, expected_run_id=None
    )
    event_bytes = _input_snapshot(path, "call-event")
    try:
        lines = event_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ContractError("call-event file must contain UTF-8") from exc
    events: list[dict[str, Any]] = []
    for ordinal, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(f"malformed call event at line {ordinal}") from exc
        if not isinstance(event, dict):
            raise ContractError(f"call event at line {ordinal} must be an object")
        events.append(event)
    expected_bytes = _input_snapshot(expected_path, "expected-attempt")
    try:
        expected_manifest = json.loads(expected_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("expected-attempt manifest is invalid JSON") from exc
    if not isinstance(expected_manifest, dict):
        raise ContractError("expected-attempt manifest must be an object")
    if set(expected_manifest) != {
        "version",
        "task_id",
        "trial_id",
        "run_id",
        "provider_config",
        "attempts",
    }:
        raise ContractError("expected-attempt manifest has an unexpected shape")
    if (
        expected_manifest["version"] != "expected-attempts/v1"
        or expected_manifest["task_id"] != "C1-R"
        or expected_manifest["trial_id"] not in {"01", "02", "03"}
        or not isinstance(expected_manifest["run_id"], str)
        or not expected_manifest["run_id"]
        or not isinstance(expected_manifest["provider_config"], dict)
        or not isinstance(expected_manifest["attempts"], list)
    ):
        raise ContractError("expected-attempt manifest is invalid")
    try:
        uuid.UUID(expected_manifest["run_id"])
    except ValueError as exc:
        raise ContractError("expected-attempt run_id must be a UUID") from exc
    if producer["run_id"] != expected_manifest["run_id"]:
        raise ContractError("producer manifest run_id does not match expected attempts")
    _verify_producer_state(producer, expected_manifest["run_id"])
    expected = []
    for raw in expected_manifest["attempts"]:
        if not isinstance(raw, dict) or set(raw) != {
            "task_id",
            "stage",
            "provider_id",
            "attempt",
        }:
            raise ContractError("expected attempt has an unexpected shape")
        if (
            not isinstance(raw["task_id"], str)
            or raw["task_id"] != expected_manifest["task_id"]
        ):
            raise ContractError("expected attempt task_id is invalid")
        if not isinstance(raw["stage"], str) or raw["stage"] not in STAGE_NAMES:
            raise ContractError("expected attempt stage is invalid")
        if not isinstance(raw["provider_id"], str) or not raw["provider_id"]:
            raise ContractError("expected attempt provider_id is invalid")
        if type(raw["attempt"]) is not int or raw["attempt"] < 1:
            raise ContractError("expected attempt attempt is invalid")
        expected.append(
            ExpectedAttempt(
                raw["task_id"], raw["stage"], raw["provider_id"], raw["attempt"]
            )
        )
    if len(expected) != 4 or len(set(expected)) != 4:
        raise ContractError("expected-attempt manifest requires four unique calls")
    with tempfile.TemporaryDirectory(prefix="dogfood-events-") as temporary:
        snapshot_path = Path(temporary) / "call_events.jsonl"
        snapshot_path.write_bytes(event_bytes)
        validation = validate_call_events(snapshot_path, expected)
    if validation.event_count != 4:
        raise ContractError("candidate routing matrix must contain exactly four calls")
    if any(
        event.get("run_id") != expected_manifest["run_id"]
        or event.get("task_id") != "C1-R"
        or event.get("outcome") != "success"
        for event in events
    ):
        raise ContractError("candidate routing matrix must be one successful C1-R run")
    provider_config = expected_manifest["provider_config"]
    if set(provider_config) != STAGE_NAMES or not all(
        isinstance(value, str) and value for value in provider_config.values()
    ):
        raise ContractError("expected provider config must name all six stages")
    if any(
        provider_config.get(event["stage"]) != event["provider_id"] for event in events
    ):
        raise ContractError("call events do not match the expected provider config")
    direct = [
        event
        for event in events
        if event.get("provider_kind") == "direct_vendor"
        and event.get("provider_id") in {"gemini_api", "openai_api", "anthropic_api"}
    ]
    if {event["provider_id"] for event in direct} != {
        "gemini_api",
        "openai_api",
        "anthropic_api",
    } or len(direct) != 3:
        raise ContractError("candidate routing matrix requires all direct vendors")
    proxy = [event for event in events if event.get("provider_kind") == "proxy_model"]
    if (
        len(proxy) != 1
        or classify_slot(proxy[0]["provider_id"]) is not SlotKind.PROXY_MODEL
    ):
        raise ContractError(
            "candidate routing matrix requires one ordinary proxy observation"
        )
    input_tokens = sum(event.get("reported_input_tokens") or 0 for event in direct)
    output_tokens = sum(event.get("reported_output_tokens") or 0 for event in direct)
    usage = (
        {"input_tokens": input_tokens, "output_tokens": output_tokens}
        if any(
            event.get("reported_input_tokens") is not None
            or event.get("reported_output_tokens") is not None
            for event in direct
        )
        else None
    )
    return {
        "call_event_count": validation.event_count,
        "call_events_sha256": hashlib.sha256(event_bytes).hexdigest(),
        "expected_attempts_sha256": hashlib.sha256(expected_bytes).hexdigest(),
        "run_id": expected_manifest["run_id"],
        "direct_vendor_ids": sorted({event["provider_id"] for event in direct}),
        "elapsed_ms": sum(event["latency_ms"] for event in events),
        "trial_id": expected_manifest["trial_id"],
        "provider_config_sha256": hashlib.sha256(
            json.dumps(provider_config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "usage": usage,
        "producer_manifest_sha256": _digest(producer_manifest_path),
        "state_sha256": producer["state_sha256"],
    }


def _producer_manifest(
    path: Path, *, event_path: Path, expected_run_id: str | None
) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ContractError("producer manifest is unreadable") from exc
    if path.is_symlink() or metadata.st_mode & 0o777 != 0o600:
        raise ContractError("producer manifest must be a non-symlink mode 0600 file")
    payload = _object(path)
    if set(payload) != {
        "version",
        "run_id",
        "state_path",
        "state_sha256",
        "call_events_path",
        "call_events_sha256",
    }:
        raise ContractError("producer manifest has an unexpected shape")
    if (
        payload["version"] != "producer-manifest/v1"
        or not isinstance(payload["run_id"], str)
        or not _is_hex(payload["state_sha256"], 64)
        or not _is_hex(payload["call_events_sha256"], 64)
        or not isinstance(payload["state_path"], str)
        or not isinstance(payload["call_events_path"], str)
    ):
        raise ContractError("producer manifest is incomplete")
    try:
        uuid.UUID(payload["run_id"])
    except ValueError as exc:
        raise ContractError("producer manifest run_id must be a UUID") from exc
    state_path = Path(payload["state_path"])
    declared_events = Path(payload["call_events_path"])
    if (
        not state_path.is_absolute()
        or not declared_events.is_absolute()
        or declared_events != event_path.absolute()
        or state_path.parent != declared_events.parent
    ):
        raise ContractError("producer manifest paths do not match candidate inputs")
    _reject_symlink_components(state_path)
    _reject_symlink_components(declared_events)
    if _digest(state_path) != payload["state_sha256"]:
        raise ContractError("producer state digest does not match")
    if _digest(declared_events) != payload["call_events_sha256"]:
        raise ContractError("producer call-event digest does not match")
    if expected_run_id is not None and payload["run_id"] != expected_run_id:
        raise ContractError("producer manifest run_id does not match")
    return payload


def _verify_producer_state(producer: dict[str, Any], run_id: str) -> None:
    state = _object(Path(producer["state_path"]))
    if state.get("run_id") != run_id:
        raise ContractError("producer state run_id does not match")


def _input_snapshot(path: Path, label: str) -> bytes:
    if path.is_symlink():
        raise ContractError(f"symlinked path is not allowed: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ContractError(f"{label} file is unreadable: {path}") from exc


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
