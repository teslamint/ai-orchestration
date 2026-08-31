import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_orchestration.evals.dogfood import (
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

CONTRACT = Path("evals/dogfood/v1")
TEST_APPROVAL_KEY = "cycle-one-test-approval-key-32-bytes"
TEST_PRODUCER_KEY = "cycle-one-producer-attestation-key-32-bytes"


def _eval_env() -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": "src",
        "AI_ORCHESTRATION_PRODUCER_ATTESTATION_KEY": TEST_PRODUCER_KEY,
    }


@pytest.fixture(autouse=True)
def _approval_key(monkeypatch):
    monkeypatch.setenv("AI_ORCHESTRATION_USER_APPROVAL_KEY", TEST_APPROVAL_KEY)
    monkeypatch.setenv("AI_ORCHESTRATION_PRODUCER_ATTESTATION_KEY", TEST_PRODUCER_KEY)


def _producer_manifest_payload(
    state: Path, events: Path, run_id: str
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": "producer-manifest/v1",
        "run_id": run_id,
        "state_path": str(state),
        "state_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
        "call_events_path": str(events),
        "call_events_sha256": hashlib.sha256(events.read_bytes()).hexdigest(),
    }
    payload["producer_signature"] = producer_manifest_signature(
        payload, TEST_PRODUCER_KEY
    )
    return payload


def _load_eval_runner():
    path = Path(__file__).parents[1] / "scripts" / "run_dogfood_eval.py"
    spec = importlib.util.spec_from_file_location("run_dogfood_eval_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contract_fixes_tasks_trials_rubric_and_thresholds():
    contract = load_eval_contract(CONTRACT)

    assert contract.version == "dogfood/v1"
    assert [task.task_id for task in contract.tasks] == ["C1-R", "C1-L", "C1-E"]
    assert all(task.trial_ids == ("01", "02", "03") for task in contract.tasks)
    assert contract.rubric.dimensions == (
        "requirement_coverage",
        "executable_correctness",
        "test_quality",
        "maintainability",
        "evidence_clarity",
    )
    assert contract.rubric.usable_score == 8
    assert contract.rubric.max_latency_ratio == 1.25


def test_contract_rejects_unknown_version_and_unexpected_shape(tmp_path):
    contract = tmp_path / "contract"
    contract.mkdir()
    (contract / "dataset.json").write_text(
        json.dumps({"version": "dogfood/v2", "tasks": []}), encoding="utf-8"
    )
    (contract / "grader.yaml").write_text(
        json.dumps({"version": "grader/v1", "dimensions": []}), encoding="utf-8"
    )

    with pytest.raises(ContractError, match="dataset version"):
        load_eval_contract(contract)


def test_grade_artifact_requires_eight_and_no_zero(tmp_path):
    contract = load_eval_contract(CONTRACT)
    artifact = tmp_path / "artifact.json"
    artifact.write_text(
        json.dumps(
            {
                "rubric_scores": {
                    "requirement_coverage": 2,
                    "executable_correctness": 2,
                    "test_quality": 1,
                    "maintainability": 1,
                    "evidence_clarity": 2,
                }
            }
        ),
        encoding="utf-8",
    )

    score = grade_artifact(artifact, contract.rubric)
    assert score.total == 8
    assert score.usable is True

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["rubric_scores"]["test_quality"] = 0
    payload["rubric_scores"]["maintainability"] = 2
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    score = grade_artifact(artifact, contract.rubric)
    assert score.total == 8
    assert score.usable is False


def test_call_event_validation_rejects_content_and_missing_attempt(tmp_path):
    path = tmp_path / "events.jsonl"
    event = {
        "version": "call-event/v1",
        "run_id": "99756532-a866-4e91-8480-47adac64778e",
        "task_id": "C1-R",
        "executor_step_id": None,
        "stage": "planner",
        "provider_kind": "proxy_model",
        "provider_id": "gpt-test",
        "attempt": 1,
        "prompt_bytes": 12,
        "estimated_input_tokens": 3,
        "reported_input_tokens": None,
        "reported_output_tokens": None,
        "transport_retry_count": None,
        "latency_ms": 10,
        "outcome": "success",
        "error_kind": None,
    }
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    expected = [ExpectedAttempt("C1-R", "planner", "gpt-test", 1)]
    assert validate_call_events(path, expected).valid is True

    event["attempt"] = True
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="attempt"):
        validate_call_events(path, expected)
    event["attempt"] = 1

    event["prompt"] = "must never persist"
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="content field"):
        validate_call_events(path, expected)

    path.write_text("", encoding="utf-8")
    with pytest.raises(ContractError, match="missing expected"):
        validate_call_events(path, expected)


def test_call_event_validation_rejects_current_credential_value(tmp_path, monkeypatch):
    credential = "sk-test-current-value-123456"
    monkeypatch.setenv("OPENAI_API_KEY", credential)
    path = tmp_path / "events.jsonl"
    event = {
        "version": "call-event/v1",
        "run_id": "99756532-a866-4e91-8480-47adac64778e",
        "task_id": "C1-R",
        "executor_step_id": None,
        "stage": "planner",
        "provider_kind": "proxy_model",
        "provider_id": credential,
        "attempt": 1,
        "prompt_bytes": 1,
        "estimated_input_tokens": 1,
        "reported_input_tokens": None,
        "reported_output_tokens": None,
        "transport_retry_count": None,
        "latency_ms": 1,
        "outcome": "error",
        "error_kind": "provider",
    }
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(ContractError, match="credential value"):
        validate_call_events(path, [])


