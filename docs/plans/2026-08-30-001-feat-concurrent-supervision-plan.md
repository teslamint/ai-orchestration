---
schema: plan/v1
title: Supervise Independent Orchestration Runs Concurrently
type: feat
status: approved
date: 2026-08-30
execution: code
origin: docs/specs/2026-08-30-concurrent-supervision-design.md
deepened: true
body_seal: fe438dbfbd4402c2231cadb3826c7e96f49c0c74c088b5a4a10219e3fa91aad0
---

# Goal

Add a bounded, non-interactive supervisor for independent orchestration commands. Each child keeps its own workspace, state/output files, process group, and terminal result while the child stages remain sequential.

# Architecture notes

The supervisor is a library plus a thin CLI. The library validates the complete plan before dispatch, starts at most `max_concurrency` child process sessions, captures each child stream in a run-specific file, and owns only the process groups it starts. A failure becomes one run result and does not cancel siblings.

Rate-limit retry is limited to a plan-declared count and a bounded delay. The result row records attempt count and terminal status. Cancellation sets one shared event, terminates only owned process groups, marks pending runs `not_started`, and returns 130. Resume reads the existing manifest, skips matching succeeded rows, and dispatches only unfinished rows.

The manifest is written atomically after every terminal child result and once more after cancellation. It contains all plan run IDs, statuses, output paths, attempt counts, exit codes, and error summaries. It does not contain child output content.

# Assumption Recheck

Origin spec retains no live assumptions; no assumption recheck required.

# File structure

## Supervisor core

- Create `src/ai_orchestration/supervisor.py` for plan/result models, pre-dispatch validation, bounded process supervision, retry classification, cancellation, resume, and atomic manifest writes.

## Command surface and verification

- Create `scripts/supervise_orchestration.py` for JSON plan parsing, CLI exit mapping, and signal handling.
- Create `tests/test_supervisor.py` for validation, isolation, concurrency, retry, and lifecycle tests.
- Create `tests/integration/test_supervisor.py` for real subprocess scenarios and aggregate manifest checks.
- Create `docs/evidence/2026-08-30-cycle5-supervisor.md` for the 30-instance dogfood record.

# Scenario coverage map

| Scenario | Ordered unit chain | Scenario evidence |
|---|---|---|
| S1 isolated concurrent children | U1 → U2 → U3 | `tests/integration/test_supervisor.py::test_supervisor_isolates_concurrent_children` **Covers S1** |
| S2 mixed outcomes | U1 → U2 → U3 | `tests/integration/test_supervisor.py::test_supervisor_preserves_mixed_terminal_results` **Covers S2** |
| S3 cancellation and resume | U1 → U2 → U3 | `tests/integration/test_supervisor.py::test_supervisor_cancellation_resume_preserves_completed_runs` **Covers S3** |

# Implementation Units

## U1: Implement bounded process supervision
Execution note: test-first

Files:
  Create: `src/ai_orchestration/supervisor.py`
  Modify: none
  Test: `tests/test_supervisor.py`

Interfaces:
  Consumes: `SupervisionPlan`, `RunSpec`, `Path` output root, `max_concurrency`, and optional cancellation event.
  Produces: `SupervisionManifest`, `RunResult`, `validate_plan(plan)`, `run_supervised(plan, output_root, resume=False, cancel_event=None)`, and exit status classification.

Test scenarios:
  happy: run four short commands with concurrency two and prove the observed peak never exceeds two.
  edge: reject duplicate run IDs, duplicate workspaces, empty argv, non-positive limits, and a limit above the plan size before starting any child. **Covers AE1**
  error: one child exits nonzero while siblings finish; classify a bounded rate-limit retry and retain its attempt count. **Covers AE2, AE3**
  integration: n/a — process core is covered by U3

Steps:
  1. Write failing tests for plan validation, process-group ownership, bounded concurrency, sibling survival, rate-limit retries, and atomic result updates.
  2. Run `UV_CACHE_DIR=/tmp/c5-uv-cache uv run pytest -q tests/test_supervisor.py`; confirm the supervisor module is absent.
  3. Implement the minimal dataclasses, process worker, owned process-group cleanup, and terminal result aggregation.
  4. Run focused tests; confirm one failure does not cancel healthy siblings and peak concurrency stays within the limit.
  5. Commit: `feat(supervisor): supervise isolated runs`

Acceptance: focused tests prove pre-dispatch validation, bounded concurrency, sibling survival, and terminal result persistence.

## U2: Add non-interactive CLI, cancellation, and resume
Execution note: test-first

Files:
  Create: `scripts/supervise_orchestration.py`
  Modify: none
  Test: `tests/test_supervisor.py`

Interfaces:
  Consumes: `--plan`, `--output-root`, `--max-concurrency`, `--rate-limit-retries`, `--rate-limit-delay`, and `--resume`.
  Produces: exit `0` for all succeeded, `1` for any failed or rate-limited result, `2` for pre-dispatch configuration failure, and `130` after cancellation.

