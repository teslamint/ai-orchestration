---
schema: spec/v1
title: Measure Prompt Payloads Before Message Migration
type: feat
status: approved
date: 2026-08-30
---

# Goal

Measure the prompt payloads already recorded by `call-event/v1` and publish a deterministic report that identifies the largest payloads. This cycle does not change prompts, provider requests, persisted context models, or stage ordering.

# Scope

The measurement reads the existing content-free call-event stream. It reports prompt bytes, estimated input tokens, latency, reported usage coverage, and deterministic rankings by stage and provider. It never reconstructs or stores prompt content.

Deliverable A is complete when the report can be generated from a real event stream, has stable bytes for the same input, and records the evidence needed to decide whether a migration is warranted. The initial decision is `no-migration` unless the measured report and review approve a later change. Deliverable B is deferred because this cycle has no approved semantic migration.

# Contracts

- Input: validated `call-event/v1` JSON Lines produced by `CallRecorder`.
- Output: `prompt-measurement/v1` JSON with event digest, counts, aggregate metrics, and ranked payload groups.
- Ranking: descending total prompt bytes, then descending maximum prompt bytes, then ascending stage and provider ID. Ties remain deterministic.
- Missing reported usage remains `unavailable`; estimated tokens remain the content-free UTF-8 estimate already recorded by the event contract.
- A malformed, partial, unknown-version, or content-bearing event stream fails closed through the existing event validator.
- The measurement command is read-only with respect to the event stream and writes one private report atomically.

# Acceptance criteria

1. The report identifies the largest prompt payload groups from a real `call-event/v1` stream.
2. Every model call contributes one measurement row or fails the input contract; no second telemetry path exists.
3. Repeating the command with identical bytes produces identical report bytes.
4. A changed prompt-byte axis changes the ranked measurement signal, while a metadata-only change does not change that signal.
5. The report records `decision: no-migration` and names the explicit follow-up gate for any future versioned adapter work.
6. Existing `CodeReviewResult`, `RalphWiggumFeedback`, persisted state, stage order, and prompt templates remain unchanged.

# User scenarios

## S1: Measure a recorded run

An operator supplies a content-free call-event stream and receives a private report with counts, usage coverage, and ranked payload groups.

## S2: Reject unsafe or unstable input

An operator supplies malformed, partial, content-bearing, or unknown-version events. The command exits nonzero and does not publish a report.

## S3: Decide whether migration is justified

An operator reviews the ranked evidence. The report records `no-migration` for this cycle unless a separate approved design changes that decision.

# Risks

| Risk | Mitigation |
|---|---|
| Aggregates hide one unusually large prompt | Include maximum and top-ranked groups, not only means. |
| Missing provider usage looks like zero cost | Preserve `unavailable` counts and never infer cost. |
| A report leaks prompt content | Consume only content-free event fields and reject forbidden fields. |
| Ranking changes across runs | Hash the exact input bytes and use explicit tie-breakers. |

# Deferred to Follow-Up Work

- Versioned adapters for `CodeReviewResult` and `RalphWiggumFeedback` remain out of scope until a later approved migration decision.
- Prompt reduction, stage-message unification, graph execution, clarification loops, and concurrent supervision remain out of scope.
