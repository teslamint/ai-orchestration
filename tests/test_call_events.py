import json
import multiprocessing
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_orchestration.config import StageConfig
from ai_orchestration.providers.base import TransportError
from ai_orchestration.providers.call_events import (
    CallEvent,
    CallEventError,
    CallRecorder,
    estimate_input_tokens,
    read_call_events,
)
from ai_orchestration.providers.http import HttpProvider
from ai_orchestration.providers.routing import complete_with_fallback

RUN_ID = "99756532-a866-4e91-8480-47adac64778e"


def _record_attempt(path: str, attempt_number: int) -> None:
    recorder = CallRecorder(Path(path), run_id=RUN_ID, task_id="C1-E")
    attempt = recorder.start_attempt(
        stage="planner",
        provider_kind="proxy_model",
        provider_id=f"model-{attempt_number}",
        attempt=attempt_number,
        prompt="parallel prompt",
    )
    attempt.finish(outcome="success")


@pytest.mark.parametrize(
    ("prompt", "expected_bytes", "expected_tokens"),
    [
        ("", 0, 0),
        ("a", 1, 1),
        ("abcd", 4, 1),
        ("abcde", 5, 2),
        ("한", 3, 1),
        ("한글", 6, 2),
    ],
)
def test_content_free_event_uses_deterministic_utf8_estimate(
    tmp_path, prompt, expected_bytes, expected_tokens
):
    path = tmp_path / "call_events.jsonl"
    attempt = CallRecorder(path, run_id=RUN_ID).start_attempt(
        stage="brainstormer",
        provider_kind="cli_binary",
        provider_id="claude",
        attempt=1,
        prompt=prompt,
    )

    event = attempt.finish(outcome="success")

    assert event.prompt_bytes == expected_bytes
    assert event.estimated_input_tokens == expected_tokens
    assert estimate_input_tokens(prompt) == expected_tokens
    assert "prompt" not in {field.name for field in fields(CallEvent)}
    assert "response" not in {field.name for field in fields(CallEvent)}
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        field.name for field in fields(CallEvent)
    }


def test_nullable_metadata_and_usage_are_written_as_null(tmp_path):
    path = tmp_path / "call_events.jsonl"
    attempt = CallRecorder(path, run_id=RUN_ID).start_attempt(
        stage="reviewer",
        provider_kind="direct_vendor",
        provider_id="openai_api",
        attempt=1,
        prompt="secret prompt",
    )
    attempt.attach_usage(None)

    attempt.finish(outcome="error", error_kind="transport")

    [row] = read_call_events(path)
    assert row.task_id is None
    assert row.executor_step_id is None
    assert row.reported_input_tokens is None
    assert row.reported_output_tokens is None
    assert row.transport_retry_count is None
    assert "secret prompt" not in path.read_text(encoding="utf-8")


def test_reported_usage_accepts_transport_token_names(tmp_path):
    path = tmp_path / "call_events.jsonl"
    attempt = CallRecorder(path, run_id=RUN_ID).start_attempt(
        stage="executor",
        provider_kind="proxy_model",
        provider_id="gpt-test",
        attempt=1,
        prompt=b"bytes",
        executor_step_id=7,
    )
    attempt.attach_usage({"prompt_tokens": 12, "completion_tokens": 5})

    event = attempt.finish(outcome="success", transport_retry_count=2, latency_ms=9)

    assert event.reported_input_tokens == 12
    assert event.reported_output_tokens == 5
    assert event.transport_retry_count == 2
    assert event.latency_ms == 9


def test_attempt_usage_and_finish_each_accept_only_one_call(tmp_path):
    attempt = CallRecorder(tmp_path / "events.jsonl", run_id=RUN_ID).start_attempt(
        stage="fixer",
        provider_kind="cli_binary",
        provider_id="claude",
        attempt=1,
        prompt="prompt",
    )
    attempt.attach_usage({"input_tokens": 1, "output_tokens": 2})

    with pytest.raises(CallEventError, match="usage.*already"):
        attempt.attach_usage(None)

    attempt.finish(outcome="success")
    with pytest.raises(CallEventError, match="already finished"):
        attempt.finish(outcome="success")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"run_id": "not-a-uuid"}, "run_id"),
        ({"stage": "unknown"}, "stage"),
        ({"stage": []}, "stage"),
        ({"provider_kind": "unknown"}, "provider_kind"),
        ({"attempt": True}, "attempt"),
        ({"outcome": "success", "error_kind": "provider"}, "error_kind"),
        ({"outcome": "error", "error_kind": None}, "error_kind"),
        ({"reported_input_tokens": -1}, "reported_input_tokens"),
    ],
)
def test_event_validation_fails_closed(changes, message):
    values = {
        "version": "call-event/v1",
        "run_id": RUN_ID,
        "task_id": None,
        "executor_step_id": None,
        "stage": "planner",
        "provider_kind": "proxy_model",
        "provider_id": "gpt-test",
        "attempt": 1,
        "prompt_bytes": 4,
        "estimated_input_tokens": 1,
        "reported_input_tokens": None,
        "reported_output_tokens": None,
        "transport_retry_count": None,
        "latency_ms": 1,
        "outcome": "success",
        "error_kind": None,
    }
    values.update(changes)

    with pytest.raises(CallEventError, match=message):
        CallEvent(**values)