def test_call_event_validation_rejects_duplicate_attempt_identity(tmp_path):
    path = tmp_path / "events.jsonl"
    event = {
        "version": "call-event/v1",
        "run_id": "99756532-a866-4e91-8480-47adac64778e",
        "task_id": "C1-R",
        "executor_step_id": None,
        "stage": "planner",
        "provider_kind": "proxy_model",
        "provider_id": "gpt-test",
        "attempt": 1,
        "prompt_bytes": 1,
        "estimated_input_tokens": 1,
        "reported_input_tokens": None,
        "reported_output_tokens": None,
        "transport_retry_count": None,
        "latency_ms": 1,
        "outcome": "success",
        "error_kind": None,
    }
    path.write_text((json.dumps(event) + "\n") * 2, encoding="utf-8")
    expected = [ExpectedAttempt("C1-R", "planner", "gpt-test", 1)]

    with pytest.raises(ContractError, match="unexpected call events"):
        validate_call_events(path, expected)


def test_call_event_validation_rejects_unexpected_extra_identity(tmp_path):
    path = tmp_path / "events.jsonl"
    base = {
        "version": "call-event/v1",
        "run_id": "99756532-a866-4e91-8480-47adac64778e",
        "task_id": "C1-R",
        "executor_step_id": None,
        "stage": "planner",
        "provider_kind": "direct_vendor",
        "provider_id": "openai_api",
        "attempt": 1,
        "prompt_bytes": 1,
        "estimated_input_tokens": 1,
        "reported_input_tokens": None,
        "reported_output_tokens": None,
        "transport_retry_count": None,
        "latency_ms": 1,
        "outcome": "success",
        "error_kind": None,
    }
    extra = {**base, "stage": "executor", "provider_id": "gpt-5.5"}
    path.write_text(
        json.dumps(base) + "\n" + json.dumps(extra) + "\n", encoding="utf-8"
    )

    with pytest.raises(ContractError, match="unexpected call events"):
        validate_call_events(
            path, [ExpectedAttempt("C1-R", "planner", "openai_api", 1)]
        )


def test_compare_builds_pairs_terminal_latency_and_reports_unmeasured_cost():
    baseline = [
        TrialResult("C1-E", "01", "success", 100, 8, True, None),
        TrialResult("C1-E", "02", "error", 1000, None, False, None),
    ]
    candidate = [
        TrialResult("C1-E", "01", "success", 124, 9, True, None),
        TrialResult("C1-E", "02", "success", 1, 9, True, None),
    ]

    report = compare_builds(baseline, candidate)

    assert report.comparable_pairs == 1
    assert report.latency_ratio == pytest.approx(1.24)
    assert report.latency_passed is True
    assert report.cost_status == "not_measurable"
    assert report.candidate_usable == 2


def _receipt(
    packet_sha256: str,
    session_id: str,
    *,
    nonce: str = "93c9c8762cbc4ecca3673ebc43a43e58",
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    payload = {
        "version": "approval-receipt/v1",
        "nonce": nonce,
        "issuer": "user",
        "scope": "pilot",
        "packet_sha256": packet_sha256,
        "session_id": session_id,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
        "consumed_at": None,
    }
    payload["approval_signature"] = receipt_signature(payload, TEST_APPROVAL_KEY)
    return payload


def test_receipt_is_packet_bound_mode_0600_and_one_shot(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(_receipt("a" * 64, "session-1")), encoding="utf-8")
    receipt.chmod(0o600)

    reservation = consume_receipt(
        receipt, "a" * 64, "session-1", expected_scope="pilot"
    )

    assert reservation.scope == "pilot"
    assert reservation.reservation_path.exists()
    assert not receipt.exists()
    with pytest.raises(ContractError, match="receipt does not exist"):
        consume_receipt(receipt, "a" * 64, "session-1", expected_scope="pilot")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"packet_sha256": "b" * 64}, "packet"),
        ({"session_id": "other"}, "session"),
        ({"issuer": "agent"}, "issuer"),
        ({"scope": "full"}, "scope"),
        ({"consumed_at": "already"}, "consumed"),
    ],
)
def test_receipt_rejects_mismatch_and_replay(tmp_path, mutation, message):
    payload = _receipt("a" * 64, "session-1")
    payload.update(mutation)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    receipt.chmod(0o600)

    with pytest.raises(ContractError, match=message):
        consume_receipt(receipt, "a" * 64, "session-1", expected_scope="pilot")


