---
title: POSIX-only process and locking primitives
type: deviation
status: recorded
date: 2026-08-27
origin: docs/plans/2026-08-25-001-feat-langchain-rewrite-plan.md
---

## Original contract

The approved plan did not state an operating-system constraint for run locking or timed CLI child cleanup.

## Discovered contradiction

The implementation uses `fcntl.flock` for process-safe run exclusion and `os.killpg` for timeout cleanup of CLI process groups. Those primitives require POSIX.

## Necessity

Both primitives are required to prevent concurrent checkpoint corruption and orphaned CLI descendants on deadline expiry.

## Observable behavior

The package supports POSIX hosts. On unsupported platforms, import or execution is not promised by this release.

## Safety and consent boundary

No external target or approval boundary changes.

## Verification changes

`tests/test_state.py` covers lock exclusion and state-directory recreation. `tests/test_providers.py` covers timeout process-group cleanup.

## Traceability

Implementation: `src/ai_orchestration/engine/state.py` and `src/ai_orchestration/providers/cli.py`; public documentation: `README.md`.
