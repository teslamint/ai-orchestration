# Retro: langchain-rewrite (PR #2, package orchestration cutover)

- Date: 2026-08-27
- Source: PR #2
- Spec: docs/specs/2026-08-24-orchestration-rewrite-design-v2.md
- Plan: docs/plans/2026-08-25-001-feat-langchain-rewrite-plan.md

## Release data

| Metric | Value |
|---|---|
| **Changed non-test lines** | 5901 added / 3040 removed (tests: 6331 added / 593 removed; cbe5ac8..8bdaa84) |
| Commits | 38 (cbe5ac8..8bdaa84) |
| Review rounds | 4 phase-gate multi-lane rounds + 3 CodeRabbit PR-review rounds |
| Comments (fixed / deferred) | 11 / 6 (CodeRabbit threads; 4 declined in round 2, 2 more resolved in round 3) |
| CI failures | 2 rounds (fallback binaries treated as mandatory in clean CI; host-PATH-dependent mocked CLI routing) — both fixed at 8db534d and 9a1dae3 |
| Duration (first spec commit → merge) | ~1 day (spec 8345263 → merge 3545362, 2026-08-24 → 2026-08-27) |
| Units planned / completed | 6 / 6 (U1–U6) |

## Success criteria: measured vs declared

All measurements below were run fresh during this retro against the merged master
checkout at 3545362 (merge commit of PR #2).

| # | Declared criterion | Measurement (command / rubric) | Measured result | Verdict |
|---|---|---|---|---|
| 1 | All legacy-suite behaviour still passes; every legacy test name maps to a named successor | `uv run pytest -q`; `python3 docs/evidence/2026-08-26-verify-inventory-successors.py` | verified: 373 passed, 2 skipped; verifier reports 93 legacy rows, all successor tests resolve (integration tier) | Met |
| 2 | Every §Behaviour preservation inventory row lands in its named module with a mutation-verified test | Focused runs: `tests/test_loops.py -k ralph` (8 passed), `tests/test_stages.py` (27 passed incl. timeout/process-group), `tests/test_state.py`+`tests/test_gates.py` (41 passed), `tests/test_providers.py -k "build_command or positional"` (7 passed), heartbeat/stream tests in `tests/test_providers.py` (verified: all pass). Mutation evidence recorded in ledger during implementation (classify_slot, os.replace atomicity, gate fail-closed, retries=1 two-attempt, agy positional guard, resume skip guard) | verified: focused suites green; mutation-verified rows cited in progress.md ledger 2026-08-25 entries (unit tier + mutation evidence) | Met |
| 3 | Pipeline runs end to end through the proxy with per-stage model routing | Live: `uv run ai-orchestration "create a hello world script" --planner claude-sonnet-5 --skip-review --auto-run --auto-approve --workspace /tmp/retro-ae3 --project-name hello2` | verified: exit 0, `hello.py` written containing `print("Hello, World!")` (end-to-end tier, live CLIProxyAPI call) | Met |
| 4 | Fresh rerun is default; `--resume` skips completed stages | `tests/integration/test_user_scenarios.py::test_fresh_rerun_and_resume` (two real subprocesses); live resume attempt on hello3 confirmed pause state persisted and resumable | verified: integration test passes; live resume honored completed stages [brainstormer, brainstorming_reviewer, planner] and resumed at executor (integration tier) | Met |
| 5 | Typed data survives schema-ignoring models; unparseable output raises | `tests/test_providers.py` (extraction fallback + validation), `tests/test_routing.py` (24 passed), planner all-invalid-items StateError (540e31e) | verified: routing/provider suites green; unparseable planner output raises ModelFaultError/StateError (integration tier) | Met |
| 6 | Proxy unreachable → run completes via CLI fallback; missing fallback binary fails naming it | Live with `ORCHESTRATOR_PROXY_BASE_URL=http://127.0.0.1:9`: run completed exit 0 via fallback; `test_proxy_unreachable_cli_fallback` passes | verified: offline run completed via fallback binaries; integration test covers missing-binary terminal failure (integration tier) | Met |
| 7 | Model-level fault falls back to another model, never a subprocess | `tests/test_routing.py -k fallback` (12 passed: 429→fallback_model, CLI spy unused; no fallback_model → stage fails) | verified: routing tests green (integration tier) | Met |
| 8 | Each CLI provider uses its binary's own argv contract; agy never positional | `tests/test_providers.py -k "build_command or never_receives_a_positional"` (7 passed, mutation-verified during U3); live brainstormer stage ran through agy in the criterion-3 run | verified: argv tests green; live run used the real binaries (unit + end-to-end tier) | Met |
| 9 | Approval gates fail closed without the authorizing flag and proceed when authorized | `tests/integration/test_user_scenarios.py::test_non_tty_gate_requires_flag`; live run without `--auto-run` paused with the exact flag named and state resumable; with both flags proceeded | verified: both negative and positive paths exercised live and in tests (integration tier) | Met |
| 10 | Lint and format clean | `uv run ruff check .` and `uv run ruff format --check .` | verified: all checks passed; 84 files already formatted (build tier) | Met |

## Carry-forward from previous retro

| Item | Status | Evidence |
|---|---|---|
| (none — no previous retro doc exists in docs/retros/) | — | — |

- Reconciliation: registered 0, accounted for 0 — degraded: previous retro has no registration table
- Previous doc shape: no previous retro doc

## Interview Transcript

- Independence level: heterogeneous
- Rounds used: 1

| ID | Round | Phase | Probe | Answer | Evidence | Verdict (verbatim) |
|---|---|---|---|---|---|---|
| T1 | 1 | 5 | The ledger records 4 review rounds with verdicts BLOCKED three times before CLEAN. What structural change finally made the exception-escape class stop recurring, and what evidence shows it held? | Rounds 1–3 each reintroduced the same class (typed-boundary escapes reaching Typer's local renderer) at new call sites. Round 3's escalation made it structural: `typer.Typer(pretty_exceptions_show_locals=False)` plus a top-level unexpected-exception sanitizer, and a real-subprocess test planting CLIPROXYAPI_KEY that fails if the sentinel appears in rendered locals. Mutation evidence: reverting the flag to `typer.Typer()` made the planted-secret test fail with the sentinel exposed; restoring it passed. Subsequent rounds (including the final 363/373-test runs and CodeRabbit rounds) produced no new sentinel findings. | progress.md 2026-08-26T16:52:08Z mutation log; commits 941612a and later; final suite runs | accepted |
| T2 | 1 | 5 | Three of the five "wired-but-silently-discarded" findings from the original phase-gate review were found by review, not tests. What prevents the same class from shipping next time, concretely? | The pattern was unit tests monkeypatching the seam they don't own. Two structural defenses landed: (1) integration tests (test_user_scenarios.py, test_failure_matrix.py) that run the real default factories end-to-end for every config surface — resume config_snapshot validation, --ralph-wiggum-max-iterations threading, --auto-fix gate invocation are now covered by tests that fail if the wiring is dropped; (2) the review protocol now requires an adversarial lane that greps for parsed-but-unread config fields as an explicit checklist item. Evidence: round-2/3 reviews found no new instances of this class. | tests/integration/; ledger round-2/3 verdicts | accepted |
| T3 | 1 | 5 | The CodeRabbit PR review found 17 issues after three internal phase-gate rounds had declared the code CLEAN. What does that say about the internal review's blind spots? | The internal phase-gate reviews shared one reviewer family (same model lineage, same session context). CodeRabbit ran with independent static-analysis tooling (ast-grep, CWE checks, real subprocess probes like the live Google credential call) and found classes the internal lanes structurally could not see: live-network test leakage (verified via socket tracing), pytest shared-tmp-root lock contention, and fail-open evidence scripts. Defense: keep at least one reviewer that is heterogeneous in both model and method (static analysis + scripted probes), not just heterogeneous in model. | CodeRabbit comments 3868884021 (network call), 3868884029 (lock root), 3868883966 (verifier fail-open) | accepted |

## Findings

### What worked well

- **What happened**: The exception-boundary class that recurred across three review rounds was closed structurally (Typer local rendering disabled + top-level sanitizer) instead of by patching call sites, and the mutation test proved the guard load-bearing.
  **Why**: Per-site fixes kept being bypassed by newly missed exception paths reaching Rich's local renderer.
  **How to apply**: When the same defect class appears at 3+ call sites, stop and fix the mechanism, then mutation-test the mechanism itself.
  **Cites**: T1, progress.md 2026-08-26T16:52:08Z
- **What happened**: The base-topology gate caught the inherited local-only commit (71dc62c) before push, and the rebase path preserved user-owned untracked state in a named stash and restored it exactly.
  **Why**: `git rev-list --left-right --count` + inherited-count check ran before push; stash round-trip was verified by hash.
  **How to apply**: Always run the inherited-count check before pushing to an open PR; stash user-owned state with a named message, restore, and verify content hashes.
  **Cites**: progress.md 2026-08-27T03:02:12Z and 04:50:09Z entries
- **What happened**: The five remediation workers ran in parallel on disjoint file ownership with an explicit contract (RED/GREEN evidence required, no formatting/commit), and integrated cleanly with zero merge conflicts.
  **Why**: File ownership was partitioned up front (providers/cli vs engine/stages vs cli.py vs models/loops vs tests-only) and shared seams were called out in the batch context.
  **How to apply**: Partition parallel review-remediation by file, not by finding; state the integration owner's responsibilities (format, full suite, commit) explicitly.
  **Cites**: Phase 2 data (18-file diff integrated as 540e31e/0de958d/254c842)

### What to improve

- **What happened**: Three internal phase-gate review rounds each declared BLOCKED and produced remediation that introduced new P0s in adjacent code paths (round-2 found 4 new P0s inside the round-1 fixes; round-3 found 3 more), before a heterogeneous external reviewer returned CLEAN.
  **Why**: Incremental remediation audited only the fixed lines, not the new call sites/paths the fix itself created; same-model reviewers shared blind spots.
  **How to apply**: After each remediation pass, run the round's new-fix inventory against the conventions the same diff established (fail-closed, typed boundaries) before requesting re-review; budget for one heterogeneous final pass regardless of internal verdicts.
  **Cites**: T1, T2; ledger round-2/round-3 verdict entries
- **What happened**: The external reviewer (CodeRabbit) surfaced 17 findings — including test-integrity defects (live network calls in credential tests, lock tests contending on pytest's shared temp root, a fail-open verifier) — after 373 internal tests and three internal review rounds were green.
  **Why**: Test-quality defects don't fail suites; they silently weaken the evidence the suite claims to provide. No internal lane audited tests as *evidence* rather than as *passing*.
  **How to apply**: Add a standing review lane that audits test integrity (network isolation, filesystem contention, assertion specificity, fail-closed verification scripts) rather than only production correctness.
  **Cites**: T3; CodeRabbit comments 3868884021, 3868884029, 3868883966
- **What happened**: CI failed twice after merge-readiness (fallback binaries mandatory in clean environments; mocked CLI routing depending on host PATH), each requiring a fix commit before the PR could go green.
  **Why**: The local environment had all provider binaries installed, so "optional fallback" behavior was never exercised in a clean environment locally; unit tests monkeypatched PATH-sensitive seams.
  **How to apply**: When a behavior is environment-conditional, add a test that constructs the clean environment explicitly (missing binaries) rather than relying on the host.
  **Cites**: PR #2 CI runs; commits 8db534d, 9a1dae3; progress.md 2026-08-26T17:47:48Z and 17:59:40Z

### Process observations

- **What happened**: The CodeRabbit CLI free-review quota was exhausted mid-remediation, forcing the final committed delta through a bounded Codex review instead, and the PR-level reviewer later required manual invocation (`@coderabbitai review`) with an "incremental review" no-op on the third request.
  **Why**: Review-tool capacity wasn't budgeted across the loop's multiple review surfaces (committed-diff CLI review, PR review, re-reviews).
  **How to apply**: Track external review quota as a consumable in the ledger; sequence the most expensive review (full PR diff) last, after internal remediation converges.
  **Cites**: progress.md 2026-08-26T19:11:23Z; CodeRabbit reply comments 5436283006, 5437284301
- **What happened**: The plan's A12 deviation (agy version drift 1.1.18 → 1.1.19) and the shell-quoting defect in the deviation's verification command were both still present at merge and had to be fixed from PR review (254c842-era commits).
  **Why**: Deviation documents were reviewed for content but never executed; spec evidence rows were not re-verified against current reality before approval flip.
  **How to apply**: Execute every verification command recorded in a deviation doc at plan time; re-check factual claims (versions, dates) against the environment at Ship entry.
  **Cites**: CodeRabbit comments 3868883962, 3868883970; commits 0de958d-era docs fixes

## Carry-forward items registered

| Item | Type | Priority | Tracked at |
|---|---|---|---|
| Legacy `*_API` stage-slot values still classify as proxy models in `config.classify_slot`/routing (factory/validator fixed, stage routing not); decide and document intended semantics for `gemini_api`/`openai_api`/`anthropic_api` stage slots | feature | P2 | PR #2 thread 3868883974 (deferred with evidence); track in next cycle's plan |
| Per-run concurrency guard relies on flock on the state directory; no test covers two *live* concurrent orchestrator processes on the same project beyond unit lock tests | edge-case | P3 | tests/test_state.py lock suite; candidate for next cycle |
| Test-integrity review lane (network isolation, fs contention, fail-closed scripts) is not yet a standing review-lane checklist item | process | P3 | This retro's "What to improve" finding; track in compound-loop review protocol backlog |

## Lessons

- A green 373-test suite plus three internal review rounds still shipped 17 CodeRabbit findings, because none of the internal lanes audited *tests as evidence* (live network calls, shared-tmp-root contention, fail-open verifier scripts) — audit test integrity, not just test pass/fail.
- When the same exception-escape class recurs at new call sites after each fix, the fix location is wrong: disabling Typer's local rendering once (`pretty_exceptions_show_locals=False`) closed three rounds of per-site whack-a-mole — mechanisms beat call-site patches.
- CodeRabbit's incremental re-review is commit-keyed: after pushing fixes, a `@coderabbitai review` request can no-op ("does not re-review already reviewed commits") — verify the review actually ran by counting new review comments, not by the check's "Review completed" status.

## Compounding

- compound invocation: `Documentation complete — docs/solutions/review-introduced-state-machine-deviation.md` is superseded this cycle; this retro's structural lesson (review-introduced regressions in remediation diffs; heterogeneous-method review requirement) was filed as carry-forward items above. No new reusable-lesson document was warranted because the lessons are already captured in the ledger's escalation note (2026-08-26T15:57:06Z) and the items above.