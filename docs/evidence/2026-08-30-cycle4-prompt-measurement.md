# Cycle 4 prompt measurement evidence

- Spec: `docs/specs/2026-08-30-prompt-payload-measurement-design.md`
- Plan: `docs/plans/2026-08-30-001-feat-prompt-payload-measurement-plan.md`
- Source commit before U3 evidence: `fe2642b`
- Captured: `2026-08-30`
- Fixture: disposable `CallRecorder` event stream under `/private/tmp`
- External model calls: none

## User-facing scenarios

Command:

```text
UV_CACHE_DIR=/tmp/c4-uv-cache uv run pytest -q tests/integration/test_prompt_measurement.py
```

Result: `3 passed in 1.18s`.

The integration tests prove the following observable outcomes:

- `test_measure_command_writes_ranked_report`: the command writes a private report from three finalized `CallRecorder` events, identifies planner/gpt-5.5 as the largest group at 240 prompt bytes, and exposes no prompt content.
- `test_report_records_no_migration_decision`: the report records `decision: no-migration` and names the separate approved migration gate.
- `test_measure_command_rejects_invalid_event_stream`: a partial event tail exits `1` and leaves no report.

## Deterministic report sample

The disposable source stream contains three finalized events: planner/gpt-5.5 at 160 and 80 bytes, and executor/claude at 120 bytes. The measurement command produced:

```json
{
  "decision": "no-migration",
  "estimated_input_tokens_total": 90,
  "event_count": 3,
  "prompt_bytes_total": 360,
  "report_sha256": "d7535a3003dfe3b41de1f880ddca8271a2f5da55f1a079f638cbee8cbceed1e9",
  "source_sha256": "a8222f3fe5ad6416a7f9433a8ee55a47693e29589a8872db3033e38eb7829133",
  "top_group": {
    "call_count": 2,
    "estimated_input_tokens_total": 60,
    "latency_ms_total": 30,
    "prompt_bytes_max": 160,
    "prompt_bytes_total": 240,
    "provider_id": "gpt-5.5",
    "stage": "planner",
    "usage_available_events": 1
  },
  "usage": {
    "available_events": 1,
    "unavailable_events": 2
  }
}
```

The exact same source bytes produce the same report bytes. A prompt-byte-only fixture changes the ranking signal. A metadata-only fixture changes latency but leaves the ranking signal unchanged. The measurement reads only existing `call-event/v1` fields and introduces no second telemetry path.

## Decision

Cycle 4 selects `no-migration`. No prompt template, message model, persisted-state schema, stage order, or provider request changed. Any future `CodeReviewResult` or `RalphWiggumFeedback` adapter requires a separate approved migration spec and a repeated before-after evaluation.
