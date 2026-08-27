---
title: Executor task-level checkpointing extends stage checkpoint semantics
type: deviation
status: recorded
date: 2026-08-27
origin: docs/plans/2026-08-25-001-feat-langchain-rewrite-plan.md
---

## Original contract

The approved plan's state matrix specifies checkpoints at stage completion. It does not name a durable intermediate state within executor execution.

## Discovered contradiction

A later executor task can fail after an earlier non-idempotent task has completed. Stage-only persistence would rerun the already-successful task on `--resume`.

## Necessity

`OrchestrationContext.completed_executor_task_ids` is persisted after every successful executor task so a resumed executor skips only confirmed prior successes. Planner task IDs must be unique; `cli._run_planner()` rejects a duplicate `step_id` before execution, preventing a checkpoint from silently suppressing a distinct task.

## Observable behavior

A failure after task 1 and before task 2 preserves task 1's ID. Resume does not replay task 1, then continues at task 2. A duplicate planner ID fails before any executor task runs.

## Safety and consent boundary

This changes persistence only. It neither executes commands without the existing `--auto-run`/`--auto-approve` gates nor adds a user-consent gate.

## Verification changes

`tests/test_cli.py::test_resume_skips_only_checkpointed_executor_tasks_after_later_failure` verifies the durable checkpoint/replay boundary. The focused CLI regression suite verifies it together with planner validation.

## Traceability

Approved source: `docs/plans/2026-08-25-001-feat-langchain-rewrite-plan.md`, state matrix row "Stage completion checkpoint".
