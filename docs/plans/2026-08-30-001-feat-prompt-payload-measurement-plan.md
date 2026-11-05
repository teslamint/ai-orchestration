---
schema: plan/v1
title: Measure Prompt Payloads Before Message Migration
type: feat
status: approved
date: 2026-08-30
execution: code
origin: docs/specs/2026-08-30-prompt-payload-measurement-design.md
deepened: true
body_seal: 1730e5c7885b85f75d9ad83a327d8bdbb8dacc739715642c4a1ceb95447b4002
---

# Goal

Implement the approved Cycle 4 measurement deliverable over the existing `call-event/v1` stream. Keep prompts, stage messages, persisted context models, and stage order unchanged while producing a deterministic report and an explicit `no-migration` decision.

# Architecture notes

The measurement module consumes `read_call_events` and maps each validated `CallEvent` to aggregate rows. It does not inspect prompt content or introduce another recorder. The report ranks stage/provider groups by total prompt bytes, maximum prompt bytes, and stable lexical tie-breakers.

The CLI command reads one event file and writes one report with the existing private atomic-write pattern. The report includes the exact input digest, event count, usage availability counts, top groups, and decision metadata. Missing usage remains unavailable and never becomes an inferred cost.

The comparison tests use two fixture pairs. Identical event bytes must produce identical report bytes. A prompt-byte-only change must alter the ranking signal. A metadata-only change must leave the ranking signal unchanged. These checks prove that the measurement observes the declared axis.

# Assumption Recheck

Origin spec retains no live assumptions; no assumption recheck required.

# File structure

## Measurement core

- Create `src/ai_orchestration/evals/prompt_measurement.py` for the report model, event aggregation, deterministic ranking, and atomic report writer.
- Modify `src/ai_orchestration/evals/__init__.py` only if the package export is required by the CLI.

## Command surface and verification

- Create `scripts/measure_prompt_payloads.py` for argument validation and the read-only measurement command.
- Create `tests/test_prompt_measurement.py` for contract, ranking, determinism, redaction, and failure tests.
- Create `tests/integration/test_prompt_measurement.py` for the user-facing command path and real event fixture.

# Scenario coverage map

| Scenario | Ordered unit chain | Scenario evidence |
|---|---|---|
| S1 measure a recorded run | U1 → U2 → U3 | `tests/integration/test_prompt_measurement.py::test_measure_command_writes_ranked_report` **Covers S1** |
| S2 reject unsafe or unstable input | U1 → U2 | `tests/integration/test_prompt_measurement.py::test_measure_command_rejects_invalid_event_stream` **Covers S2** |
| S3 decide whether migration is justified | U1 → U2 → U3 | `tests/integration/test_prompt_measurement.py::test_report_records_no_migration_decision` **Covers S3** |

# Implementation Units

## U1: Aggregate content-free prompt measurements
Execution note: test-first

Files:
  Create: `src/ai_orchestration/evals/prompt_measurement.py`
  Modify: none
  Test: `tests/test_prompt_measurement.py`

Interfaces:
  Consumes: `Path` to a `call-event/v1` JSONL stream and existing `read_call_events(path: Path) -> list[CallEvent]`.
  Produces: `measure_prompt_payloads(path: Path) -> PromptMeasurementReport`, `PromptMeasurementReport.to_dict() -> dict[str, object]`, and `write_measurement_report(input_path: Path, output_path: Path) -> PromptMeasurementReport`.

Test scenarios:
  happy: aggregate every event, report total bytes and estimated tokens, and rank stage/provider groups deterministically. **Covers AE1**
  edge: preserve unavailable usage counts, handle an empty valid stream, and break equal rankings by stable fields.
  error: reject malformed, partial, unknown-version, and content-bearing events through the existing validator. **Covers AE2**
  integration: n/a — leaf unit

Steps:
  1. Write failing tests for aggregation, ranking, unavailable usage, and the two controlled comparison pairs.
  2. Run `UV_CACHE_DIR=/tmp/c4-uv-cache uv run pytest -q tests/test_prompt_measurement.py`; confirm the measurement module is absent.
  3. Implement the minimal immutable report model and aggregation over validated `CallEvent` fields.
  4. Run the focused tests; confirm identical inputs compare equal and the changed prompt-byte axis compares different.
  5. Commit: `feat(eval): measure recorded prompt payloads`

Acceptance: `UV_CACHE_DIR=/tmp/c4-uv-cache uv run pytest -q tests/test_prompt_measurement.py` passes, and no report field contains prompt content.

## U2: Add deterministic read-only CLI output
Execution note: test-first

Files:
  Create: `scripts/measure_prompt_payloads.py`
  Modify: none
  Test: `tests/test_prompt_measurement.py`

