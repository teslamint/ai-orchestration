---
title: S4 scenario-coverage-map row overstates test_review_fix_loops
type: deviation
status: recorded
date: 2026-08-26
origin: docs/plans/2026-08-25-001-feat-langchain-rewrite-plan.md
---

## Claim

The plan's Scenario coverage map, S4 row, states:

> `tests/integration/test_user_scenarios.py::test_review_fix_loops`; main loop and Ralph Wiggum
> fixtures exercise separate caps and acceptance rules (**Covers S4, Covers AE2**)

## Fresh command

```bash
uv run --active python -c "
import ast
tree = ast.parse(open('tests/integration/test_user_scenarios.py').read())
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'test_review_fix_loops':
        src = ast.get_source_segment(open('tests/integration/test_user_scenarios.py').read(), node)
        print('enable_ralph_wiggum' in src, '--enable-ralph-wiggum' in src)
"
```

## Observation

`test_review_fix_loops` (phase-gate review 2026-08-25T23:45:38Z, confirmed again on the
2026-08-26 remediation pass) invokes the CLI with `--auto-select --auto-run --auto-approve
--auto-fix` only. It never passes `--enable-ralph-wiggum`, so it exercises exactly one loop (the
main Stage 5->6 review/fix loop, asserting `review_calls["n"] == 1` under the default
`--max-fix-iterations`). The Ralph Wiggum loop's separate cap and acceptance rule
(`decision == ACCEPTED` or `confidence_score >= threshold`, default `max_attempts=3`) is unit
tested in `tests/test_loops.py` (e.g. `run_ralph_wiggum_loop` tests), not exercised at this
named integration seam.

## Outcome

`contradiction` for the claim that this test exercises both loops' separate caps.

## Resolution

The plan and its approval stand unchanged (this is a test-description drift, not a functional
gap: the Ralph Wiggum loop's cap/acceptance rules do have test coverage, just at the unit level
in `tests/test_loops.py` rather than the named integration test). No plan-body edit is made
because the plan is `status: approved` with a computed `body_seal`; this addendum is the
authoritative correction to the S4 row's evidence citation. Future scenario-coverage claims
should name the exact assertion (e.g. "asserts `review_calls['n'] == 1` under the main loop
only") rather than "exercise separate caps" when a single test only drives one of two loops.
