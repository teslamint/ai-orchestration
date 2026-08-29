"""Contract tests for content-free prompt payload measurement."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ai_orchestration.evals.prompt_measurement import (
    PromptMeasurementReport,
    measure_prompt_payloads,
    write_measurement_report,
)
from ai_orchestration.providers.call_events import CallEvent, CallEventError

RUN_ID = "99756532-a866-4e91-8480-47adac64778e"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _events(*, prompt_delta: int = 0, metadata_delta: int = 0) -> list[CallEvent]:
    return [
        CallEvent(
            run_id=RUN_ID,
            task_id="C1-E",
            executor_step_id=None,
            stage="planner",
            provider_kind="proxy_model",
            provider_id="gpt-5.5",
            attempt=1,
            prompt_bytes=160 + prompt_delta,
            estimated_input_tokens=(160 + prompt_delta + 3) // 4,
            reported_input_tokens=40,
            reported_output_tokens=8,
            transport_retry_count=None,
            latency_ms=20 + metadata_delta,
            outcome="success",
            error_kind=None,
        ),
        CallEvent(
            run_id=RUN_ID,
            task_id="C1-E",
            executor_step_id=None,
            stage="planner",
            provider_kind="proxy_model",
            provider_id="gpt-5.5",
            attempt=2,
            prompt_bytes=80,
            estimated_input_tokens=20,
            reported_input_tokens=None,
            reported_output_tokens=None,
            transport_retry_count=None,
            latency_ms=10,
            outcome="success",
            error_kind=None,
        ),
        CallEvent(
            run_id=RUN_ID,
            task_id="C1-E",
            executor_step_id=None,
            stage="executor",
            provider_kind="cli_binary",
            provider_id="claude",
            attempt=1,
            prompt_bytes=120,
            estimated_input_tokens=30,
            reported_input_tokens=None,
            reported_output_tokens=None,
            transport_retry_count=None,
            latency_ms=30,
            outcome="success",
            error_kind=None,
        ),
    ]


def _write_events(path: Path, events: list[CallEvent]) -> bytes:
    data = b"".join(
        json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")).encode()
        + b"\n"
        for event in events
    )
    path.write_bytes(data)
    return data


def test_measurement_ranks_groups_and_preserves_unavailable_usage(tmp_path):
    source = tmp_path / "call_events.jsonl"
    data = _write_events(source, _events())

    report = measure_prompt_payloads(source)

    assert isinstance(report, PromptMeasurementReport)
    assert report.to_dict()["version"] == "prompt-measurement/v1"
    assert report.to_dict()["source_sha256"] == hashlib.sha256(data).hexdigest()
    assert report.to_dict()["event_count"] == 3
    assert report.to_dict()["prompt_bytes_total"] == 360
    assert report.to_dict()["estimated_input_tokens_total"] == 90
    assert report.to_dict()["usage"] == {
        "available_events": 1,
        "unavailable_events": 2,
    }
    assert report.to_dict()["ranked_groups"][0] == {
        "stage": "planner",
        "provider_id": "gpt-5.5",
        "call_count": 2,
        "prompt_bytes_total": 240,
        "prompt_bytes_max": 160,
        "estimated_input_tokens_total": 60,
        "latency_ms_total": 30,
        "usage_available_events": 1,
    }


def test_measurement_is_byte_stable_and_observes_only_prompt_size(tmp_path):
    same_a = tmp_path / "same-a.jsonl"
    same_b = tmp_path / "same-b.jsonl"
    changed = tmp_path / "changed.jsonl"
    metadata_only = tmp_path / "metadata-only.jsonl"
    _write_events(same_a, _events())
    _write_events(same_b, _events())
    _write_events(changed, _events(prompt_delta=80))
    _write_events(metadata_only, _events(metadata_delta=99))

    first = measure_prompt_payloads(same_a).to_dict()
    second = measure_prompt_payloads(same_b).to_dict()
    changed_report = measure_prompt_payloads(changed).to_dict()
    metadata_report = measure_prompt_payloads(metadata_only).to_dict()

    first["source_sha256"] = second["source_sha256"]
    assert first == second
    ranking_fields = (
        "stage",
        "provider_id",
        "prompt_bytes_total",
        "prompt_bytes_max",
    )

    def ranking_signal(report):
        return [
            tuple(group[field] for field in ranking_fields)
            for group in report["ranked_groups"]
        ]

    assert ranking_signal(first) != ranking_signal(changed_report)
    assert ranking_signal(first) == ranking_signal(metadata_report)


def test_measurement_rejects_content_bearing_events(tmp_path):
    source = tmp_path / "unsafe.jsonl"
    payload = _events()[0].to_dict()
    payload["prompt"] = "secret prompt"
    source.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(CallEventError, match="unknown fields"):
        measure_prompt_payloads(source)


def test_measurement_writes_private_deterministic_report(tmp_path):
    source = tmp_path / "call_events.jsonl"
    output = tmp_path / "measurement.json"
    data = _write_events(source, _events())

    first = write_measurement_report(source, output)
    first_bytes = output.read_bytes()
    second = write_measurement_report(source, output)

    assert first.to_dict() == second.to_dict()
    assert output.read_bytes() == first_bytes
    assert output.stat().st_mode & 0o777 == 0o600
    assert hashlib.sha256(data).hexdigest() in output.read_text(encoding="utf-8")
    assert "secret prompt" not in output.read_text(encoding="utf-8")


def _measure_command(source: Path, output: Path) -> list[str]:
    return [
        sys.executable,
        "scripts/measure_prompt_payloads.py",
        "--call-events",
        str(source),
        "--output",
        str(output),
    ]


def test_measure_command_writes_private_stable_report(tmp_path):
    source = tmp_path / "call_events.jsonl"
    output = tmp_path / "measurement.json"
    _write_events(source, _events())

    first = subprocess.run(
        _measure_command(source, output),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": "src"},
    )
    assert first.returncode == 0, first.stderr
    first_bytes = output.read_bytes()
    second = subprocess.run(
        _measure_command(source, output),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": "src"},
    )

    assert second.returncode == 0, second.stderr
    assert output.read_bytes() == first_bytes
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(first.stdout)["decision"] == "no-migration"


def test_measure_command_rejects_invalid_input_without_publishing(tmp_path):
    source = tmp_path / "invalid.jsonl"
    output = tmp_path / "measurement.json"
    source.write_text("not-json\n", encoding="utf-8")

    result = subprocess.run(
        _measure_command(source, output),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": "src"},
    )

    assert result.returncode == 1
    assert "invalid" in result.stderr.lower()
    assert not output.exists()


def test_measure_command_rejects_output_symlink_without_touching_target(tmp_path):
    source = tmp_path / "call_events.jsonl"
    target = tmp_path / "target.json"
    output = tmp_path / "measurement.json"
    _write_events(source, _events())
    target.write_text("keep", encoding="utf-8")
    output.symlink_to(target)

    result = subprocess.run(
        _measure_command(source, output),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": "src"},
    )

    assert result.returncode == 1
    assert "symlink" in result.stderr.lower()
    assert target.read_text(encoding="utf-8") == "keep"