def test_read_rejects_unknown_fields_and_malformed_complete_rows(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"version": "call-event/v1", "prompt": "leak"}) + "\n")

    with pytest.raises(CallEventError, match="unknown fields|missing fields"):
        read_call_events(path)

    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(CallEventError, match="line 1"):
        read_call_events(path)


def test_process_safe_append_and_partial_tail_recovery(tmp_path):
    path = tmp_path / "call_events.jsonl"
    _record_attempt(str(path), 1)
    partial = b'{"version":"call-event/v1"'
    with path.open("ab") as handle:
        handle.write(partial)

    process_count = 8
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_record_attempt, args=(str(path), number))
        for number in range(2, process_count + 2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    rows = read_call_events(path)
    assert len(rows) == process_count + 1
    assert {row.attempt for row in rows} == set(range(1, process_count + 2))
    assert path.read_bytes().endswith(b"\n")
    quarantine = list(tmp_path.glob("call_events.jsonl.partial.*"))
    assert len(quarantine) == 1
    assert quarantine[0].read_bytes() == partial


def test_append_failure_warns_and_next_attempt_recovers_partial_tail(
    tmp_path, monkeypatch
):
    path = tmp_path / "call_events.jsonl"
    recorder = CallRecorder(path, run_id=RUN_ID)
    original_write = recorder._write_all
    interrupted = False

    def interrupt_once(fd, payload):
        nonlocal interrupted
        if not interrupted and payload.endswith(b"\n"):
            interrupted = True
            original_write(fd, payload[:11])
            raise OSError("forced append interruption")
        original_write(fd, payload)

    monkeypatch.setattr(recorder, "_write_all", interrupt_once)
    first = recorder.start_attempt(
        stage="planner",
        provider_kind="proxy_model",
        provider_id="gpt-test",
        attempt=1,
        prompt="prompt",
    )
    with pytest.warns(RuntimeWarning, match="failed to record call event"):
        first.finish(outcome="success")

    monkeypatch.setattr(recorder, "_write_all", original_write)
    second = recorder.start_attempt(
        stage="planner",
        provider_kind="proxy_model",
        provider_id="gpt-test",
        attempt=2,
        prompt="prompt",
    )
    second.finish(outcome="success")

    rows = read_call_events(path)
    assert [row.attempt for row in rows] == [2]
    quarantine = list(tmp_path.glob("call_events.jsonl.partial.*"))
    assert len(quarantine) == 1
    assert quarantine[0].read_bytes() == b'{"version":'


def test_routing_records_primary_and_fallback_attempts_in_order(tmp_path):
    recorder = CallRecorder(tmp_path / "events.jsonl", run_id=RUN_ID, task_id="C1-R")

    class Provider:
        def __init__(self, value, error=None):
            self.value = value
            self.error = error

        def complete(self, _prompt, **_kwargs):
            if self.error:
                raise self.error
            return self.value

    stage = StageConfig(model="primary", fallback_binary="codex")
    result, provider_id = complete_with_fallback(
        stage,
        "secret prompt",
        http_provider_factory=lambda _model: Provider(None, TransportError("offline")),
        cli_provider_factory=lambda _binary: Provider("ok"),
        recorder=recorder,
        stage_name="planner",
    )

    assert (result, provider_id) == ("ok", "codex")
    events = recorder.read_events()
    assert [(event.provider_id, event.attempt, event.outcome) for event in events] == [
        ("primary", 1, "error"),
        ("codex", 2, "success"),
    ]
    assert "secret prompt" not in recorder.path.read_text(encoding="utf-8")


def test_direct_routing_finalizes_one_content_free_usage_event(tmp_path):
    recorder = CallRecorder(tmp_path / "events.jsonl", run_id=RUN_ID, task_id="C1-R")

    class DirectProvider:
        usage_sink = None

        def complete(self, _prompt, **_kwargs):
            self.usage_sink({"prompt_tokens": 13, "completion_tokens": 8})
            return "ok"

    result, provider_id = complete_with_fallback(
        StageConfig(model="anthropic_api"),
        "candidate secret prompt",
        http_provider_factory=lambda _model: pytest.fail("used proxy factory"),
        cli_provider_factory=lambda _binary: pytest.fail("used CLI factory"),
        direct_provider_factory=lambda _tool_type: DirectProvider(),
        recorder=recorder,
        stage_name="planner",
    )

    assert (result, provider_id) == ("ok", "anthropic_api")
    [event] = recorder.read_events()
    assert (
        event.task_id,
        event.provider_kind,
        event.provider_id,
        event.reported_input_tokens,
        event.reported_output_tokens,
        event.outcome,
    ) == ("C1-R", "direct_vendor", "anthropic_api", 13, 8, "success")
    persisted = recorder.path.read_text(encoding="utf-8")
    assert "candidate secret prompt" not in persisted


def test_http_provider_reports_only_available_sdk_usage():
    observed = []
    provider = HttpProvider(
        model="gpt-test",
        base_url="http://127.0.0.1:1/v1",
        api_key="test",
        usage_sink=observed.append,
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(prompt_tokens=7),
    )
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: response)
        )
    )

    assert provider.complete("secret") == "ok"
    assert observed == [{"input_tokens": 7}]
