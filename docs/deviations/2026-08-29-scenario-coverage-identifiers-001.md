---
title: Stabilize routing locking scenario coverage identifier correction
type: deviation
status: recorded
date: 2026-08-29
origin: docs/plans/2026-08-28-001-feat-stabilize-routing-locking-plan.md
---

## Claim

The approved Scenario coverage map and T1-T6 matrix name several test identifiers
that are absent from the current test tree.

## Fresh command

```bash
rg -n '^def test_|^async def test_' tests/test_routing.py \
  tests/integration/test_user_scenarios.py tests/integration/test_live_run_lock.py \
  tests/test_dogfood_eval.py tests/test_state.py tests/test_call_events.py
```

## Observation

The current evidence seams are:

- S2: `test_mixed_missing_anthropic_fails_after_one_probe_without_provider_event`.
- S3: `test_resolve_provider_chain_binary_model_returns_cli_provider`,
  `test_resolve_provider_chain_proxy_model_returns_http_provider`, and the
  routing fallback tests in `tests/test_routing.py`.
- T1: `test_save_state_is_atomic_via_os_replace` and
  `test_save_state_failure_before_replace_leaves_prior_state_valid`.
- T3: `test_process_safe_append_and_partial_tail_recovery` and
  `test_append_failure_warns_and_next_attempt_recovers_partial_tail`.
- T6: live generation remains a first-hand V1 ceremony. No automated test may
  substitute for its external approval, receipt, provider, and grading evidence.

## Outcome

The plan's behavior and acceptance criteria remain unchanged. The identifiers
above are the authoritative evidence map for this release-loop run.

## Resolution

No approved plan-body edit is made because the plan is sealed. This addendum
corrects documentation drift only. The named tests and the first-hand V1
ceremony must both be present before the release can pass its corresponding
gate.