Interfaces:
  Consumes: `--call-events <path>` and `--output <path>`; both paths must be regular non-symlink files or valid output targets under non-symlink parents.
  Produces: exit `0` with a mode `0600` `prompt-measurement/v1` report, or exit `1` without a report for a contract or I/O error.

Test scenarios:
  happy: write one private report atomically and print the report JSON in stable key order. **Covers AE3**
  edge: repeat the command against identical bytes and compare output bytes exactly; preserve the source digest.
  error: refuse an unsafe input path, an invalid event stream, and a pre-existing output symlink without changing the target.
  integration: n/a — command boundary is covered by U3

Steps:
  1. Write failing subprocess tests for success, byte stability, output permissions, symlink rejection, and no-output-on-error.
  2. Run the tests; confirm the command is absent or returns the expected parser error.
  3. Implement argument validation, existing event loading, and the established private atomic-write pattern.
  4. Run the command tests and Ruff; confirm no network or model call occurs.
  5. Commit: `feat(cli): publish prompt measurement reports`

Acceptance: the success command writes a `0600` report with the exact input SHA-256, and every invalid-input case exits `1` without a new output file.

## U3: Dogfood the measurement report and record the migration decision
Execution note: test-first

Files:
  Create: `tests/integration/test_prompt_measurement.py`
  Create: `docs/evidence/2026-08-30-cycle4-prompt-measurement.md`
  Modify: none
  Test: `tests/integration/test_prompt_measurement.py`

Interfaces:
  Consumes: a real disposable `call-events.jsonl` fixture with multiple stages, providers, usage states, and byte sizes.
  Produces: a checked-in evidence report naming the largest groups, command output, input digest, and `decision: no-migration`.

Test scenarios:
  happy: the user-facing command produces a ranked report from the real event fixture. **Covers S1**
  edge: the report identifies a largest group without exposing its prompt bytes and records unavailable usage separately. **Covers S3**
  error: malformed fixture input leaves no published report. **Covers S2**
  integration: the fixed prompt measurement fixture proves the command is read-only and emits no network call. **Covers AE4**

Steps:
  1. Write the integration fixture and failing assertions for ranking, redaction, no-migration, and no-network behavior.
  2. Run `UV_CACHE_DIR=/tmp/c4-uv-cache uv run pytest -q tests/integration/test_prompt_measurement.py`; confirm the command/report path is not complete.
  3. Run the command against the fixture and capture the exact report digest and ranked rows in the evidence document.
  4. Review the report against the approved spec; record `no-migration` and the separate approval gate required before any adapter change.
  5. Commit: `test(eval): dogfood prompt measurement report`

Acceptance: the integration suite passes; the evidence names the largest payload group, records the source digest, proves no prompt content, and records `no-migration`.

# Mutation/failure-state matrix

| Transition | Pre-state | Action | Expected post-state | Owner | Evidence owner | Success | Forced failure | Rerun | Rollback or compensation | Headless | Cancellation or abort |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 report publication | Valid event stream and absent output | Read validated events and atomically publish one report | One readable mode `0600` report with source digest | U2 | U3 disposable fixture | Report bytes validate and ranking is present | Inject `os.replace` failure in a disposable output directory; no final report exists and temporary output is removed by U2 | Re-run with unchanged input produces byte-identical output | Remove only the disposable output and temporary files; source event bytes remain unchanged | Command runs without prompts or network calls | Interrupt before replacement leaves source unchanged and no final report; a later run starts from the unchanged source |

# Carry-forward trigger audit

- `teslamint/ai-orchestration#3` — event-based; Cycle 1 work started and remains an external live-eval dependency. Deferred because Cycle 4 consumes only its persisted event contract and does not alter live execution.
- `teslamint/ai-orchestration#4` — event-based; Cycle 3 implementation work exists. Deferred because embedded compound contracts are outside prompt measurement.
- `teslamint/ai-orchestration#5` — event-based; this Cycle 4 plan is the fired row. Fold as U1–U3 because its measurement and decision deliverables are the current scope.
- `teslamint/ai-orchestration#6` — event-based; no Cycle 5 implementation has started. Deferred because concurrent supervision is explicitly out of scope.

Audited GitHub issues #3–#6 at open state on 2026-08-30: 4 open rows, 1 fired, 0 unobservable.

# Deferred to Follow-Up Work

- Deliverable B versioned adapters remain deferred until a later approved migration decision.
- Prompt optimization, graph execution, clarification loops, and concurrent supervision remain outside this plan.

# Open unknowns

## Planning-time

None. The approved spec fixes the input event version, output version, ranking signal, decision value, and no-migration scope.

## Implementation-time

- The exact private atomic-write helper may be shared from the nearest existing module or kept local if import coupling would increase; the observable file contract remains fixed.
