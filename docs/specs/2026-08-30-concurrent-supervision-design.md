---
schema: spec/v1
title: Supervise Independent Orchestration Runs Concurrently
type: feat
status: draft
date: 2026-08-30
---

# Goal

Run independent orchestration commands concurrently while preserving one workspace, output stream, state identity, and terminal result per run. Keep the six stages inside each child sequential.

# Scope

The supervisor consumes a versioned JSON plan of explicit child commands. It validates unique run IDs, unique workspace paths, and bounded concurrency before dispatch. It captures each child stream under a run-specific output path, terminates owned process groups on cancellation, retries only an explicit rate-limit exit, and writes a terminal manifest with every run including `not_started` entries.

The CLI is non-interactive. Child commands receive their authorizations in the supplied plan. Interactive approval queueing and distributed workers are out of scope.

# Contracts

- Plan: `supervision-plan/v1` with unique `run_id`, unique workspace, and non-empty argv for each run.
- Manifest: `supervision-result/v1` with one status row per planned run.
- Statuses: `succeeded`, `failed`, `rate_limited`, `cancelled`, and `not_started`.
- Exit codes: `0` when every run succeeds, `1` when a run fails, `2` for pre-dispatch configuration failure, and `130` after cancellation.
- Default concurrency is `4`; the configured limit is positive and never exceeds the number of planned runs.
- Resume skips previously succeeded runs with matching run identity and reruns unfinished or failed runs in their isolated workspace.

# Acceptance criteria

1. Deterministic tests prove workspace, output, state, and failure isolation.
2. One failed or cancelled child does not terminate healthy siblings.
3. Every manifest row retains the correct run identity and terminal status.
4. In-flight children never exceed the configured concurrency limit.
5. Rate-limit retries are bounded and recorded per run.
6. Cancellation returns 130 and records cancelled/not-started rows; resume reruns unfinished rows without changing completed rows.
7. At least 30 supervised instances cover success, failure, cancellation, rate-limit, and resume classes in the dogfood evidence.

# User scenarios

## S1: Run isolated children concurrently

An operator supplies several non-interactive child commands and receives independent logs plus an aggregate manifest.

## S2: Survive mixed outcomes

One child fails or reaches a rate limit while healthy siblings finish. The supervisor preserves every result and returns a nonzero aggregate status.

## S3: Cancel and resume

An operator cancels a batch. The supervisor records terminal and not-started rows, then a resume reruns only unfinished rows.

# Risks

| Risk | Mitigation |
|---|---|
| A child writes into another run | Require unique workspace paths and launch each child with its own cwd. |
| A descendant survives cancellation | Start a new process session and terminate the owned process group. |
| Output becomes interleaved | Capture one file per run and aggregate only metadata. |
| Retry amplifies a rate limit | Bound retries and delay; record each attempt. |

# Deferred to Follow-Up Work

- Parallel stages within one run, interactive approval queueing, graph execution, distributed workers, and remote scheduling remain out of scope.
