import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_orchestration.evals.dogfood import (
    ContractError,
    ExpectedAttempt,
    TrialResult,
    compare_builds,
    consume_receipt,
    grade_artifact,
    load_eval_contract,
    validate_call_events,
    validate_result,
)

CONTRACT = Path("evals/dogfood/v1")


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


def _receipt(packet_sha256: str, session_id: str) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "version": "approval-receipt/v1",
        "nonce": "93c9c8762cbc4ecca3673ebc43a43e58",
        "issuer": "user",
        "scope": "pilot",
        "packet_sha256": packet_sha256,
        "session_id": session_id,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
        "consumed_at": None,
    }


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
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": "src"}
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
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": "src"}
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
        env={"PATH": os.environ["PATH"], "PYTHONPATH": "src"},
    )
    assert result.returncode == 1
    assert "commit" in result.stderr


def test_producer_manifest_rejects_changed_event_after_snapshot(tmp_path):
    module = _load_eval_runner()
    run_id = "99756532-a866-4e91-8480-47adac64778e"
    state = tmp_path / "run_state.json"
    state.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    events = tmp_path / "call_events.jsonl"
    events.write_bytes(b"current event bytes")
    manifest = tmp_path / "producer-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "producer-manifest/v1",
                "run_id": run_id,
                "state_path": str(state),
                "state_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
                "call_events_path": str(events),
                "call_events_sha256": hashlib.sha256(events.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    manifest.chmod(0o600)

    with pytest.raises(module.ContractError, match="call-event digest"):
        module._producer_manifest(
            manifest,
            event_path=events,
            expected_run_id=run_id,
            event_bytes=b"snapshot taken before replacement",
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
        json.dumps(
            {
                "version": "producer-manifest/v1",
                "run_id": run_id,
                "state_path": str(state),
                "state_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
                "call_events_path": str(events),
                "call_events_sha256": hashlib.sha256(events.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
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
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": "src"}

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


def test_cli_local_candidate_fragment_rejects_missing_direct_success(tmp_path):
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
                "run_id": "99756532-a866-4e91-8480-47adac64778e",
                "provider_config": provider_config,
                "attempts": [
                    {
                        "task_id": "C1-R",
                        "stage": "planner",
                        "provider_id": "openai_api",
                        "attempt": 1,
                    }
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
        "--output",
        str(output),
    ]
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": "src"}

    result = subprocess.run(command, text=True, capture_output=True, env=env)

    assert result.returncode == 1
    assert "producer-manifest" in result.stderr
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