Test scenarios:
  happy: invoke the command with a valid plan and inspect one manifest row per run. **Covers AE4**
  edge: resume skips only matching succeeded rows and keeps their result bytes unchanged.
  error: malformed plan, unsafe workspace, and invalid concurrency exit `2` without starting a child or publishing a partial result.
  integration: n/a — real CLI flow is covered by U3

Steps:
  1. Write failing subprocess tests for CLI exit mapping, signal cancellation, not-started rows, resume selection, and non-interactive execution.
  2. Run the tests; confirm the command is absent or cannot produce the required manifest.
  3. Implement strict JSON plan parsing, signal-to-event handling, and the CLI-to-library exit mapping.
  4. Run subprocess tests and Ruff; confirm no interactive prompt is emitted.
  5. Commit: `feat(cli): add supervised run command`

Acceptance: CLI exit codes and manifest statuses match the approved contract for success, failure, configuration error, cancellation, and resume.

## U3: Dogfood 30 supervised instances and record evidence
Execution note: test-first

Files:
  Create: `tests/integration/test_supervisor.py`
  Create: `docs/evidence/2026-08-30-cycle5-supervisor.md`
  Modify: none
  Test: `tests/integration/test_supervisor.py`

Interfaces:
  Consumes: disposable local child commands representing success, failure, cancellation, rate-limit, and resume classes.
  Produces: a manifest covering at least 30 instances, per-class counts, peak concurrency, isolation checks, and sequential comparison.

Test scenarios:
  happy: concurrent children write only to their own workspaces and logs. **Covers S1**
  edge: failed, rate-limited, cancelled, and not-started rows coexist with successful siblings. **Covers S2**
  error: cancellation followed by resume preserves completed rows and reruns unfinished rows only. **Covers S3**
  integration: run 30 disposable instances and compare throughput with the same commands sequentially. **Covers AE5**

Steps:
  1. Write the real subprocess fixtures and failing assertions for isolation, mixed statuses, cancellation, resume, and the 30-instance class matrix.
  2. Run `UV_CACHE_DIR=/tmp/c5-uv-cache uv run pytest -q tests/integration/test_supervisor.py`; confirm the aggregate evidence is absent.
  3. Run the fixed corpus concurrently and sequentially; capture counts, latency, throughput, peak concurrency, and isolation observations.
  4. Write the evidence document with exact commands, results, and the confidence limitation that local fixtures do not measure vendor 429 behavior.
  5. Commit: `test(supervisor): dogfood concurrent run isolation`

Acceptance: integration tests pass, at least 30 instances are recorded, every terminal row is attributable, and concurrent throughput is at least 20 percent above the paired sequential fixture or the evidence records the unmet gate.

# Mutation/failure-state matrix

| Transition | Pre-state | Action | Expected post-state | Owner | Evidence owner | Success | Forced failure | Rerun | Rollback or compensation | Headless | Cancellation or abort |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 batch dispatch | Valid plan and empty output root | Validate and launch isolated child sessions | One result stream per run and no sibling path overlap | U1 | U3 disposable fixtures | All children finish with distinct rows and logs | Inject one child failure; siblings continue and its row is `failed` | Resume reads the manifest and skips matching succeeded rows | Remove only disposable output root after all owned processes exit | CLI supplies all child flags in the plan; no prompt is allowed | Signal sets cancellation, terminates owned groups, and writes cancelled/not-started rows |
| M2 rate-limit retry | Valid run spec with bounded retry policy | Retry only the declared rate-limit result | Attempt count and final status are persisted | U1 | U3 rate-limit class | Child succeeds within retry bound | Repeated rate-limit results stop at the bound and remain `rate_limited` | Resume reruns the unfinished run with a fresh attempt sequence | Keep prior result manifest for audit; no unbounded retry occurs | Retry policy is read from the plan | Cancellation interrupts delay and marks the run cancelled |

# Carry-forward trigger audit

- `teslamint/ai-orchestration#3` — event-based; Cycle 1 remains externally blocked on live model credentials. Deferred because this supervisor uses local non-interactive child fixtures and cannot create that authority.
- `teslamint/ai-orchestration#4` — event-based; Cycle 3 embedding exists. Deferred because compound phase contracts are not supervisor behavior.
- `teslamint/ai-orchestration#5` — event-based; Cycle 4 measurement is complete with no migration. Deferred because this cycle consumes its existing per-call metrics.
- `teslamint/ai-orchestration#6` — event-based; this plan is the fired row. Fold as U1–U3.

Audited GitHub issues #3–#6 at open state on 2026-08-30: 4 open rows, 1 fired, 0 unobservable.

# Deferred to Follow-Up Work

- Parallel stages within a run, interactive approval queueing, distributed workers, and remote scheduling remain out of scope.
- Vendor-specific live 429 confidence intervals remain unmeasured by local fixtures and require the Cycle 1 external-evidence gate.

# Open unknowns

## Planning-time

None. The spec fixes plan/result versions, statuses, exit codes, concurrency bound, retry behavior, and the 30-instance target.

## Implementation-time

- The exact child environment allowlist follows existing CLI subprocess conventions; the supervisor must preserve only the explicit non-interactive variables needed by each plan.