def test_receipt_rejects_stale_permissions_and_concurrent_consumption(tmp_path):
    stale = _receipt("a" * 64, "session-1")
    stale["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    stale["approval_signature"] = receipt_signature(stale, TEST_APPROVAL_KEY)
    receipt = tmp_path / "stale.json"
    receipt.write_text(json.dumps(stale), encoding="utf-8")
    receipt.chmod(0o600)
    with pytest.raises(ContractError, match="expired"):
        consume_receipt(receipt, "a" * 64, "session-1", expected_scope="pilot")

    receipt.write_text(json.dumps(_receipt("a" * 64, "session-1")), encoding="utf-8")
    receipt.chmod(0o644)
    with pytest.raises(ContractError, match="0600"):
        consume_receipt(receipt, "a" * 64, "session-1", expected_scope="pilot")

    receipt.chmod(0o600)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: _consume_result(receipt),
                range(2),
            )
        )
    assert sorted(results) == ["consumed", "rejected"]


def test_receipt_rejects_symlink(tmp_path):
    target = tmp_path / "target.json"
    target.write_text(json.dumps(_receipt("a" * 64, "session-1")), encoding="utf-8")
    target.chmod(0o600)
    receipt = tmp_path / "receipt.json"
    receipt.symlink_to(target)

    with pytest.raises(ContractError, match="symlink"):
        consume_receipt(receipt, "a" * 64, "session-1", expected_scope="pilot")


def test_receipt_requires_first_hand_approval_attestation(tmp_path, monkeypatch):
    payload = _receipt("a" * 64, "session-1")
    payload["approval_signature"] = "0" * 64
    receipt = tmp_path / "unsigned.json"
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    receipt.chmod(0o600)

    with pytest.raises(ContractError, match="first-hand attestation"):
        consume_receipt(receipt, "a" * 64, "session-1", expected_scope="pilot")

    receipt.write_text(json.dumps(_receipt("a" * 64, "session-1")), encoding="utf-8")
    receipt.chmod(0o600)
    monkeypatch.delenv("AI_ORCHESTRATION_USER_APPROVAL_KEY")
    with pytest.raises(ContractError, match="attestation key"):
        consume_receipt(receipt, "a" * 64, "session-1", expected_scope="pilot")


def test_cancelled_trial_requires_fresh_receipt_before_retry(tmp_path):
    packet = "a" * 64
    first = tmp_path / "first-receipt.json"
    first.write_text(json.dumps(_receipt(packet, "session-1")), encoding="utf-8")
    first.chmod(0o600)

    reservation = consume_receipt(first, packet, "session-1", expected_scope="pilot")

    assert not first.exists()
    assert reservation.reservation_path.exists()
    assert not (tmp_path / "call_events.jsonl").exists()
    with pytest.raises(ContractError, match="receipt does not exist"):
        consume_receipt(first, packet, "session-1", expected_scope="pilot")

    fresh = tmp_path / "fresh-receipt.json"
    fresh.write_text(
        json.dumps(
            _receipt(
                packet,
                "session-1",
                nonce="a3c9c8762cbc4ecca3673ebc43a43e58",
            )
        ),
        encoding="utf-8",
    )
    fresh.chmod(0o600)
    fresh_reservation = consume_receipt(
        fresh, packet, "session-1", expected_scope="pilot"
    )
    assert fresh_reservation.nonce != reservation.nonce


def _consume_result(receipt: Path) -> str:
    try:
        consume_receipt(receipt, "a" * 64, "session-1", expected_scope="pilot")
    except ContractError:
        return "rejected"
    return "consumed"


