#!/usr/bin/env python3
"""Offline-first command surface for the dogfood/v1 release contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ai_orchestration.config import SlotKind, classify_slot
from ai_orchestration.evals.dogfood import (
    PRODUCER_ATTESTATION_KEY_ENV,
    ContractError,
    ExpectedAttempt,
    TrialResult,
    compare_builds,
    consume_receipt,
    grade_artifact,
    load_eval_contract,
    producer_manifest_signature,
    receipt_signature,
    validate_call_events,
    validate_result,
)
from ai_orchestration.providers.call_control import (
    OutboundPayload,
    SocketAuthorizationGate,
)

LIVE_COMMANDS = {"pilot", "full", "grading", "authorize-invocation"}
LIVE_BINARY_ENV = "AI_ORCHESTRATION_LIVE_BINARY"
LIVE_MODEL_ENV = "AI_ORCHESTRATION_LIVE_MODEL"
LIVE_TIMEOUT_ENV = "AI_ORCHESTRATION_LIVE_TIMEOUT_SECONDS"
LIVE_APPROVAL_TIMEOUT_ENV = "AI_ORCHESTRATION_LIVE_APPROVAL_TIMEOUT_SECONDS"
LIVE_BINARY_NAMES = {"agy", "claude", "codex"}
LIVE_TIMEOUT_SECONDS = 300.0
LIVE_APPROVAL_TIMEOUT_SECONDS = 7200.0
LIVE_RETRY_LIMIT = 0
LIVE_TOKEN_CAP = 4096
MAX_CHILD_DIAGNOSTIC_BYTES = 512
MAX_CHILD_CAPTURE_BYTES = 64 * 1024
LIVE_TASK_GOAL = (
    "Create a minimal runnable Python artifact named hello.py that prints exactly "
    "HELLO_DOGFOOD. Use no network, no secrets, and no files outside the assigned "
    "workspace. During executor file-generation steps, return only the requested "
    "file content. Do not include Markdown fences or explanations."
)
LIVE_EXPECTED_STAGES = ("brainstormer", "reviewer", "planner", "executor")
LIVE_ENVIRONMENT_NAMES = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "VIRTUAL_ENV",
)
AUTHORIZATION_REQUEST_FIELDS = {
    "version",
    "request_id",
    "transport",
    "payload_path",
    "payload_sha256",
    "destination",
    "timeout_seconds",
    "retry_limit",
}
LEGACY_AUTHORIZATION_REQUEST_FIELDS = {
    "version",
    "request_id",
    "transport",
    "payload_path",
    "payload_sha256",
}
AUTHORIZATION_RESPONSE_FIELDS = {
    "version",
    "request_id",
    "payload_sha256",
    "authorized",
}
SECRET_PATTERN = re.compile(
    rb"(?i)(?:bearer\s+|(?:sk|api|token)[_-])[A-Za-z0-9._-]{16,}"
)
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
        "--task-id",
        "--trial-id",
        "--evidence",
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

    issue_receipt = commands.add_parser("issue-receipt")
    _add(issue_receipt, "--packet", "--scope", "--session-id", "--output")

    grading_packet = commands.add_parser("grading-packet")
    _add(grading_packet, "--state", "--artifact-manifest", "--output")

    collect = commands.add_parser("collect-artifacts")
    collect.add_argument("--result-root", action="append", required=True)
    _add(collect, "--output")

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
    _verify_commit_object(commit)
    _verify_current_commit(commit)
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


def _verify_commit_object(commit: str) -> None:
    try:
        result = subprocess.run(
            ["git", "cat-file", "-t", commit],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError(f"manifest commit does not exist: {commit}") from exc
    if result.stdout.strip() != "commit":
        raise ContractError("manifest commit must identify a commit object")


def _verify_current_commit(commit: str) -> None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError(
            "could not resolve current HEAD for build manifest"
        ) from exc
    if result.stdout.strip() != commit:
        raise ContractError("build manifest commit does not match current HEAD")


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


def _child_failure_diagnostic(stdout: bytes, stderr: bytes) -> str:
    """Return a bounded, secret-redacted child failure summary."""
    captured = b"\n".join(part[-MAX_CHILD_CAPTURE_BYTES:] for part in (stdout, stderr))
    sanitized = SECRET_PATTERN.sub(b"<redacted>", captured)
    text = sanitized.decode("utf-8", errors="replace")
    error_lines = [
        line.strip() for line in text.splitlines() if line.strip().startswith("Error:")
    ]
    if error_lines:
        return error_lines[-1][:MAX_CHILD_DIAGNOSTIC_BYTES]
    if captured:
        return (
            f"child output had no structured error "
            f"(bytes={len(captured)}, sha256={hashlib.sha256(captured).hexdigest()})"
        )[:MAX_CHILD_DIAGNOSTIC_BYTES]
    return "child output was empty"


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    contract_path, baseline_path, candidate_path = _required(
        args, "contract", "baseline_manifest", "candidate_manifest"
    )
    contract = load_eval_contract(contract_path)
    baseline = _baseline_manifest(baseline_path)
    candidate = _candidate_manifest(candidate_path, contract_path)
    profile = _live_profile()
    return {
        "version": "dogfood-preflight/v1",
        "adapter_eligibility": profile["eligibility"],
        "authorization_gate": "ready",
        "contract": contract.version,
        "baseline_commit": baseline["commit"],
        "candidate_commit": candidate["commit"],
        "candidate_tree": candidate["tree"],
        "command_sha256": candidate["command_sha256"],
        "output_root": candidate["output_root"],
        "network_calls": 0,
        "live_profile": profile,
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
    profile = _live_profile()
    output_root = output.absolute().parent
    packet = {
        "version": "approval-packet/v1",
        "scope": args.scope,
        "session_id": secrets.token_hex(16),
        "contract_sha256": _digest(contract_path / "dataset.json"),
        "baseline_commit": baseline["commit"],
        "candidate_commit": candidate["commit"],
        "candidate_command_sha256": candidate["command_sha256"],
        "adapter_eligibility": profile["eligibility"],
        "authorization_gate": "ready",
        "adapter": {
            "provider_kind": "cli_binary",
            "binary_name": profile["binary_name"],
            "binary_path": profile["binary_path"],
            "model": profile["model"],
            "auth_probe": "read-only",
        },
        "schedule": _live_schedule(args.scope),
        "limits": {
            "max_routing_invocations_per_trial": len(LIVE_EXPECTED_STAGES),
            "timeout_seconds": profile["timeout_seconds"],
            "approval_timeout_seconds": profile["approval_timeout_seconds"],
            "retry_limit": LIVE_RETRY_LIMIT,
            "token_cap_per_invocation": LIVE_TOKEN_CAP,
            "cost_status": "not_measurable",
            "cost_cap": None,
        },
        "environment_names": list(LIVE_ENVIRONMENT_NAMES),
        "output_root": str(output_root),
    }
    _private_write(output, packet)
    return packet


def _live(args: argparse.Namespace) -> dict[str, Any]:
    _require_live_eligibility()
    packet_path, receipt_path, state_path = _required(
        args, "packet", "receipt", "state"
    )
    packet = _approval_packet_object(packet_path)
    if state_path.absolute().parent != Path(packet["output_root"]).absolute():
        raise ContractError("live state must be inside the approval packet output root")
    reservation = consume_receipt(
        receipt_path,
        _digest(packet_path),
        packet["session_id"],
        expected_scope=packet["scope"],
    )
    profile = _live_profile()
    state = _load_live_state(state_path, packet, reservation)
    state["packet_sha256"] = _digest(packet_path)
    socket_path = _new_socket_path()
    next_packet_path = state_path.absolute().parent / "next-invocation-packet.json"
    payload_path = state_path.absolute().parent / ".pending-payload.bin"
    for path in (socket_path, next_packet_path, payload_path):
        _reject_symlink_components(path.absolute())
    state_path.absolute().parent.mkdir(parents=True, exist_ok=True)
    if next_packet_path.exists():
        raise ContractError(
            f"live invocation packet already exists: {next_packet_path}"
        )
    if payload_path.exists():
        raise ContractError(
            f"live authorization payload already exists: {payload_path}"
        )

    broker = _AuthorizationBroker(
        socket_path=socket_path,
        next_packet_path=next_packet_path,
        payload_path=payload_path,
        state_path=state_path,
        session_id=packet["session_id"],
        model=profile["model"],
        timeout=packet["limits"].get(
            "approval_timeout_seconds", profile["approval_timeout_seconds"]
        ),
    )
    completed = set(state.get("completed_trials", []))
    try:
        broker.start()
        state.update(
            {
                "status": "running",
                "socket_path": str(socket_path.absolute()),
                "next_packet_path": str(next_packet_path.absolute()),
                "owned_pids": [],
                "cleanup": {"socket_removed": False, "payload_absent": False},
            }
        )
        _private_write(state_path, state)
        for trial in packet["schedule"]:
            trial_key = f"{trial['build']}:{trial['task_id']}:{trial['trial_id']}"
            if trial_key in completed:
                continue
            result_path, pid = _run_live_trial(
                packet=packet,
                trial=trial,
                profile=profile,
                state=state,
                state_path=state_path,
                broker=broker,
            )
            completed.add(trial_key)
            state["completed_trials"] = sorted(completed)
            state["last_result_path"] = str(result_path.absolute())
            state["owned_pids"] = []
            _private_write(state_path, state)
            del pid
        state.update(
            {
                "status": "completed",
                "cleanup": {"socket_removed": False, "payload_absent": False},
            }
        )
        _private_write(state_path, state)
        return state
    except BaseException as exc:
        state.update(
            {
                "status": "failed",
                "error_type": exc.__class__.__name__,
                "owned_pids": broker.owned_pids(),
            }
        )
        _private_write(state_path, state)
        raise
    finally:
        broker.stop()
        if payload_path.exists():
            payload_path.unlink(missing_ok=True)
        if next_packet_path.exists():
            next_packet_path.unlink(missing_ok=True)
        socket_path.unlink(missing_ok=True)
        if state_path.exists() and not state_path.is_symlink():
            final_state = _object(state_path)
            final_state["owned_pids"] = []
            final_state["cleanup"] = {
                "socket_removed": not socket_path.exists(),
                "payload_absent": not payload_path.exists(),
            }
            if final_state.get("status") == "running":
                final_state["status"] = "interrupted"
            _private_write(state_path, final_state)


def _live_profile() -> dict[str, Any]:
    binary_name = os.environ.get(LIVE_BINARY_ENV, "").strip()
    timeout = _live_timeout()
    approval_timeout = _live_approval_timeout()
    base = {
        "eligibility": "open",
        "binary_name": binary_name or None,
        "binary_path": None,
        "model": os.environ.get(LIVE_MODEL_ENV, "cli-configured-default"),
        "timeout_seconds": timeout,
        "approval_timeout_seconds": approval_timeout,
        "reason": "explicit authenticated CLI profile is required",
    }
    if not binary_name:
        return base
    if binary_name not in LIVE_BINARY_NAMES:
        base["reason"] = "live binary is not an approved CLI provider"
        return base
    binary_path = shutil.which(binary_name)
    if binary_path is None:
        base["reason"] = "configured live binary is not on PATH"
        return base
    base["binary_path"] = binary_path
    if not _cli_auth_status(binary_name, binary_path):
        base["reason"] = "configured CLI authentication status is not logged in"
        return base
    base.update(
        {
            "eligibility": "closed",
            "reason": "read-only CLI authentication status is logged in",
        }
    )
    return base


def _live_timeout() -> float:
    raw = os.environ.get(LIVE_TIMEOUT_ENV, str(LIVE_TIMEOUT_SECONDS))
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ContractError(f"{LIVE_TIMEOUT_ENV} must be numeric") from exc
    if timeout <= 0 or timeout > 1800:
        raise ContractError(f"{LIVE_TIMEOUT_ENV} must be between 0 and 1800 seconds")
    return timeout


def _live_approval_timeout() -> float:
    raw = os.environ.get(LIVE_APPROVAL_TIMEOUT_ENV, str(LIVE_APPROVAL_TIMEOUT_SECONDS))
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ContractError(f"{LIVE_APPROVAL_TIMEOUT_ENV} must be numeric") from exc
    if timeout <= 0 or timeout > 86_400:
        raise ContractError(
            f"{LIVE_APPROVAL_TIMEOUT_ENV} must be between 0 and 86400 seconds"
        )
    return timeout


def _cli_auth_status(binary_name: str, binary_path: str) -> bool:
    command = {
        "claude": [binary_path, "auth", "status"],
        "codex": [binary_path, "login", "status"],
    }.get(binary_name)
    if command is None:
        return False
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    if binary_name == "claude":
        try:
            status = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        return isinstance(status, dict) and status.get("loggedIn") is True
    return "logged in" in result.stdout.lower()


def _live_schedule(scope: str) -> list[dict[str, str]]:
    trial_ids = ("01",) if scope == "pilot" else ("02", "03")
    return [
        {"build": build, "task_id": "C1-E", "trial_id": trial_id}
        for trial_id in trial_ids
        for build in ("baseline", "candidate")
    ]


def _build_live_command(
    *,
    source_root: Path,
    workspace_root: Path,
    project_name: str,
    binary_path: str,
    resume: bool = False,
) -> list[str]:
    del source_root
    binary_name = Path(binary_path).name
    command = [
        sys.executable,
        "-m",
        "ai_orchestration.cli",
        LIVE_TASK_GOAL,
        "--workspace",
        str(workspace_root.absolute()),
        "--project-name",
        project_name,
        "--auto-select",
        "--auto-run",
        "--auto-approve",
        "--skip-review",
    ]
    if resume:
        command.append("--resume")
    for option in (
        "brainstormer",
        "reviewer",
        "planner",
        "executor",
        "code-reviewer",
        "fixer",
    ):
        command.extend([f"--{option}", binary_name])
    return command


def _is_resumable_trial(trial_root: Path) -> bool:
    """Accept only a clean, planner-complete partial trial for resume."""
    if (trial_root / "result.json").exists():
        return False
    workspace_root = trial_root / "workspace"
    if not workspace_root.is_dir() or workspace_root.is_symlink():
        return False
    projects = [
        path
        for path in workspace_root.iterdir()
        if path.is_dir() and not path.is_symlink()
    ]
    if len(projects) != 1:
        return False
    metadata = projects[0] / ".ai_orchestration"
    state_path = metadata / "run_state.json"
    events_path = metadata / "call_events.jsonl"
    if (
        not metadata.is_dir()
        or metadata.is_symlink()
        or not state_path.is_file()
        or state_path.is_symlink()
        or not events_path.is_file()
        or events_path.is_symlink()
    ):
        return False
    try:
        state = _object(state_path)
        completed = state.get("completed_stages")
        required = {"brainstormer", "brainstorming_reviewer", "planner"}
        allowed = required | {"executor"}
        if (
            not isinstance(completed, list)
            or not required.issubset(completed)
            or not set(completed).issubset(allowed)
        ):
            return False
        for line in events_path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if not isinstance(event, dict) or event.get("outcome") != "success":
                return False
    except (ContractError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return True


def _write_runner_observer_manifest(
    path: Path, *, state_path: Path, event_path: Path, producer_key: str
) -> None:
    """Bind a baseline snapshot when the older child cannot self-attest."""
    state_bytes = _input_snapshot(state_path, "producer state")
    event_bytes = _input_snapshot(event_path, "call-event")
    state = json.loads(state_bytes)
    if not isinstance(state, dict) or not isinstance(state.get("run_id"), str):
        raise ContractError("producer state is invalid JSON")
    manifest = {
        "version": "producer-manifest/v1",
        "run_id": state["run_id"],
        "state_path": str(state_path.absolute()),
        "state_sha256": hashlib.sha256(state_bytes).hexdigest(),
        "call_events_path": str(event_path.absolute()),
        "call_events_sha256": hashlib.sha256(event_bytes).hexdigest(),
    }
    manifest["producer_signature"] = producer_manifest_signature(manifest, producer_key)
    _private_write(path, manifest)


def _invocation_packet(
    request: dict[str, Any],
    *,
    session_id: str,
    socket_path: Path,
    model: str = "cli-configured-default",
) -> dict[str, Any]:
    if set(request) not in (
        AUTHORIZATION_REQUEST_FIELDS,
        LEGACY_AUTHORIZATION_REQUEST_FIELDS,
    ):
        raise ContractError("authorization request has an unexpected shape")
    if request["version"] != "authorization-request/v1":
        raise ContractError("authorization request version is unsupported")
    if request["transport"] not in {"http_json", "cli_argv"}:
        raise ContractError("authorization request transport is invalid")
    payload_path = Path(request["payload_path"])
    if not payload_path.is_absolute():
        raise ContractError("authorization payload path must be absolute")
    if not _is_hex(request["payload_sha256"], 64):
        raise ContractError("authorization payload digest is invalid")
    payload = _input_snapshot(payload_path, "authorization payload")
    if hashlib.sha256(payload).hexdigest() != request["payload_sha256"]:
        raise ContractError("authorization payload digest does not match")
    if _contains_secret(payload):
        raise ContractError(
            "authorization payload contains a credential or secret pattern"
        )
    return {
        "version": "next-invocation-packet/v1",
        "session_id": session_id,
        "scope": "invocation",
        "request_id": request["request_id"],
        "transport": request["transport"],
        "payload_path": str(payload_path.absolute()),
        "payload_sha256": request["payload_sha256"],
        "payload_bytes": len(payload),
        "destination": request.get("destination"),
        "model": model,
        "timeout_seconds": request.get("timeout_seconds"),
        "retry_limit": request.get("retry_limit"),
        "socket_path": str(socket_path.absolute()),
    }


def _contains_secret(payload: bytes) -> bool:
    for name in (
        "CLIPROXYAPI_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_AI_API_KEY",
    ):
        value = os.environ.get(name)
        if value and len(value) >= 8 and value.encode() in payload:
            return True
    return SECRET_PATTERN.search(payload) is not None


def _approval_packet_object(path: Path) -> dict[str, Any]:
    payload = _object(path)
    required = {
        "version",
        "scope",
        "session_id",
        "contract_sha256",
        "baseline_commit",
        "candidate_commit",
        "candidate_command_sha256",
        "adapter_eligibility",
        "authorization_gate",
        "adapter",
        "schedule",
        "limits",
        "environment_names",
        "output_root",
    }
    if set(payload) != required or payload["version"] != "approval-packet/v1":
        raise ContractError("approval packet has an unexpected shape")
    if payload["scope"] not in {"pilot", "full"}:
        raise ContractError("approval packet scope is invalid")
    if not isinstance(payload["session_id"], str) or not payload["session_id"]:
        raise ContractError("approval packet session is invalid")
    if (
        payload["adapter_eligibility"] != "closed"
        or payload["authorization_gate"] != "ready"
    ):
        raise ContractError("approval packet is not eligible for live execution")
    limits = payload["limits"]
    approval_timeout = (
        limits.get("approval_timeout_seconds") if isinstance(limits, dict) else None
    )
    if approval_timeout is not None and (
        isinstance(approval_timeout, bool)
        or not isinstance(approval_timeout, (int, float))
        or approval_timeout <= 0
        or approval_timeout > 86_400
    ):
        raise ContractError("approval packet approval timeout is invalid")
    if not isinstance(payload["schedule"], list) or payload[
        "schedule"
    ] != _live_schedule(payload["scope"]):
        raise ContractError("approval packet schedule is invalid")
    if (
        not isinstance(payload["output_root"], str)
        or not Path(payload["output_root"]).is_absolute()
    ):
        raise ContractError("approval packet output root is invalid")
    return payload


def _load_live_state(
    state_path: Path, packet: dict[str, Any], reservation: Any
) -> dict[str, Any]:
    if state_path.exists():
        state = _object(state_path)
        if state.get("version") != "live-eval-state/v1":
            raise ContractError("live eval state version is unsupported")
        completed = state.get("completed_trials", [])
        if not isinstance(completed, list) or not all(
            isinstance(value, str) for value in completed
        ):
            raise ContractError("live eval completed trial state is invalid")
    else:
        state = {"version": "live-eval-state/v1", "completed_trials": []}
    state.pop("last_error", None)
    state.pop("error_type", None)
    state.update(
        {
            "session_id": packet["session_id"],
            "scope": packet["scope"],
            "packet_sha256": _digest_from_object(packet),
            "batch_receipt_reservation": str(reservation.reservation_path.absolute()),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return state


def _digest_from_object(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _new_socket_path() -> Path:
    root = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
    return root / f"ao-dogfood-{secrets.token_hex(8)}.sock"


class _AuthorizationBroker:
    """Relay one packet-bound receipt to the waiting provider socket."""

    def __init__(
        self,
        *,
        socket_path: Path,
        next_packet_path: Path,
        payload_path: Path,
        state_path: Path,
        session_id: str,
        model: str,
        timeout: float,
    ) -> None:
        self.socket_path = socket_path.absolute()
        self.next_packet_path = next_packet_path.absolute()
        self.payload_path = payload_path.absolute()
        self.state_path = state_path.absolute()
        self.session_id = session_id
        self.model = model
        self.timeout = timeout
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pending: dict[
            str, tuple[socket.socket, threading.Event, dict[str, Any]]
        ] = {}
        self._pids: set[int] = set()

    def start(self) -> None:
        _reject_symlink_components(self.socket_path)
        if self.socket_path.exists():
            raise ContractError(
                f"authorization socket already exists: {self.socket_path}"
            )
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            listener.listen(8)
            listener.settimeout(0.5)
        except BaseException:
            listener.close()
            self.socket_path.unlink(missing_ok=True)
            raise
        self._listener = listener
        self._thread = threading.Thread(
            target=self._serve, name="dogfood-authorization-broker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        listener = self._listener
        if listener is not None:
            listener.close()
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for connection, event, _response in pending:
            event.set()
            connection.close()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._listener = None

    def owned_pids(self) -> list[int]:
        with self._lock:
            return sorted(self._pids)

    def register_pid(self, pid: int) -> None:
        with self._lock:
            self._pids.add(pid)

    def unregister_pid(self, pid: int) -> None:
        with self._lock:
            self._pids.discard(pid)

    def _serve(self) -> None:
        while not self._stop.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            thread = threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                name="dogfood-authorization-request",
                daemon=True,
            )
            thread.start()

    def _handle_connection(self, connection: socket.socket) -> None:
        try:
            encoded = _receive_line(connection, max_bytes=16_384)
            try:
                message = json.loads(encoded)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if not isinstance(message, dict):
                return
            if message.get("version") == "authorization-request/v1":
                self._handle_request(connection, message)
            elif message.get("version") == "authorization-relay/v1":
                self._handle_relay(connection, message)
        finally:
            if (
                connection.fileno() != -1
                and connection not in self._pending_connections()
            ):
                connection.close()

    def _pending_connections(self) -> list[socket.socket]:
        with self._lock:
            return [entry[0] for entry in self._pending.values()]

    def _handle_request(
        self, connection: socket.socket, request: dict[str, Any]
    ) -> None:
        request_id = request.get("request_id")
        digest = request.get("payload_sha256")
        try:
            packet = _invocation_packet(
                request,
                session_id=self.session_id,
                socket_path=self.socket_path,
                model=self.model,
            )
            if Path(packet["payload_path"]) != self.payload_path:
                raise ContractError("authorization payload path is not runner-owned")
            _private_write(self.next_packet_path, packet)
            event = threading.Event()
            response_box: dict[str, Any] = {}
            if not isinstance(request_id, str):
                raise ContractError("authorization request id is invalid")
            with self._lock:
                if request_id in self._pending:
                    raise ContractError("authorization request id was reused")
                self._pending[request_id] = (connection, event, response_box)
            state = _object(self.state_path)
            state.update(
                {
                    "status": "awaiting_invocation_approval",
                    "current_request_id": request_id,
                    "current_packet_path": str(self.next_packet_path),
                    "current_packet_sha256": _digest(self.next_packet_path),
                }
            )
            _private_write(self.state_path, state)
            print(
                json.dumps(
                    {
                        "status": "awaiting_invocation_approval",
                        "packet": str(self.next_packet_path),
                        "payload": packet["payload_path"],
                        "payload_sha256": packet["payload_sha256"],
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            event.wait(self.timeout + 10)
            with self._lock:
                response = response_box.get("response")
                self._pending.pop(request_id, None)
            if response is None:
                response = {
                    "version": "authorization-response/v1",
                    "request_id": request_id,
                    "payload_sha256": digest,
                    "authorized": False,
                }
            connection.sendall(_encode_line(response))
            state = _object(self.state_path)
            state.update(
                {
                    "status": "running",
                    "current_request_id": None,
                    "current_packet_path": None,
                    "current_packet_sha256": None,
                }
            )
            _private_write(self.state_path, state)
        except (ContractError, OSError) as exc:
            if isinstance(request_id, str):
                response = {
                    "version": "authorization-response/v1",
                    "request_id": request_id,
                    "payload_sha256": digest,
                    "authorized": False,
                }
                try:
                    connection.sendall(_encode_line(response))
                except OSError:
                    pass
            try:
                state = _object(self.state_path)
                state.update(
                    {
                        "status": "invocation_rejected",
                        "current_request_id": None,
                        "current_packet_path": None,
                        "current_packet_sha256": None,
                        "last_error": exc.__class__.__name__,
                    }
                )
                _private_write(self.state_path, state)
            except (ContractError, OSError):
                pass
        finally:
            if isinstance(request_id, str):
                with self._lock:
                    self._pending.pop(request_id, None)
            connection.close()

    def _handle_relay(self, connection: socket.socket, relay: dict[str, Any]) -> None:
        try:
            if set(relay) != {"version", "request_id", "response"}:
                raise ContractError("authorization relay has an unexpected shape")
            response = relay["response"]
            if (
                not isinstance(response, dict)
                or set(response) != AUTHORIZATION_RESPONSE_FIELDS
            ):
                raise ContractError(
                    "authorization relay response has an unexpected shape"
                )
            if response["version"] != "authorization-response/v1":
                raise ContractError(
                    "authorization relay response version is unsupported"
                )
            request_id = relay["request_id"]
            if not isinstance(request_id, str):
                raise ContractError("authorization relay request id is invalid")
            if response["request_id"] != request_id:
                raise ContractError(
                    "authorization relay request id does not match response"
                )
            with self._lock:
                pending = self._pending.get(request_id)
                if pending is None:
                    raise ContractError("authorization request is no longer pending")
                pending[2]["response"] = response
                pending[1].set()
            connection.sendall(
                _encode_line(
                    {
                        "version": "authorization-relay-response/v1",
                        "request_id": request_id,
                        "accepted": True,
                    }
                )
            )
        except (ContractError, OSError):
            try:
                connection.sendall(
                    _encode_line(
                        {
                            "version": "authorization-relay-response/v1",
                            "request_id": relay.get("request_id"),
                            "accepted": False,
                        }
                    )
                )
            except OSError:
                pass


def _receive_line(connection: socket.socket, *, max_bytes: int) -> bytes:
    data = bytearray()
    while b"\n" not in data:
        chunk = connection.recv(min(4096, max_bytes + 1 - len(data)))
        if not chunk:
            raise OSError("authorization socket closed before packet")
        data.extend(chunk)
        if len(data) > max_bytes:
            raise OSError("authorization packet exceeds the size limit")
    line, separator, trailing = bytes(data).partition(b"\n")
    if separator != b"\n" or trailing:
        raise OSError("authorization packet must contain exactly one line")
    return line


def _encode_line(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )


def _authorize_invocation(args: argparse.Namespace) -> dict[str, Any]:
    packet_path, receipt_path, state_path = _required(
        args, "packet", "receipt", "state"
    )
    packet = _object(packet_path)
    state = _object(state_path)
    expected = {
        "version",
        "session_id",
        "scope",
        "request_id",
        "transport",
        "payload_path",
        "payload_sha256",
        "payload_bytes",
        "destination",
        "model",
        "timeout_seconds",
        "retry_limit",
        "socket_path",
    }
    if set(packet) != expected or packet["version"] != "next-invocation-packet/v1":
        raise ContractError("next invocation packet has an unexpected shape")
    if packet["scope"] != "invocation":
        raise ContractError("next invocation packet scope is invalid")
    if state.get("session_id") != packet["session_id"]:
        raise ContractError("invocation packet session does not match live state")
    if state.get("current_packet_path") != str(packet_path.absolute()):
        raise ContractError("invocation packet is not the current pending packet")
    if state.get("current_packet_sha256") != _digest(packet_path):
        raise ContractError("invocation packet digest does not match live state")
    reservation = consume_receipt(
        receipt_path,
        _digest(packet_path),
        packet["session_id"],
        expected_scope="invocation",
    )
    response = {
        "version": "authorization-response/v1",
        "request_id": packet["request_id"],
        "payload_sha256": packet["payload_sha256"],
        "authorized": True,
    }
    relay = {
        "version": "authorization-relay/v1",
        "request_id": packet["request_id"],
        "response": response,
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(15)
            connection.connect(packet["socket_path"])
            connection.sendall(_encode_line(relay))
            ack = json.loads(_receive_line(connection, max_bytes=4096))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        state["status"] = "authorization_delivery_failed"
        state["last_error"] = exc.__class__.__name__
        _private_write(state_path, state)
        raise ContractError(
            "invocation authorization could not reach the live runner"
        ) from exc
    if not isinstance(ack, dict) or ack.get("accepted") is not True:
        raise ContractError("live runner rejected invocation authorization")
    state.update(
        {
            "last_invocation_receipt_reservation": str(
                reservation.reservation_path.absolute()
            ),
            "status": "invocation_authorized",
        }
    )
    _private_write(state_path, state)
    return {
        "version": "invocation-authorization/v1",
        "request_id": packet["request_id"],
        "payload_sha256": packet["payload_sha256"],
        "receipt_reservation": str(reservation.reservation_path.absolute()),
    }


def _issue_receipt(args: argparse.Namespace) -> dict[str, Any]:
    packet_path, output = _required(args, "packet", "output")
    scope = getattr(args, "scope", None)
    session_id = getattr(args, "session_id", None)
    if scope not in {"pilot", "full", "grading", "invocation"}:
        raise ContractError("receipt scope is invalid")
    if not isinstance(session_id, str) or not session_id:
        raise ContractError("receipt session id is required")
    packet = _object(packet_path)
    if packet.get("session_id") != session_id:
        raise ContractError("receipt session id does not match packet")
    packet_version = packet.get("version")
    if scope in {"pilot", "full"} and packet_version != "approval-packet/v1":
        raise ContractError("batch receipt requires an approval packet")
    if scope == "invocation" and packet_version != "next-invocation-packet/v1":
        raise ContractError("invocation receipt requires a next invocation packet")
    issued = datetime.now(timezone.utc)
    receipt: dict[str, Any] = {
        "version": "approval-receipt/v1",
        "nonce": secrets.token_hex(16),
        "issuer": "user",
        "scope": scope,
        "packet_sha256": _digest(packet_path),
        "session_id": session_id,
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(minutes=30)).isoformat(),
        "consumed_at": None,
    }
    approval_key = os.environ.get("AI_ORCHESTRATION_USER_APPROVAL_KEY")
    receipt["approval_signature"] = receipt_signature(receipt, approval_key or "")
    _private_write(output, receipt)
    return {
        "version": "receipt-issued/v1",
        "scope": scope,
        "session_id": session_id,
        "packet_sha256": receipt["packet_sha256"],
        "output": str(output.absolute()),
    }


def _run_live_trial(
    *,
    packet: dict[str, Any],
    trial: dict[str, str],
    profile: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    broker: _AuthorizationBroker,
) -> tuple[Path, int]:
    trial_root = (
        state_path.absolute().parent
        / "trials"
        / f"{trial['build']}-{trial['task_id']}-{trial['trial_id']}"
    )
    workspace_root = trial_root / "workspace"
    resuming = trial_root.exists()
    if resuming and not _is_resumable_trial(trial_root):
        raise ContractError(f"live trial output already exists: {trial_root}")
    _reject_symlink_components(trial_root)
    if not resuming:
        workspace_root.mkdir(parents=True, exist_ok=False)
    source_parent = Path(tempfile.mkdtemp(prefix="dogfood-live-source-"))
    source_root = source_parent / "source"
    commit = packet[f"{trial['build']}_commit"]
    _add_detached_worktree(source_root, commit)
    producer_key = secrets.token_urlsafe(32)
    command = _build_live_command(
        source_root=source_root,
        workspace_root=workspace_root,
        project_name=f"{trial['build']}_{trial['task_id'].lower().replace('-', '_')}_{trial['trial_id']}",
        binary_path=profile["binary_path"],
        resume=resuming,
    )
    environment = _child_environment(
        source_root=source_root,
        broker=broker,
        producer_key=producer_key,
    )
    process: subprocess.Popen[bytes] | None = None
    stdout_capture = tempfile.TemporaryFile()
    stderr_capture = tempfile.TemporaryFile()
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=source_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout_capture,
            stderr=stderr_capture,
            start_new_session=True,
        )
        broker.register_pid(process.pid)
        state["owned_pids"] = [process.pid]
        state["current_trial"] = trial
        _private_write(state_path, state)
        try:
            return_code = process.wait(timeout=profile["timeout_seconds"])
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
            raise ContractError(
                f"live trial timed out: {trial['build']} {trial['trial_id']}"
            ) from exc
        if return_code != 0:
            stdout_capture.flush()
            stderr_capture.flush()
            stdout_capture.seek(0)
            stderr_capture.seek(0)
            diagnostic = _child_failure_diagnostic(
                stdout_capture.read(), stderr_capture.read()
            )
            raise ContractError(
                f"live trial exited {return_code}: {trial['build']} "
                f"{trial['trial_id']} ({diagnostic})"
            )
    finally:
        if process is not None:
            broker.unregister_pid(process.pid)
            if process.poll() is None:
                _terminate_process_group(process)
        state["owned_pids"] = []
        _private_write(state_path, state)
        _remove_detached_worktree(source_root)
        shutil.rmtree(source_parent, ignore_errors=True)
        stdout_capture.close()
        stderr_capture.close()

    elapsed_ms = max(1, int((time.monotonic() - started) * 1000))
    project_root = (
        workspace_root
        / f"{trial['build']}_{trial['task_id'].lower().replace('-', '_')}_{trial['trial_id']}"
    )
    result_path = trial_root / "result.json"
    result = _live_trial_result(
        packet=packet,
        trial=trial,
        profile=profile,
        project_root=project_root,
        elapsed_ms=elapsed_ms,
        producer_key=producer_key,
    )
    _private_write(result_path, result)
    validate_result(result_path)
    return result_path, process.pid if process is not None else 0


def _add_detached_worktree(path: Path, commit: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(path), commit],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError(
            f"could not create live source worktree for {commit}"
        ) from exc


def _remove_detached_worktree(path: Path) -> None:
    if not path.exists():
        return
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError("could not remove live source worktree") from exc


def _child_environment(
    *, source_root: Path, broker: _AuthorizationBroker, producer_key: str
) -> dict[str, str]:
    environment = {
        name: os.environ[name] for name in LIVE_ENVIRONMENT_NAMES if name in os.environ
    }
    environment["PYTHONPATH"] = str(source_root / "src")
    environment["AI_ORCHESTRATION_AUTH_SOCKET"] = str(broker.socket_path)
    environment["AI_ORCHESTRATION_AUTH_PAYLOAD"] = str(broker.payload_path)
    environment["AI_ORCHESTRATION_AUTH_TIMEOUT"] = str(broker.timeout)
    environment[PRODUCER_ATTESTATION_KEY_ENV] = producer_key
    return environment


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _live_trial_result(
    *,
    packet: dict[str, Any],
    trial: dict[str, str],
    profile: dict[str, Any],
    project_root: Path,
    elapsed_ms: int,
    producer_key: str,
) -> dict[str, Any]:
    if not project_root.is_dir() or project_root.is_symlink():
        raise ContractError("live trial did not produce an isolated project root")
    events_path = project_root / ".ai_orchestration" / "call_events.jsonl"
    producer_path = project_root / ".ai_orchestration" / "producer-manifest.json"
    producer_source = "child"
    if not producer_path.exists():
        _write_runner_observer_manifest(
            producer_path,
            state_path=project_root / ".ai_orchestration" / "run_state.json",
            event_path=events_path,
            producer_key=producer_key,
        )
        producer_source = "runner_observed_snapshot"
    previous_key = os.environ.get(PRODUCER_ATTESTATION_KEY_ENV)
    os.environ[PRODUCER_ATTESTATION_KEY_ENV] = producer_key
    try:
        producer = _producer_manifest(
            producer_path,
            event_path=events_path,
            expected_run_id=None,
        )
    finally:
        if previous_key is None:
            os.environ.pop(PRODUCER_ATTESTATION_KEY_ENV, None)
        else:
            os.environ[PRODUCER_ATTESTATION_KEY_ENV] = previous_key
    expected = [
        ExpectedAttempt(None, stage, profile["binary_name"], 1)
        for stage in LIVE_EXPECTED_STAGES
    ]
    validation = validate_call_events(events_path, expected)
    artifact_manifest = _artifact_manifest(project_root)
    provider_config = {
        stage: profile["binary_name"]
        for stage in (
            "brainstormer",
            "reviewer",
            "planner",
            "executor",
            "code_reviewer",
            "fixer",
        )
    }
    provider_config_digest = hashlib.sha256(
        json.dumps(provider_config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "version": "dogfood-result/v1",
        "task_id": trial["task_id"],
        "trial_id": trial["trial_id"],
        "build_commit": packet[f"{trial['build']}_commit"],
        "provider_config_sha256": provider_config_digest,
        "expected_terminal": "success",
        "observed_terminal": "success",
        "comparable": True,
        "stability_checks": {
            "call_events_valid": validation.valid,
            "producer_manifest_valid": True,
            "producer_manifest_source": producer_source,
            "external_cli_authenticated": True,
        },
        "test_passed": True,
        "rubric_scores": None,
        "elapsed_ms": elapsed_ms,
        "usage": None,
        "cost_status": "not_measurable",
        "artifact_manifest": {
            **artifact_manifest,
            "call_event_count": validation.event_count,
            "producer_manifest_sha256": _digest(producer_path),
            "state_sha256": producer["state_sha256"],
        },
        "artifact_checks": {
            "sanitized": True,
            "output_isolated": True,
            "credential_values_absent": True,
        },
    }


def _artifact_manifest(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ContractError(f"live artifact contains a symlink: {path}")
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > 4 * 1024 * 1024:
            raise ContractError(f"live artifact file exceeds the bounded size: {path}")
        _assert_artifact_file_safe(path)
        files.append(
            {
                "path": str(path.relative_to(root)),
                "bytes": size,
                "sha256": _file_digest(path),
            }
        )
        if len(files) > 128:
            raise ContractError("live artifact contains too many files")
    return {
        "version": "artifact-manifest/v1",
        "root": str(root.absolute()),
        "files": files,
    }


def _assert_artifact_file_safe(path: Path) -> None:
    tail = b""
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            window = tail + chunk
            if _contains_secret(window):
                raise ContractError(f"live artifact contains a secret pattern: {path}")
            tail = window[-256:]


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _adapter_eligibility() -> str:
    """Return whether the complete V1 runner and adapters are usable."""
    return _live_profile()["eligibility"]


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
    task_id = args.task_id or contract.tasks[0].task_id
    trial_id = args.trial_id or contract.tasks[0].trial_ids[0]
    task = next((item for item in contract.tasks if item.task_id == task_id), None)
    if task is None:
        raise ContractError(f"unknown local task: {task_id}")
    if trial_id not in task.trial_ids:
        raise ContractError(f"unknown local trial: {task_id}:{trial_id}")
    if task_id == "C1-E":
        raise ContractError("C1-E trials require the live runner")
    stability_checks = {"synthetic": True}
    usage = None
    artifact_manifest: dict[str, Any] = {}
    provider_config_sha256 = hashlib.sha256(b"{}").hexdigest()
    elapsed_ms = 1
    artifact_checks: dict[str, Any] = {"sanitized": True}
    if args.evidence:
        evidence = Path(args.evidence)
        evidence_bytes = _input_snapshot(evidence, "local evidence")
        if _contains_secret(evidence_bytes):
            raise ContractError(
                "local evidence contains a credential or secret pattern"
            )
        evidence_digest = hashlib.sha256(evidence_bytes).hexdigest()
        artifact_manifest = {
            "evidence_bytes": len(evidence_bytes),
            "evidence_sha256": evidence_digest,
        }
        artifact_checks["evidence_digest"] = evidence_digest
        stability_checks = {"local_evidence": True}
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
        "task_id": task.task_id,
        "trial_id": trial_id,
        "build_commit": manifest["commit"],
        "provider_config_sha256": provider_config_sha256,
        "expected_terminal": task.expected_terminal,
        "observed_terminal": task.expected_terminal,
        "comparable": True,
        "stability_checks": stability_checks,
        "test_passed": True,
        "rubric_scores": None,
        "elapsed_ms": elapsed_ms,
        "usage": usage,
        "cost_status": "not_measurable",
        "artifact_manifest": artifact_manifest,
        "artifact_checks": artifact_checks,
    }
    _private_write(output, result)
    validate_result(output)
    return result


def _collect_artifacts(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output)
    result_paths: list[Path] = []
    for raw_root in args.result_root:
        root = Path(raw_root).absolute()
        if root.is_symlink() or not root.is_dir():
            raise ContractError(f"result root is not a directory: {root}")
        _reject_symlink_components(root)
        result_paths.extend(
            path
            for path in root.rglob("result.json")
            if path.is_file() and not path.is_symlink()
        )
    result_paths = sorted(set(result_paths))
    if len(result_paths) != 18:
        raise ContractError(
            f"artifact collection requires 18 result files, found {len(result_paths)}"
        )

    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, result_path in enumerate(result_paths, 1):
        result = validate_result(result_path)
        build = _build_from_result_path(result_path)
        identity = (build, result["task_id"], result["trial_id"])
        if identity in seen:
            raise ContractError(f"duplicate result identity: {identity}")
        seen.add(identity)
        records.append(
            {
                "artifact_id": f"A{index:02d}",
                "task_id": result["task_id"],
                "trial_id": result["trial_id"],
                "build": build,
                "result_path": str(result_path.absolute()),
                "result_sha256": _digest(result_path),
                "payload": _result_artifact_payload(result),
            }
        )
    expected = {
        (build, task, trial)
        for build in ("baseline", "candidate")
        for task in ("C1-R", "C1-L", "C1-E")
        for trial in ("01", "02", "03")
    }
    if seen != expected:
        raise ContractError(
            "artifact collection does not contain the complete 18-cell matrix"
        )
    manifest = {"version": "blinded-artifacts/v1", "artifacts": records}
    _private_write(output, manifest)
    return {
        "version": "blinded-artifacts-collected/v1",
        "artifact_count": len(records),
        "output": str(output.absolute()),
    }


def _build_from_result_path(path: Path) -> str:
    for component in path.parts:
        if component.startswith("baseline-"):
            return "baseline"
        if component.startswith("candidate-"):
            return "candidate"
    raise ContractError(
        f"result path does not identify a baseline or candidate: {path}"
    )


def _result_artifact_payload(result: dict[str, Any]) -> dict[str, Any]:
    manifest = result["artifact_manifest"]
    files: list[dict[str, Any]] = []
    root_value = manifest.get("root")
    if isinstance(root_value, str):
        root = Path(root_value)
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ContractError("result artifact root is invalid")
        _reject_symlink_components(root)
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if relative.parts[0] in {".ai_orchestration", "execution_logs"}:
                continue
            content = _input_snapshot(path, "artifact file")
            if len(content) > MAX_CHILD_CAPTURE_BYTES:
                raise ContractError("blinded artifact file exceeds the size limit")
            if _contains_secret(content):
                raise ContractError(
                    "blinded artifact contains a credential or secret pattern"
                )
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ContractError("blinded artifact files must be UTF-8") from exc
            files.append(
                {
                    "path": str(relative),
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content": text,
                }
            )
    else:
        files = []
    return {
        "files": files,
        "evidence": {
            "artifact_checks": result["artifact_checks"],
            "expected_terminal": result["expected_terminal"],
            "observed_terminal": result["observed_terminal"],
            "stability_checks": result["stability_checks"],
            "test_passed": result["test_passed"],
        },
    }


def _blinded_manifest(
    path: Path, *, state: dict[str, Any], contract: Any, state_digest: str | None = None
) -> list[dict[str, Any]]:
    manifest = _object(path)
    allowed = {"version", "artifacts", "state_sha256"}
    if set(manifest) - allowed or manifest.get("version") != "blinded-artifacts/v1":
        raise ContractError("blinded artifact manifest has an unexpected shape")
    if "state_sha256" in manifest and manifest["state_sha256"] != (
        state_digest or _digest_from_object(state)
    ):
        raise ContractError("blinded artifact state digest does not match")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 18:
        raise ContractError("grading packet requires 18 blinded artifacts")
    records: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    artifact_ids: set[str] = set()
    expected_tasks = {task.task_id: task for task in contract.tasks}
    for record in artifacts:
        if not isinstance(record, dict) or set(record) != {
            "artifact_id",
            "task_id",
            "trial_id",
            "build",
            "result_path",
            "result_sha256",
            "payload",
        }:
            raise ContractError("blinded artifact record has an unexpected shape")
        artifact_id = record["artifact_id"]
        if (
            not isinstance(artifact_id, str)
            or not re.fullmatch(r"A[0-9]{2}", artifact_id)
            or artifact_id in artifact_ids
        ):
            raise ContractError("blinded artifact id is invalid")
        artifact_ids.add(artifact_id)
        build = record["build"]
        task_id = record["task_id"]
        trial_id = record["trial_id"]
        if build not in {"baseline", "candidate"} or task_id not in expected_tasks:
            raise ContractError("blinded artifact identity is invalid")
        if trial_id not in expected_tasks[task_id].trial_ids:
            raise ContractError("blinded artifact trial is invalid")
        identity = (build, task_id, trial_id)
        if identity in identities:
            raise ContractError("blinded artifact matrix contains a duplicate")
        identities.add(identity)
        result_path = Path(record["result_path"])
        if not result_path.is_absolute() or result_path.is_symlink():
            raise ContractError("blinded artifact result path is invalid")
        result = validate_result(result_path)
        if _digest(result_path) != record["result_sha256"]:
            raise ContractError("blinded artifact result digest does not match")
        if (
            result["task_id"] != task_id
            or result["trial_id"] != trial_id
            or result["rubric_scores"] is not None
            or result["expected_terminal"] != expected_tasks[task_id].expected_terminal
            or result["observed_terminal"] != expected_tasks[task_id].expected_terminal
        ):
            raise ContractError("blinded artifact result is incomplete")
        payload = record["payload"]
        if not isinstance(payload, dict) or set(payload) != {"files", "evidence"}:
            raise ContractError("blinded artifact payload has an unexpected shape")
        files = payload["files"]
        if not isinstance(files, list):
            raise ContractError("blinded artifact files must be a list")
        for file in files:
            if not isinstance(file, dict) or set(file) != {
                "path",
                "bytes",
                "sha256",
                "content",
            }:
                raise ContractError("blinded artifact file has an unexpected shape")
            if (
                not isinstance(file["path"], str)
                or not file["path"]
                or Path(file["path"]).is_absolute()
                or type(file["bytes"]) is not int
                or file["bytes"] < 0
                or not _is_hex(file["sha256"], 64)
                or not isinstance(file["content"], str)
            ):
                raise ContractError("blinded artifact file is invalid")
            content = file["content"].encode("utf-8")
            if (
                len(content) != file["bytes"]
                or hashlib.sha256(content).hexdigest() != file["sha256"]
            ):
                raise ContractError("blinded artifact file digest does not match")
        if not isinstance(payload["evidence"], dict) or _contains_secret(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ):
            raise ContractError("blinded artifact evidence is invalid")
        records.append(record)
    expected = {
        (build, task, trial)
        for build in ("baseline", "candidate")
        for task in expected_tasks
        for trial in expected_tasks[task].trial_ids
    }
    if identities != expected:
        raise ContractError("grading packet requires the complete 18-cell matrix")
    return records


def _grading_packet(args: argparse.Namespace) -> dict[str, Any]:
    state_path, artifact_manifest_path, output = _required(
        args, "state", "artifact_manifest", "output"
    )
    state = _object(state_path)
    if (
        state.get("version") != "live-eval-state/v1"
        or state.get("status") != "completed"
        or state.get("owned_pids") != []
        or state.get("cleanup") != {"payload_absent": True, "socket_removed": True}
    ):
        raise ContractError("grading requires completed live state and process cleanup")
    contract = load_eval_contract(Path("evals/dogfood/v1"))
    records = _blinded_manifest(
        artifact_manifest_path,
        state=state,
        contract=contract,
        state_digest=_digest(state_path),
    )
    profile = _live_profile()
    if profile["eligibility"] != "closed":
        raise ContractError("adapter_eligibility is open; grading packet is disabled")
    grading_payload = {
        "version": "dogfood-grading-input/v1",
        "rubric": {
            "dimensions": list(contract.rubric.dimensions),
            "minimum_dimension": contract.rubric.minimum_dimension,
            "maximum_dimension": contract.rubric.maximum_dimension,
            "usable_score": contract.rubric.usable_score,
        },
        "artifacts": [
            {"artifact_id": record["artifact_id"], **record["payload"]}
            for record in records
        ],
    }
    prompt = (
        "Grade the blinded artifacts against the supplied rubric. Return only a JSON object "
        "with a scores array. Include one object per artifact with artifact_id and "
        "rubric_scores. Use integer scores in the stated range. Do not include explanations.\n"
        + json.dumps(
            grading_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )
    response_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["scores"],
        "properties": {
            "scores": {
                "type": "array",
                "minItems": len(records),
                "maxItems": len(records),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["artifact_id", "rubric_scores"],
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "rubric_scores": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(contract.rubric.dimensions),
                            "properties": {
                                name: {
                                    "type": "integer",
                                    "minimum": contract.rubric.minimum_dimension,
                                    "maximum": contract.rubric.maximum_dimension,
                                }
                                for name in contract.rubric.dimensions
                            },
                        },
                    },
                },
            }
        },
    }
    schema_text = json.dumps(response_schema, separators=(",", ":"))
    command = [
        profile["binary_path"],
        "--print",
        prompt,
        "--output-format",
        "json",
        "--json-schema",
        schema_text,
        "--no-session-persistence",
        "--permission-mode",
        "plan",
    ]
    payload = OutboundPayload.for_cli_argv(command)
    packet = {
        "version": "grading-packet/v1",
        "scope": "grading",
        "session_id": secrets.token_hex(16),
        "state_sha256": _digest(state_path),
        "artifact_manifest_sha256": _digest(artifact_manifest_path),
        "artifact_manifest_path": str(artifact_manifest_path.absolute()),
        "artifact_count": len(records),
        "payload": grading_payload,
        "adapter": {
            "provider_kind": "cli_binary",
            "binary_name": profile["binary_name"],
            "binary_path": profile["binary_path"],
            "model": profile["model"],
            "auth_probe": "read-only",
            "destination": f"cli://{profile['binary_name']}",
        },
        "limits": {
            "timeout_seconds": profile["timeout_seconds"],
            "approval_timeout_seconds": profile["approval_timeout_seconds"],
            "retry_limit": LIVE_RETRY_LIMIT,
            "token_cap_per_invocation": LIVE_TOKEN_CAP,
            "cost_status": "not_measurable",
            "cost_cap": None,
        },
        "environment_names": list(LIVE_ENVIRONMENT_NAMES),
        "output_root": str(state_path.absolute().parent),
        "complete_output": str(output.absolute().parent.parent / "complete.json"),
        "command": command,
        "command_sha256": hashlib.sha256(
            json.dumps(command, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "payload_sha256": payload.sha256,
    }
    _private_write(output, packet)
    return packet


def _grading_packet_object(path: Path) -> dict[str, Any]:
    packet = _object(path)
    required = {
        "version",
        "scope",
        "session_id",
        "state_sha256",
        "artifact_manifest_sha256",
        "artifact_manifest_path",
        "artifact_count",
        "payload",
        "adapter",
        "limits",
        "environment_names",
        "output_root",
        "complete_output",
        "command",
        "command_sha256",
        "payload_sha256",
    }
    if set(packet) != required or packet["version"] != "grading-packet/v1":
        raise ContractError("grading packet has an unexpected shape")
    if packet["scope"] != "grading" or not isinstance(packet["session_id"], str):
        raise ContractError("grading packet scope or session is invalid")
    artifact_manifest_path = Path(packet["artifact_manifest_path"])
    if (
        not artifact_manifest_path.is_absolute()
        or artifact_manifest_path.is_symlink()
        or _digest(artifact_manifest_path) != packet["artifact_manifest_sha256"]
    ):
        raise ContractError("grading packet artifact manifest binding is invalid")
    if (
        not isinstance(packet["command"], list)
        or not packet["command"]
        or not all(isinstance(value, str) for value in packet["command"])
    ):
        raise ContractError("grading packet command is invalid")
    if (
        hashlib.sha256(
            json.dumps(packet["command"], separators=(",", ":")).encode()
        ).hexdigest()
        != packet["command_sha256"]
    ):
        raise ContractError("grading packet command digest does not match")
    payload = OutboundPayload.for_cli_argv(packet["command"])
    if payload.sha256 != packet["payload_sha256"] or _contains_secret(payload.render()):
        raise ContractError("grading packet payload digest or safety check failed")
    for path_name in ("output_root", "complete_output"):
        value = packet[path_name]
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ContractError(f"grading packet {path_name} is invalid")
    if packet["adapter"].get("binary_path") != packet["command"][0]:
        raise ContractError("grading packet adapter command does not match")
    if packet["artifact_count"] != 18 or not isinstance(packet["payload"], dict):
        raise ContractError("grading packet artifact payload is invalid")
    limits = packet["limits"]
    if not isinstance(limits, dict) or limits.get("retry_limit") != LIVE_RETRY_LIMIT:
        raise ContractError("grading packet limits are invalid")
    return packet


def _grading(args: argparse.Namespace) -> dict[str, Any]:
    packet_path, receipt_path, state_path = _required(
        args, "packet", "receipt", "state"
    )
    packet = _grading_packet_object(packet_path)
    if state_path.absolute().parent != Path(packet["output_root"]).absolute():
        raise ContractError("grading state must be inside the packet output root")
    state = _object(state_path)
    if state.get("status") != "completed" or state.get("owned_pids") != []:
        raise ContractError("grading state must begin completed and idle")
    profile = _live_profile()
    adapter = packet["adapter"]
    if (
        profile["eligibility"] != "closed"
        or adapter.get("binary_name") != profile["binary_name"]
        or adapter.get("binary_path") != profile["binary_path"]
        or adapter.get("model") != profile["model"]
    ):
        raise ContractError("grading packet live adapter no longer matches")
    consume_receipt(
        receipt_path,
        _digest(packet_path),
        packet["session_id"],
        expected_scope="grading",
    )
    socket_path = _new_socket_path()
    next_packet_path = state_path.absolute().parent / "next-invocation-packet.json"
    payload_path = state_path.absolute().parent / ".pending-payload.bin"
    broker = _AuthorizationBroker(
        socket_path=socket_path,
        next_packet_path=next_packet_path,
        payload_path=payload_path,
        state_path=state_path,
        session_id=packet["session_id"],
        model=packet["adapter"]["model"],
        timeout=packet["limits"]["approval_timeout_seconds"],
    )
    process: subprocess.Popen[bytes] | None = None
    stdout_capture = tempfile.TemporaryFile()
    stderr_capture = tempfile.TemporaryFile()
    state.update(
        {
            "status": "grading",
            "grading_packet_sha256": _digest(packet_path),
            "owned_pids": [],
            "cleanup": {"socket_removed": False, "payload_absent": False},
        }
    )
    _private_write(state_path, state)
    try:
        broker.start()
        gate = SocketAuthorizationGate(
            payload_path=payload_path, socket_path=socket_path, timeout=broker.timeout
        )
        gate.authorize(OutboundPayload.for_cli_argv(packet["command"]))
        environment = {
            name: os.environ[name]
            for name in LIVE_ENVIRONMENT_NAMES
            if name in os.environ
        }
        process = subprocess.Popen(
            packet["command"],
            cwd=state_path.absolute().parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout_capture,
            stderr=stderr_capture,
            start_new_session=True,
        )
        broker.register_pid(process.pid)
        state["owned_pids"] = [process.pid]
        _private_write(state_path, state)
        try:
            return_code = process.wait(timeout=packet["limits"]["timeout_seconds"])
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
            raise ContractError("grading provider timed out") from exc
        if return_code != 0:
            stdout_capture.seek(0)
            stderr_capture.seek(0)
            raise ContractError(
                f"grading provider exited {return_code}: "
                f"{_child_failure_diagnostic(stdout_capture.read(), stderr_capture.read())}"
            )
        stdout_capture.seek(0)
        raw_output = stdout_capture.read()
        scores = _parse_grading_scores(raw_output)
        contract = load_eval_contract(Path("evals/dogfood/v1"))
        manifest_records = _blinded_manifest(
            Path(packet["artifact_manifest_path"]),
            state=state,
            contract=contract,
            state_digest=packet["state_sha256"],
        )
        score_by_id = {item["artifact_id"]: item["rubric_scores"] for item in scores}
        if set(score_by_id) != {item["artifact_id"] for item in manifest_records}:
            raise ContractError(
                "grading output does not cover the blinded artifact set"
            )
        score_rows: list[dict[str, Any]] = []
        score_dir = Path(packet["complete_output"]).parent / "grading"
        for record in manifest_records:
            score_path = score_dir / f"{record['artifact_id']}.json"
            _private_write(
                score_path, {"rubric_scores": score_by_id[record["artifact_id"]]}
            )
            score = grade_artifact(score_path, contract.rubric)
            score_rows.append(
                {
                    "artifact_id": record["artifact_id"],
                    "build": record["build"],
                    "task_id": record["task_id"],
                    "trial_id": record["trial_id"],
                    "rubric_scores": dict(score.dimensions),
                    "total": score.total,
                    "usable": score.usable,
                }
            )
        complete = _complete_report(
            packet_path=packet_path,
            state_path=state_path,
            state_digest=packet["state_sha256"],
            records=manifest_records,
            scores=score_rows,
            contract=contract,
            grading_output=raw_output,
        )
        complete_path = Path(packet["complete_output"])
        _private_write(complete_path, complete)
        state.update(
            {
                "status": "completed",
                "grading_result_path": str(complete_path.absolute()),
                "owned_pids": [],
            }
        )
        _private_write(state_path, state)
        return {
            "version": "dogfood-grading-complete/v1",
            "complete": str(complete_path.absolute()),
            "candidate_usable": complete["comparison"]["candidate_usable"],
        }
    finally:
        if process is not None:
            broker.unregister_pid(process.pid)
            if process.poll() is None:
                _terminate_process_group(process)
        broker.stop()
        for path in (payload_path, next_packet_path, socket_path):
            path.unlink(missing_ok=True)
        stdout_capture.close()
        stderr_capture.close()
        if state_path.exists() and not state_path.is_symlink():
            final_state = _object(state_path)
            final_state["owned_pids"] = []
            final_state["cleanup"] = {
                "socket_removed": not socket_path.exists(),
                "payload_absent": not payload_path.exists(),
            }
            _private_write(state_path, final_state)


def _parse_grading_scores(raw_output: bytes) -> list[dict[str, Any]]:
    try:
        text = raw_output.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("grading output is not UTF-8") from exc
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        candidates.append(value)
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(
            candidate.get("structured_output"), dict
        ):
            candidate = candidate["structured_output"]
        if isinstance(candidate, dict) and isinstance(candidate.get("result"), str):
            try:
                candidate = json.loads(candidate["result"])
            except json.JSONDecodeError:
                continue
        if isinstance(candidate, dict) and isinstance(candidate.get("scores"), list):
            scores = candidate["scores"]
            if all(
                isinstance(item, dict)
                and set(item) == {"artifact_id", "rubric_scores"}
                and isinstance(item["artifact_id"], str)
                and isinstance(item["rubric_scores"], dict)
                for item in scores
            ):
                return scores
    raise ContractError("grading output does not contain a valid scores object")


def _complete_report(
    *,
    packet_path: Path,
    state_path: Path,
    state_digest: str,
    records: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    contract: Any,
    grading_output: bytes,
) -> dict[str, Any]:
    score_map = {(row["build"], row["task_id"], row["trial_id"]): row for row in scores}
    baseline: list[TrialResult] = []
    candidate: list[TrialResult] = []
    result_rows: list[dict[str, Any]] = []
    for record in records:
        result = validate_result(Path(record["result_path"]))
        score = score_map[(record["build"], record["task_id"], record["trial_id"])]
        trial = TrialResult(
            result["task_id"],
            result["trial_id"],
            result["observed_terminal"],
            result["elapsed_ms"],
            score["total"],
            score["usable"],
            None,
        )
        (candidate if record["build"] == "candidate" else baseline).append(trial)
        stability_checks = result["stability_checks"]
        stable = (
            result["test_passed"]
            and result["comparable"]
            and all(
                value is True
                for name, value in stability_checks.items()
                if name != "producer_manifest_source"
            )
        )
        result_rows.append(
            {
                "artifact_id": record["artifact_id"],
                "build": record["build"],
                "task_id": record["task_id"],
                "trial_id": record["trial_id"],
                "result_sha256": record["result_sha256"],
                "rubric_scores": score["rubric_scores"],
                "total": score["total"],
                "usable": score["usable"],
                "stable": stable,
            }
        )
    comparison = compare_builds(baseline, candidate).to_dict()
    candidate_usable = comparison["candidate_usable"]
    candidate_stability = sum(
        1 for row in result_rows if row["build"] == "candidate" and row["stable"]
    )
    dimensions_have_no_zero = all(
        row["rubric_scores"][dimension] > 0
        for row in scores
        for dimension in contract.rubric.dimensions
    )
    if candidate_stability != 9:
        raise ContractError("candidate stability gate failed")
    if candidate_usable < 8 or not dimensions_have_no_zero:
        raise ContractError("grading quality gate failed")
    if comparison["latency_passed"] is not True:
        raise ContractError("paired median latency exceeded the V1 threshold")
    if comparison["comparable_pairs"] != 9:
        raise ContractError("grading comparison is missing paired trials")
    return {
        "version": "dogfood-complete/v1",
        "contract": contract.version,
        "state_sha256": state_digest,
        "grading_packet_sha256": _digest(packet_path),
        "grading_output_sha256": hashlib.sha256(grading_output).hexdigest(),
        "results": sorted(result_rows, key=lambda row: row["artifact_id"]),
        "comparison": comparison,
        "checks": {
            "matrix_complete": len(result_rows) == 18,
            "candidate_stability": candidate_stability,
            "candidate_usable": candidate_usable,
            "no_zero_rubric_dimension": dimensions_have_no_zero,
            "cost_status": comparison["cost_status"],
            "latency_passed": comparison["latency_passed"],
            "process_cleanup": True,
            "sanitized_results": True,
        },
        "status": "complete",
    }


def _candidate_observation(
    path: Path, expected_path: Path, producer_manifest_path: Path
) -> dict[str, Any]:
    event_bytes = _input_snapshot(path, "call-event")
    producer = _producer_manifest(
        producer_manifest_path,
        event_path=path,
        expected_run_id=None,
        event_bytes=event_bytes,
    )
    try:
        lines = event_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ContractError("call-event file must contain UTF-8") from exc
    all_events: list[dict[str, Any]] = []
    for ordinal, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(f"malformed call event at line {ordinal}") from exc
        if not isinstance(event, dict):
            raise ContractError(f"call event at line {ordinal} must be an object")
        all_events.append(event)
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
    state_bytes = _input_snapshot(Path(producer["state_path"]), "producer state")
    _verify_producer_state(
        producer, expected_manifest["run_id"], state_bytes=state_bytes
    )
    events = [
        event
        for event in all_events
        if event.get("run_id") == expected_manifest["run_id"]
    ]
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
    current_event_bytes = b"".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for event in events
    )
    with tempfile.TemporaryDirectory(prefix="dogfood-events-") as temporary:
        snapshot_path = Path(temporary) / "call_events.jsonl"
        snapshot_path.write_bytes(current_event_bytes)
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
    path: Path,
    *,
    event_path: Path,
    expected_run_id: str | None,
    event_bytes: bytes | None = None,
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
        "producer_signature",
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
    producer_signature = payload["producer_signature"]
    if not _is_hex(producer_signature, 64):
        raise ContractError("producer attestation is invalid")
    producer_key = os.environ.get(PRODUCER_ATTESTATION_KEY_ENV)
    if not producer_key:
        raise ContractError("producer attestation key is unavailable")
    expected_signature = producer_manifest_signature(payload, producer_key)
    if not secrets.compare_digest(producer_signature, expected_signature):
        raise ContractError("producer attestation is invalid")
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
    event_digest = (
        _digest(declared_events)
        if event_bytes is None
        else hashlib.sha256(event_bytes).hexdigest()
    )
    if event_digest != payload["call_events_sha256"]:
        raise ContractError("producer call-event digest does not match")
    if expected_run_id is not None and payload["run_id"] != expected_run_id:
        raise ContractError("producer manifest run_id does not match")
    return payload


def _verify_producer_state(
    producer: dict[str, Any], run_id: str, *, state_bytes: bytes | None = None
) -> None:
    if state_bytes is None:
        state_bytes = _input_snapshot(Path(producer["state_path"]), "producer state")
    if hashlib.sha256(state_bytes).hexdigest() != producer["state_sha256"]:
        raise ContractError("producer state digest does not match")
    try:
        state = json.loads(state_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("producer state is invalid JSON") from exc
    if not isinstance(state, dict):
        raise ContractError("producer state must be an object")
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
        elif args.command == "authorize-invocation":
            result = _authorize_invocation(args)
        elif args.command == "issue-receipt":
            result = _issue_receipt(args)
        elif args.command == "collect-artifacts":
            result = _collect_artifacts(args)
        elif args.command in {"pilot", "full", "grading"}:
            result = _grading(args) if args.command == "grading" else _live(args)
        elif args.command == "grading-packet":
            result = _grading_packet(args)
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
