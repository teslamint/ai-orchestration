# Cycle 5 Evidence: Concurrent Supervision

Date: 2026-08-30
Branch: `feat/supervise-orchestration-runs`
Plan: `docs/plans/2026-08-30-001-feat-concurrent-supervision-plan.md`
Plan seal: `fe438dbfbd4402c2231cadb3826c7e96f49c0c74c088b5a4a10219e3fa91aad0`

## Contract

The supervisor consumes `supervision-plan/v1` and publishes
`supervision-result/v1`. It validates the complete plan before dispatch,
limits active child process sessions, isolates workspaces and logs, preserves
healthy siblings after a failure, bounds rate-limit retries, and records
cancellation and resume state.

## Verification commands

Focused dogfood:

```text
UV_CACHE_DIR=/tmp/c5-uv-cache uv run pytest -q tests/integration/test_supervisor.py
....                                                                     [100%]
4 passed in 5.03s
```

Full repository gate before the U1 commit:

```text
UV_CACHE_DIR=.uv-cache uv run pytest -q
562 passed, 2 skipped in 21.06s
```

The metric harness invoked the same real subprocess fixtures as the
integration test. It ran one 30-instance corpus with concurrency 1 and 6,
then ran the cancellation and resume fixture.

## Observed results

```text
matrix_instances: 30
sequential_seconds: 3.313
concurrent_seconds: 0.593
throughput_ratio_sequential_over_concurrent: 5.59
peak_concurrency: 6
sequential_counts: succeeded=22, failed=4, rate_limited=4
concurrent_counts: succeeded=22, failed=4, rate_limited=4
cancelled_counts: succeeded=1, cancelled=1, not_started=2
cancelled_exit_code: 130
resumed_counts: succeeded=4
resumed_exit_code: 0
completed_row_preserved: true
completed_log_preserved: true
total_instances_including_cancel_resume: 34
```

The concurrent fixture exceeded the required 20 percent throughput gain.
Every concurrent row retained a distinct run identity, workspace marker, and
output log. The failed and rate-limited rows did not cancel successful
siblings. The rate-limit fixture used exit code 75 and one bounded retry;
the four terminal rate-limited rows each recorded two attempts.

The cancellation fixture used one active child and two pending children. The
active process group was terminated, pending rows were `not_started`, and the
completed row and its log were byte-preserved across resume. Resume reran only
unfinished rows.

## Confidence boundary

This evidence proves local process-group ownership, isolation, aggregate
status, bounded concurrency, retry accounting, cancellation, and resume. It
does not prove behavior against a live provider's HTTP 429 response or a live
orchestration model. That evidence remains part of Cycle 1's external runner
gate.