def test_cli_preflight_is_offline_and_live_commands_fail_closed(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    baseline_result = tmp_path / "baseline-result.json"
    baseline_result.write_text("{}", encoding="utf-8")
    baseline_command = "offline baseline command"
    baseline.write_text(
        json.dumps(
            {
                "version": "baseline-manifest/v1",
                "commit": commit,
                "tree": tree,
                "source_archive_sha256": "a" * 64,
                "contract_archive_sha256": "b" * 64,
                "command": baseline_command,
                "command_sha256": hashlib.sha256(baseline_command.encode()).hexdigest(),
                "result": baseline_result.name,
                "result_sha256": hashlib.sha256(
                    baseline_result.read_bytes()
                ).hexdigest(),
                "provider_config_sha256": "c" * 64,
                "dirty": False,
                "tracked_clean_before": True,
                "tracked_clean_after": True,
                "network_calls": 0,
            }
        ),
        encoding="utf-8",
    )
    commands = {"focused": "focused command", "full": "full command"}
    candidate_manifest = {
        "version": "candidate-manifest/v1",
        "commit": commit,
        "tree": tree,
        "dirty": False,
        "contract_sha256": hashlib.sha256(
            (CONTRACT / "dataset.json").read_bytes()
        ).hexdigest(),
        "commands": commands,
        "command_sha256": {
            name: hashlib.sha256(command.encode()).hexdigest()
            for name, command in commands.items()
        },
        "output_root": str(tmp_path),
    }
    candidate.write_text(json.dumps(candidate_manifest), encoding="utf-8")
    command = [
        sys.executable,
        "scripts/run_dogfood_eval.py",
        "preflight",
        "--contract",
        str(CONTRACT),
        "--baseline-manifest",
        str(baseline),
        "--candidate-manifest",
        str(candidate),
    ]
    env = _eval_env()
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    assert result.returncode == 0
    preflight = json.loads(result.stdout)
    assert preflight["adapter_eligibility"] == "open"
    assert preflight["authorization_gate"] == "ready"
    assert preflight["candidate_tree"] == tree
    assert preflight["command_sha256"] == candidate_manifest["command_sha256"]
    assert preflight["output_root"] == candidate_manifest["output_root"]
    assert preflight["network_calls"] == 0

    packet = tmp_path / "pilot-packet.json"
    approval = subprocess.run(
        [
            sys.executable,
            "scripts/run_dogfood_eval.py",
            "approval-packet",
            "--scope",
            "pilot",
            "--contract",
            str(CONTRACT),
            "--baseline-manifest",
            str(baseline),
            "--candidate-manifest",
            str(candidate),
            "--output",
            str(packet),
        ],
        text=True,
        capture_output=True,
        env=env,
    )
    assert approval.returncode == 1
    assert "adapter_eligibility" in approval.stderr
    assert not packet.exists()

    candidate_manifest["commit"] = tree
    candidate.write_text(json.dumps(candidate_manifest), encoding="utf-8")
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    assert result.returncode == 1
    assert "commit object" in result.stderr

    candidate_manifest["commit"] = commit
    candidate_manifest["contract_sha256"] = "0" * 64
    candidate.write_text(json.dumps(candidate_manifest), encoding="utf-8")
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    assert result.returncode == 1
    assert "contract digest" in result.stderr

    result = subprocess.run(
        [sys.executable, "scripts/run_dogfood_eval.py", "pilot"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 1
    assert "adapter_eligibility is open" in result.stderr


def test_cli_local_writes_private_output_and_rejects_symlinked_root(tmp_path):
    manifest = tmp_path / "manifest.json"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest.write_text(
        json.dumps({"version": "build-manifest/v1", "commit": commit, "dirty": False}),
        encoding="utf-8",
    )
    output = tmp_path / "result.json"
    command = [
        sys.executable,
        "scripts/run_dogfood_eval.py",
        "local",
        "--contract",
        str(CONTRACT),
        "--build-manifest",
        str(manifest),
        "--output",
        str(output),
    ]
    env = _eval_env()
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    assert result.returncode == 0
    local_result = validate_result(output)
    assert local_result["task_id"] == "C1-R"
    assert local_result["provider_config_sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert output.stat().st_mode & 0o777 == 0o600

    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)
    command[-1] = str(linked_root / "result.json")
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    assert result.returncode == 1
    assert "symlinked output root" in result.stderr


def test_cli_local_rejects_unknown_build_commit(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"version": "build-manifest/v1", "commit": "a" * 40, "dirty": False}
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_dogfood_eval.py",
            "local",
            "--contract",
            str(CONTRACT),
            "--build-manifest",
            str(manifest),
            "--output",
            str(tmp_path / "result.json"),
        ],
        text=True,
        capture_output=True,
        env=_eval_env(),
    )
    assert result.returncode == 1
    assert "commit" in result.stderr

    parent_commit = subprocess.run(
        ["git", "rev-parse", "HEAD^"], check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest.write_text(
        json.dumps(
            {
                "version": "build-manifest/v1",
                "commit": parent_commit,
                "dirty": False,
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_dogfood_eval.py",
            "local",
            "--contract",
            str(CONTRACT),
            "--build-manifest",
            str(manifest),
            "--output",
            str(tmp_path / "parent-result.json"),
        ],
        text=True,
        capture_output=True,
        env=_eval_env(),
    )
    assert result.returncode == 1
    assert "HEAD" in result.stderr


def test_producer_manifest_rejects_changed_event_after_snapshot(tmp_path):
    module = _load_eval_runner()
    run_id = "99756532-a866-4e91-8480-47adac64778e"
    state = tmp_path / "run_state.json"
    state.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    events = tmp_path / "call_events.jsonl"
    events.write_bytes(b"current event bytes")
    manifest = tmp_path / "producer-manifest.json"
    manifest.write_text(
        json.dumps(_producer_manifest_payload(state, events, run_id)), encoding="utf-8"
    )
    manifest.chmod(0o600)

    with pytest.raises(module.ContractError, match="call-event digest"):
        module._producer_manifest(
            manifest,
            event_path=events,
            expected_run_id=run_id,
            event_bytes=b"snapshot taken before replacement",
        )


def test_producer_manifest_requires_trusted_attestation(tmp_path, monkeypatch):
    module = _load_eval_runner()
    run_id = "99756532-a866-4e91-8480-47adac64778e"
    state = tmp_path / "run_state.json"
    state.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    events = tmp_path / "call_events.jsonl"
    events.write_bytes(b"current event bytes")
    manifest = tmp_path / "producer-manifest.json"
    payload = _producer_manifest_payload(state, events, run_id)
    payload["producer_signature"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    manifest.chmod(0o600)

    with pytest.raises(module.ContractError, match="producer attestation"):
        module._producer_manifest(manifest, event_path=events, expected_run_id=run_id)

    payload = _producer_manifest_payload(state, events, run_id)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    manifest.chmod(0o600)
    monkeypatch.delenv("AI_ORCHESTRATION_PRODUCER_ATTESTATION_KEY")
    with pytest.raises(module.ContractError, match="attestation key"):
        module._producer_manifest(manifest, event_path=events, expected_run_id=run_id)


def test_producer_state_rejects_changed_snapshot(tmp_path):
    module = _load_eval_runner()
    run_id = "99756532-a866-4e91-8480-47adac64778e"
    state = tmp_path / "run_state.json"
    state.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")

    with pytest.raises(module.ContractError, match="producer state digest"):
        module._verify_producer_state(
            {
                "state_path": str(state),
                "state_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
            },
            run_id,
            state_bytes=b'{"run_id":"changed"}',
        )


def test_cli_local_candidate_fragment_validates_direct_routing_events(tmp_path):
    manifest = tmp_path / "manifest.json"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest.write_text(
        json.dumps({"version": "build-manifest/v1", "commit": commit, "dirty": False}),
        encoding="utf-8",
    )
    run_id = "99756532-a866-4e91-8480-47adac64778e"
    routes = [
        ("brainstormer", "direct_vendor", "gemini_api"),
        ("reviewer", "direct_vendor", "anthropic_api"),
        ("planner", "direct_vendor", "openai_api"),
        ("executor", "proxy_model", "claude-sonnet-5"),
    ]
    event_rows = [
        {
            "version": "call-event/v1",
            "run_id": run_id,
            "task_id": "C1-R",
            "executor_step_id": None,
            "stage": stage,
            "provider_kind": provider_kind,
            "provider_id": provider_id,
            "attempt": 1,
            "prompt_bytes": 20,
            "estimated_input_tokens": 5,
            "reported_input_tokens": 11 if provider_kind == "direct_vendor" else None,
            "reported_output_tokens": 5 if provider_kind == "direct_vendor" else None,
            "transport_retry_count": None,
            "latency_ms": 2,
            "outcome": "success",
            "error_kind": None,
        }
        for stage, provider_kind, provider_id in routes
    ]
    events = tmp_path / "events.jsonl"
    previous_run_id = "e5e4ef7d-23b1-4bcb-a7e2-c55cb2e7d6ef"
    previous_event_rows = [{**event, "run_id": previous_run_id} for event in event_rows]
    events.write_text(
        "".join(
            json.dumps(event) + "\n" for event in [*previous_event_rows, *event_rows]
        ),
        encoding="utf-8",
    )
    provider_config = {
        "brainstormer": "gemini_api",
        "reviewer": "anthropic_api",
        "planner": "openai_api",
        "executor": "claude-sonnet-5",
        "code_reviewer": "gpt-5.5",
        "fixer": "claude-sonnet-5",
    }
    expected = tmp_path / "expected.json"
    expected.write_text(
        json.dumps(
            {
                "version": "expected-attempts/v1",
                "task_id": "C1-R",
                "trial_id": "01",
                "run_id": run_id,
                "provider_config": provider_config,
                "attempts": [
                    {
                        "task_id": "C1-R",
                        "stage": stage,
                        "provider_id": provider_id,
                        "attempt": 1,
                    }
                    for stage, _provider_kind, provider_id in routes
                ],
            }
        ),
        encoding="utf-8",
    )
    state = tmp_path / "run_state.json"
    state.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    producer_manifest = tmp_path / "producer-manifest.json"
    producer_manifest.write_text(
        json.dumps(_producer_manifest_payload(state, events, run_id)), encoding="utf-8"
    )
    producer_manifest.chmod(0o600)
    output = tmp_path / "candidate-fragment.json"
    command = [
        sys.executable,
        "scripts/run_dogfood_eval.py",
        "local",
        "--contract",
        str(CONTRACT),
        "--build-manifest",
        str(manifest),
        "--call-events",
        str(events),
        "--expected-attempts",
        str(expected),
        "--producer-manifest",
        str(producer_manifest),
        "--output",
        str(output),
    ]
    env = _eval_env()

    result = subprocess.run(command, text=True, capture_output=True, env=env)

    assert result.returncode == 0, result.stderr
    fragment = validate_result(output)
    assert fragment["stability_checks"] == {
        "call_events_valid": True,
        "direct_vendor_observed": True,
        "synthetic": True,
    }
    assert fragment["artifact_manifest"] == {
        "call_event_count": 4,
        "call_events_sha256": hashlib.sha256(events.read_bytes()).hexdigest(),
        "direct_vendor_ids": ["anthropic_api", "gemini_api", "openai_api"],
        "expected_attempts_sha256": hashlib.sha256(expected.read_bytes()).hexdigest(),
        "producer_manifest_sha256": hashlib.sha256(
            producer_manifest.read_bytes()
        ).hexdigest(),
        "run_id": run_id,
        "state_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
    }
    assert (
        fragment["provider_config_sha256"]
        == hashlib.sha256(
            json.dumps(provider_config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert fragment["elapsed_ms"] == 8
    assert fragment["usage"] == {"input_tokens": 33, "output_tokens": 15}
    assert fragment["artifact_checks"]["sanitized"] is True

    state.write_text(json.dumps({"run_id": run_id, "tampered": True}), encoding="utf-8")
    command[-1] = str(tmp_path / "tampered-state.json")
    tampered = subprocess.run(command, text=True, capture_output=True, env=env)
    assert tampered.returncode == 1
    assert "producer state digest" in tampered.stderr
    state.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")

    expected_payload = json.loads(expected.read_text(encoding="utf-8"))
    for field, invalid_value in (
        ("task_id", 7),
        ("stage", []),
        ("provider_id", []),
        ("attempt", True),
        ("attempt", []),
    ):
        invalid_payload = json.loads(json.dumps(expected_payload))
        invalid_payload["attempts"][0][field] = invalid_value
        expected.write_text(json.dumps(invalid_payload), encoding="utf-8")
        command[-1] = str(tmp_path / f"invalid-{field}.json")
        result = subprocess.run(command, text=True, capture_output=True, env=env)
        assert result.returncode == 1
        assert f"expected attempt {field} is invalid" in result.stderr

    expected.write_text(json.dumps(expected_payload), encoding="utf-8")
    reserved_proxy_rows = [dict(event) for event in event_rows]
    reserved_proxy_rows[-1]["provider_id"] = "openai_api"
    events.write_text(
        "".join(json.dumps(event) + "\n" for event in reserved_proxy_rows),
        encoding="utf-8",
    )
    producer_payload = json.loads(producer_manifest.read_text(encoding="utf-8"))
    producer_payload["call_events_sha256"] = hashlib.sha256(
        events.read_bytes()
    ).hexdigest()
    producer_payload["producer_signature"] = producer_manifest_signature(
        producer_payload, TEST_PRODUCER_KEY
    )
    producer_manifest.write_text(json.dumps(producer_payload), encoding="utf-8")
    producer_manifest.chmod(0o600)
    expected_payload["attempts"][-1]["provider_id"] = "openai_api"
    expected_payload["provider_config"]["executor"] = "openai_api"
    expected.write_text(json.dumps(expected_payload), encoding="utf-8")
    command[-1] = str(tmp_path / "reserved-proxy.json")
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    assert result.returncode == 1
    assert "ordinary proxy observation" in result.stderr

    expected_payload["run_id"] = "not-a-uuid"
    expected.write_text(json.dumps(expected_payload), encoding="utf-8")
    command[-1] = str(tmp_path / "invalid-run.json")
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    assert result.returncode == 1
    assert "run_id must be a UUID" in result.stderr


def test_failed_live_trial_never_completes_generation(tmp_path):
    manifest = tmp_path / "manifest.json"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest.write_text(
        json.dumps({"version": "build-manifest/v1", "commit": commit, "dirty": False}),
        encoding="utf-8",
    )
    events = tmp_path / "events.jsonl"
    events.write_text("", encoding="utf-8")
    run_id = "99756532-a866-4e91-8480-47adac64778e"
    state = tmp_path / "run_state.json"
    state.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    producer_manifest = tmp_path / "producer-manifest.json"
    producer_manifest.write_text(
        json.dumps(_producer_manifest_payload(state, events, run_id)), encoding="utf-8"
    )
    producer_manifest.chmod(0o600)
    provider_config = {
        "brainstormer": "gemini_api",
        "reviewer": "anthropic_api",
        "planner": "openai_api",
        "executor": "claude-sonnet-5",
        "code_reviewer": "gpt-5.5",
        "fixer": "claude-sonnet-5",
    }
    expected = tmp_path / "expected.json"
    expected.write_text(
        json.dumps(
            {
                "version": "expected-attempts/v1",
                "task_id": "C1-R",
                "trial_id": "01",
                "run_id": run_id,
                "provider_config": provider_config,
                "attempts": [
                    {
                        "task_id": "C1-R",
                        "stage": stage,
                        "provider_id": provider_id,
                        "attempt": 1,
                    }
                    for stage, provider_id in (
                        ("brainstormer", "gemini_api"),
                        ("reviewer", "anthropic_api"),
                        ("planner", "openai_api"),
                        ("executor", "claude-sonnet-5"),
                    )
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "candidate-fragment.json"
    command = [
        sys.executable,
        "scripts/run_dogfood_eval.py",
        "local",
        "--contract",
        str(CONTRACT),
        "--build-manifest",
        str(manifest),
        "--call-events",
        str(events),
        "--expected-attempts",
        str(expected),
        "--producer-manifest",
        str(producer_manifest),
        "--output",
        str(output),
    ]
    env = _eval_env()

    result = subprocess.run(command, text=True, capture_output=True, env=env)

    assert result.returncode == 1
    assert "missing expected call events" in result.stderr
    assert not output.exists()


def test_contract_schema_files_are_strict_and_result_validator_rejects_content(
    tmp_path,
):
    for name in ("call-event.schema.json", "result.schema.json"):
        payload = json.loads((CONTRACT / name).read_text(encoding="utf-8"))
        assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert payload["additionalProperties"] is False
    result = {
        "version": "dogfood-result/v1",
        "task_id": "C1-R",
        "trial_id": "01",
        "build_commit": "a" * 40,
        "provider_config_sha256": "b" * 64,
        "expected_terminal": "observed",
        "observed_terminal": "observed",
        "comparable": True,
        "stability_checks": {},
        "test_passed": True,
        "rubric_scores": None,
        "elapsed_ms": 1,
        "usage": None,
        "cost_status": "not_measurable",
        "artifact_manifest": {},
        "artifact_checks": {},
        "response": "must not persist",
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ContractError, match="content field"):
        validate_result(path)


def test_live_profile_requires_explicit_authenticated_cli(monkeypatch):
    runner = _load_eval_runner()

    monkeypatch.delenv("AI_ORCHESTRATION_LIVE_BINARY", raising=False)
    assert runner._live_profile()["eligibility"] == "open"

    monkeypatch.setenv("AI_ORCHESTRATION_LIVE_BINARY", "claude")
    monkeypatch.setattr(runner, "_cli_auth_status", lambda name, path: True)
    profile = runner._live_profile()

    assert profile["eligibility"] == "closed"
    assert profile["binary_name"] == "claude"
    assert profile["binary_path"]
    assert profile["model"] == "cli-configured-default"


def test_live_command_is_argument_vector_with_bounded_stage_profile(tmp_path):
    runner = _load_eval_runner()

    command = runner._build_live_command(
        source_root=tmp_path / "source",
        workspace_root=tmp_path / "trial",
        project_name="c1_e_01",
        binary_path="/opt/homebrew/bin/claude",
    )

    assert isinstance(command, list)
    assert all(isinstance(value, str) for value in command)
    assert "--skip-review" in command
    assert command[command.index("--workspace") + 1] == str(tmp_path / "trial")
    for stage in (
        "brainstormer",
        "reviewer",
        "planner",
        "executor",
        "code-reviewer",
        "fixer",
    ):
        assert command[command.index(f"--{stage}") + 1] == "claude"
    assert "shell=True" not in command


def test_live_command_resumes_a_partial_trial(tmp_path):
    runner = _load_eval_runner()

    command = runner._build_live_command(
        source_root=tmp_path / "source",
        workspace_root=tmp_path / "trial",
        project_name="c1_e_01",
        binary_path="/opt/homebrew/bin/claude",
        resume=True,
    )

    assert "--resume" in command


def test_resumable_trial_rejects_failed_or_unplanned_state(tmp_path):
    runner = _load_eval_runner()
    project = tmp_path / "workspace" / "c1_e_01"
    metadata = project / ".ai_orchestration"
    metadata.mkdir(parents=True)
    state = {
        "completed_stages": [
            "brainstormer",
            "brainstorming_reviewer",
            "planner",
            "executor",
        ],
        "outputs": {"context": {}},
    }
    (metadata / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    (metadata / "call_events.jsonl").write_text(
        json.dumps({"outcome": "success"}) + "\n", encoding="utf-8"
    )

    assert runner._is_resumable_trial(tmp_path)

    state["completed_stages"] = ["brainstormer"]
    (metadata / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    assert not runner._is_resumable_trial(tmp_path)


def test_runner_observer_manifest_binds_snapshot_without_child_manifest(tmp_path):
    runner = _load_eval_runner()
    state = tmp_path / "run_state.json"
    events = tmp_path / "call_events.jsonl"
    manifest = tmp_path / "producer-manifest.json"
    state.write_text(
        json.dumps({"run_id": "31b98181-f6f6-4960-ba61-fe2bbbcc2500"}),
        encoding="utf-8",
    )
    events.write_text("snapshot\n", encoding="utf-8")

    runner._write_runner_observer_manifest(
        manifest,
        state_path=state,
        event_path=events,
        producer_key=TEST_PRODUCER_KEY,
    )

    producer = runner._producer_manifest(
        manifest,
        event_path=events,
        expected_run_id="31b98181-f6f6-4960-ba61-fe2bbbcc2500",
    )
    assert producer["state_sha256"] == hashlib.sha256(state.read_bytes()).hexdigest()


def test_child_failure_diagnostic_is_bounded_and_redacts_secret_patterns():
    runner = _load_eval_runner()

    diagnostic = runner._child_failure_diagnostic(
        b"prefix\nError: provider failed with api_token_"
        + b"A" * 24
        + b"\n"
        + b"x" * 20_000,
        b"",
    )

    assert diagnostic.startswith("Error: provider failed")
    assert "api_token_" not in diagnostic
    assert len(diagnostic) <= runner.MAX_CHILD_DIAGNOSTIC_BYTES


def test_invocation_packet_is_content_free(tmp_path):
    runner = _load_eval_runner()
    payload = b'["claude","--print","secret-prompt-marker"]'
    payload_path = tmp_path / "payload.bin"
    payload_path.write_bytes(payload)

    packet = runner._invocation_packet(
        {
            "version": "authorization-request/v1",
            "request_id": "request-1",
            "transport": "cli_argv",
            "payload_path": str(payload_path),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "destination": None,
            "timeout_seconds": None,
            "retry_limit": None,
        },
        session_id="session-1",
        socket_path=tmp_path / "auth.sock",
    )

    encoded = json.dumps(packet, sort_keys=True)
    assert "secret-prompt-marker" not in encoded
    assert packet["version"] == "next-invocation-packet/v1"
    assert packet["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert packet["scope"] == "invocation"


def test_invocation_packet_accepts_legacy_gate_request_shape(tmp_path):
    runner = _load_eval_runner()
    payload = b'["claude","--print","legacy-probe"]'
    payload_path = tmp_path / "payload.bin"
    payload_path.write_bytes(payload)

    packet = runner._invocation_packet(
        {
            "version": "authorization-request/v1",
            "request_id": "request-legacy",
            "transport": "cli_argv",
            "payload_path": str(payload_path),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
        },
        session_id="session-legacy",
        socket_path=tmp_path / "auth.sock",
    )

    assert packet["request_id"] == "request-legacy"
    assert packet["destination"] is None
    assert packet["timeout_seconds"] is None
    assert packet["retry_limit"] is None


def test_live_state_clears_stale_error_when_loading_new_batch(tmp_path):
    runner = _load_eval_runner()
    state_path = tmp_path / "live-eval-state.json"
    runner._private_write(
        state_path,
        {
            "version": "live-eval-state/v1",
            "completed_trials": [],
            "last_error": "OSError",
            "error_type": "ContractError",
        },
    )
    packet = {
        "session_id": "session-new",
        "scope": "pilot",
        "output_root": str(tmp_path),
    }

    state = runner._load_live_state(
        state_path,
        packet,
        SimpleNamespace(reservation_path=tmp_path / "batch-receipt.reserved"),
    )

    assert "last_error" not in state
    assert "error_type" not in state


def test_live_approval_timeout_is_separate_from_trial_timeout(monkeypatch):
    runner = _load_eval_runner()
    monkeypatch.setenv(runner.LIVE_TIMEOUT_ENV, "300")
    monkeypatch.setenv(runner.LIVE_APPROVAL_TIMEOUT_ENV, "7200")

    assert runner._live_timeout() == 300
    assert runner._live_approval_timeout() == 7200


def test_live_task_goal_does_not_conflict_with_raw_executor_output():
    runner = _load_eval_runner()

    assert "Return a concise plan and implement it." not in runner.LIVE_TASK_GOAL
    assert "return only the requested file content." in runner.LIVE_TASK_GOAL


def test_live_authorization_broker_relays_one_packet_bound_receipt(tmp_path):
    runner = _load_eval_runner()
    socket_path = runner._new_socket_path()
    payload_path = tmp_path / "payload.bin"
    next_packet_path = tmp_path / "next-invocation-packet.json"
    state_path = tmp_path / "live-eval-state.json"
    runner._private_write(
        state_path,
        {
            "version": "live-eval-state/v1",
            "session_id": "session-1",
            "completed_trials": [],
        },
    )
    broker = runner._AuthorizationBroker(
        socket_path=socket_path,
        next_packet_path=next_packet_path,
        payload_path=payload_path,
        state_path=state_path,
        session_id="session-1",
        model="cli-configured-default",
        timeout=5,
    )
    gate = SocketAuthorizationGate(socket_path, payload_path, timeout=5)
    broker.start()
    try:
        payload = OutboundPayload.for_cli_argv(["claude", "--print", "probe"])
        errors = []

        def authorize():
            try:
                gate.authorize(payload)
            except Exception as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        import threading

        thread = threading.Thread(target=authorize)
        thread.start()
        for _ in range(100):
            if next_packet_path.exists():
                break
            time.sleep(0.01)
        assert next_packet_path.exists()
        issued = datetime.now(timezone.utc)
        receipt = {
            "version": "approval-receipt/v1",
            "nonce": "a" * 32,
            "issuer": "user",
            "scope": "invocation",
            "packet_sha256": hashlib.sha256(next_packet_path.read_bytes()).hexdigest(),
            "session_id": "session-1",
            "issued_at": issued.isoformat(),
            "expires_at": (issued + timedelta(minutes=5)).isoformat(),
            "consumed_at": None,
        }
        receipt["approval_signature"] = receipt_signature(receipt, TEST_APPROVAL_KEY)
        receipt_path = tmp_path / "invocation-receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        receipt_path.chmod(0o600)
        authorization = runner._authorize_invocation(
            SimpleNamespace(
                packet=str(next_packet_path),
                receipt=str(receipt_path),
                state=str(state_path),
            )
        )
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors
        assert authorization["payload_sha256"] == payload.sha256
        assert not payload_path.exists()
    finally:
        broker.stop()


def test_live_artifact_manifest_rejects_secret_patterns(tmp_path):
    runner = _load_eval_runner()
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("api_token_" + "A" * 20, encoding="utf-8")

    with pytest.raises(ContractError, match="secret pattern"):
        runner._artifact_manifest(tmp_path)


def test_issue_receipt_binds_user_key_packet_and_scope(tmp_path):
    runner = _load_eval_runner()
    packet = tmp_path / "pilot-packet.json"
    packet.write_text(
        json.dumps(
            {
                "version": "approval-packet/v1",
                "scope": "pilot",
                "session_id": "session-1",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "pilot-receipt.json"

    issued = runner._issue_receipt(
        SimpleNamespace(
            packet=str(packet),
            scope="pilot",
            session_id="session-1",
            output=str(output),
        )
    )

    assert issued["packet_sha256"] == hashlib.sha256(packet.read_bytes()).hexdigest()
    assert output.stat().st_mode & 0o777 == 0o600
    reservation = consume_receipt(
        output,
        issued["packet_sha256"],
        "session-1",
        expected_scope="pilot",
    )
    assert reservation.scope == "pilot"
