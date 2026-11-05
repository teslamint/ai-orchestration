"""User-facing Cycle 4 prompt measurement scenarios."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from ai_orchestration.providers.call_events import CallRecorder

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "e5e4ef7d-23b1-4bcb-a7e2-c55cb2e7d6ef"


def _write_recorded_events(path: Path) -> bytes:
    recorder = CallRecorder(path, run_id=RUN_ID, task_id="C1-E")
    planner_one = recorder.start_attempt(
        stage="planner",
        provider_kind="proxy_model",
        provider_id="gpt-5.5",
        attempt=1,
        prompt="p" * 160,
    )
    planner_one.attach_usage({"input_tokens": 40, "output_tokens": 8})
    planner_one.finish(outcome="success", latency_ms=20)
    planner_two = recorder.start_attempt(
        stage="planner",
        provider_kind="proxy_model",
        provider_id="gpt-5.5",
        attempt=2,
        prompt="p" * 80,
    )
    planner_two.finish(outcome="success", latency_ms=10)
    executor = recorder.start_attempt(
        stage="executor",
        provider_kind="cli_binary",
        provider_id="claude",
        attempt=1,
        prompt="p" * 120,
    )
    executor.finish(outcome="success", latency_ms=30)
    return path.read_bytes()


def _command(source: Path, output: Path) -> list[str]:
    return [
        sys.executable,
        "scripts/measure_prompt_payloads.py",
        "--call-events",
        str(source),
        "--output",
        str(output),
    ]


def _run(source: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command(source, output),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": "src"},
    )


def test_measure_command_writes_ranked_report(tmp_path):
    source = tmp_path / "call_events.jsonl"
    output = tmp_path / "measurement.json"
    source_bytes = _write_recorded_events(source)

    result = _run(source, output)

    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert report["event_count"] == 3
    assert report["ranked_groups"][0]["stage"] == "planner"
    assert report["ranked_groups"][0]["provider_id"] == "gpt-5.5"
    assert report["ranked_groups"][0]["prompt_bytes_total"] == 240
    assert report["usage"] == {"available_events": 1, "unavailable_events": 2}
    assert "pppp" not in output.read_text(encoding="utf-8")


def test_report_records_no_migration_decision(tmp_path):
    source = tmp_path / "call_events.jsonl"
    output = tmp_path / "measurement.json"
    _write_recorded_events(source)

    result = _run(source, output)

    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["decision"] == "no-migration"
    assert "approved migration spec" in report["migration_gate"]


def test_measure_command_rejects_invalid_event_stream(tmp_path):
    source = tmp_path / "call_events.jsonl"
    output = tmp_path / "measurement.json"
    _write_recorded_events(source)
    with source.open("ab") as handle:
        handle.write(b'{"partial":')

    result = _run(source, output)

    assert result.returncode == 1
    assert "partial" in result.stderr.lower()
    assert not output.exists()
