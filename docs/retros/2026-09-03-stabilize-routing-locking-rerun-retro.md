# Retro: stabilize-routing-locking-rerun

- Date: 2026-09-03
- Source: ad-hoc post-closeout verification (no PR)
- Spec: docs/specs/2026-08-28-stabilize-routing-locking-design.md
- Plan: docs/plans/2026-08-28-001-feat-stabilize-routing-locking-plan.md

## Release data

| Metric | Value |
|---|---|
| **Changed non-test lines** | 6006 added / 115 removed (a39998d..0de811f) |
| Commits | 35 (a39998d..0de811f) |
| Review rounds (unit / final / standalone) | 6 (0 / 6 / 0) |
| Fix rounds | 0 |
| Internal findings (fixed / deferred) | 9 / 0 |
| Pull request comments (fixed / deferred) | 0 / 0 (no PR) |
| Count completeness | exact |
| CI failures | 0 recorded; no PR CI run was used |
| Duration (first spec commit → merge) | about 6 days (2026-08-28 → 2026-09-02) |
| Units planned / completed | 6 / 6 (U1–U6) |

## Success criteria: measured vs declared

Measurements ran fresh during this rerun against current `master` HEAD `9b3429d3eee78279eeb7fb0113dd84839ea84e62`.

| # | Declared criterion | Measurement (command / rubric) | Measured result | Verdict |
|---|---|---|---|---|
| 1 | The three legacy API literals select their direct-vendor adapters. | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_config.py tests/test_routing.py tests/test_legacy_api.py` | verified: 154 passed, 2 skipped in 2.11s | Met |
| 2 | Proxy model identifiers and exact CLI binary identifiers retain their existing behavior. | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_config.py tests/test_routing.py tests/test_legacy_api.py tests/test_providers.py` | verified: 228 passed, 2 skipped in 7.14s | Met |
| 3 | Missing direct-vendor credentials or extras cause typed failures before a network call. | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_legacy_api.py::test_validate_direct_vendor_rejects_missing_credential_before_socket_creation tests/test_legacy_api.py::test_validate_direct_vendor_rejects_only_selected_missing_extra` | verified: 6 passed in 0.24s; socket creation and unselected dependency probes were denied | Met |
| 4 | A second live process cannot mutate one project's held run state. | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/integration/test_live_run_lock.py`; before/after SHA-256 comparison of `run_state.json` | verified: 7 passed in 3.22s; the suite asserted unchanged durable state and no contender provider events | Met |
| 5 | A later process resumes after the first process releases the lock. | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/integration/test_live_run_lock.py` | verified: 7 passed in 3.31s; normal-release and forced-kill resume cases passed | Met |
| 6 | The full deterministic quality gate stays green. | `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q && UV_CACHE_DIR=/private/tmp/uv-cache uv run ruff check . && UV_CACHE_DIR=/private/tmp/uv-cache uv run ruff format --check .` | verified: 566 passed, 2 skipped in 25.40s; Ruff passed; 54 files already formatted | Met |
| 7 | Dogfooding has no stability failure across the repeated release corpus. | 9 of 9 candidate runs have no uncaught exception, secret disclosure, or corrupt state. | verified: V1 rubric applied to archived `evidence/V1/complete.json`; 9/9 candidate runs are stable and 18 matrix results are present | Met |
| 8 | Dogfooding output quality remains usable. | At least 8 of 9 candidate artifacts score at least 8 of 10, with no zero dimension. | verified: V1 rubric applied to archived `evidence/V1/complete.json`; 9/9 candidate artifacts are usable and zero rubric dimensions equal 0 | Met |
| 9 | Cost and latency remain bounded. | Candidate median latency stays within 25 percent of comparable baseline pairs, or a USER approves the recorded exception. Cost uses the same gate only for paired `C1-E` trials with reported usage and a pinned price snapshot; otherwise `cost_status` is `not_measurable`. | verified: V1 rubric applied to archived `evidence/V1/complete.json`; latency ratio is 0.8423195084485408 and passed, with 9 comparable pairs; cost status is `not_measurable` as permitted | Met |

## Carry-forward from previous retro

| Item | Status | Evidence |
|---|---|---|
| Publish V1 completion reports at the plan's canonical `evidence/V1/complete.json` path. | In progress | Archived `.release-loop/archive/2026-09-03-stabilize-routing-locking/evidence/V1/complete.json` proves the path exists, but the runner still needs automatic publication; `teslamint/ai-orchestration#3` remains open (T4) |
| Materialize and validate the missing deviation-process solution referenced by the approved plan. | Not started | `docs/solutions/workflow-issues/review-introduced-state-machine-deviation.md` is absent; `teslamint/ai-orchestration#3` remains open (T4) |
| Add the standing test-integrity review lane. | Not started | No protocol change landed in this repository; `teslamint/compound-loop#28` remains open (T4) |

- Reconciliation: registered 3, accounted for 3
- Previous doc shape: conformant

## Interview Transcript

- Independence level: same-model fresh-context
- Rounds used: 1

| ID | Round | Phase | Probe | Answer | Evidence | Verdict (verbatim) |
|---|---|---|---|---|---|---|
| T1 | 1 | 5 | What new evidence was acquired by rerunning this retrospective after the prior closeout? | The rerun confirmed archive-bound V1 evidence, terminal archive state, source-ledger absence, and commit lineage after the prior closeout. | Archived `evidence/V1/complete.json` is `status: complete` with 18 results. Fresh results report `archived`, `moved: []`; HEAD `9b3429d3...` descends from merge `0de811f3...`. | accepted |
| T2 | 1 | 5 | What did the archive verifier initially reject, and what exact artifact-bound correction made terminal archive verification pass? | The supplied artifacts do not record the initial rejection or the exact correction. | Archived `progress.md` records only final `V2 status: accepted` and revalidation of the report, source manifest, handoff marker, and base commit. | no evidenced answer (3 rejections): "The supplied artifacts prove only the final archived state; they do not prove the archive verifier's initial rejection or the exact artifact-bound correction." |
| T3 | 1 | 5 | Does the fresh report prove all nine criteria, including what remains explicitly not measurable? | Yes. Fresh command results prove criteria 1–6, and the report proves criteria 7–9. Cost remains explicitly `not_measurable`, as permitted by criterion 9. | Fresh results: 154/2 skipped, 228/2 skipped, 6 passed, 7 passed, 7 passed, and 566/2 skipped with Ruff passed; report: 9/9 stable, 9/9 usable, no zero dimensions, latency passed, ratio `0.8423195084485408`, 9 comparable pairs, `cost_status: not_measurable`. | accepted |
| T4 | 1 | 4 | How are every prior carry-forward item and every newly registered follow-up accounted for? | All three prior items are named and accounted for: API semantics Done, locking Done, and the test-integrity lane Not started under `compound-loop#28`. The three new follow-ups are tracked by `ai-orchestration#3` and `compound-loop#28`. | Prior retro carry-forward table and reconciliation state `registered 3, accounted for 3`; current follow-up table lists two items under `ai-orchestration#3` and one under `compound-loop#28`. | accepted |
| T5 | 1 | 5 | What would you change in the workflow based on the evidence? | Make canonical V1 report publication and archive revalidation mandatory terminal steps. Preserve `cost_status: not_measurable` as an explicit result. | Plan requires exact `evidence/V1/complete.json`; prior retro records rejection of the alternate report path; archived ledger records canonical report materialization and final V2 acceptance; fresh result records explicit cost non-measurability. | accepted |

## Findings

### What worked well

- **What happened**: All nine declared criteria were measured again after the prior closeout.
  **Why**: The rerun used the spec's commands and read the archived V1 rubric report.
  **How to apply**: Re-run declared commands when a retrospective is invoked directly after a completed lifecycle.
  **Cites**: T3; Phase 3 criteria 1–9
- **What happened**: The terminal archive verifier returned `archived` with `moved: []`, and the live source ledger remained absent.
  **Why**: The archive retained the terminal ledger, V1 report, ownership data, and handoff marker.
  **How to apply**: Make terminal archive verification idempotent and check both source absence and destination presence.
  **Cites**: T1; archived progress ledger; final archive verifier result
- **What happened**: The rerun accounted for every previous carry-forward row by name.
  **Why**: The current table retained completed work and unresolved tracker-owned work separately.
  **How to apply**: Reconcile carry-forward rows by exact item name before registering the next cycle.
  **Cites**: T4; previous retro and current carry-forward table

### What to improve

- **What happened**: The archived record proves final V2 acceptance but does not preserve the archive verifier's initial rejection or its exact correction.
  **Why**: The causal recovery trace was not recorded before terminalizing the archive.
  **How to apply**: Persist the failure reason, changed artifact digest, and successful rerun result in the terminal archive ledger.
  **Cites**: T2; archived `.release-loop/archive/2026-09-03-stabilize-routing-locking/progress.md`
- **What happened**: Canonical V1 publication remains a manual completion step for this runner.
  **Why**: The plan names the canonical path, but the current follow-up issue still owns automatic publication.
  **How to apply**: Publish `evidence/V1/complete.json` as part of the runner's completion transition.
  **Cites**: T4, T5; plan; `teslamint/ai-orchestration#3`
- **What happened**: The deviation-process solution and standing test-integrity lane remain absent.
  **Why**: This rerun verified the existing cycle but did not change the shared process contracts.
  **How to apply**: Close the two P2 tracker rows before using this cycle as evidence for future process changes.
  **Cites**: T4; missing solution path; `teslamint/ai-orchestration#3`; `teslamint/compound-loop#28`

### Process observations

- **What happened**: The report kept `cost_status: not_measurable` instead of inventing a price result.
  **Why**: The spec permits that state when usage and a pinned price snapshot are unavailable.
  **How to apply**: Treat non-measurability as an explicit measured result with its permitted condition.
  **Cites**: T3; Phase 3 criterion 9
- **What happened**: Code quality, process behavior, evidence integrity, and archive state were checked as separate claims.
  **Why**: A green test suite does not prove report identity or lifecycle persistence.
  **How to apply**: Keep code, evidence, and lifecycle gates separate in every closeout.
  **Cites**: T1, T3; Phase 3 criteria 6–9; archive verifier result

## Carry-forward items registered

| Item | Type | Priority | Tracked at |
|---|---|---|---|
| Publish V1 completion reports at the plan's canonical `evidence/V1/complete.json` path. | feature | P2 | [teslamint/ai-orchestration#3](https://github.com/teslamint/ai-orchestration/issues/3) |
| Materialize and validate the missing deviation-process solution referenced by the approved plan. | process | P2 | [teslamint/ai-orchestration#3](https://github.com/teslamint/ai-orchestration/issues/3) |
| Add the standing test-integrity review lane. | process | P2 | [teslamint/compound-loop#28](https://github.com/teslamint/compound-loop/issues/28) |

## Lessons

- A terminal archive can prove final bytes while omitting the causal trace of the failure that led to them; persist both before declaring V2 accepted.
- A passing full suite proves code behavior, while an idempotent archive check proves evidence lifecycle; completion requires both.
- `not_measurable` is a valid measurement state when the approved criterion permits missing usage and price data.

## Compounding

- compound invocation: Documentation complete — docs/solutions/workflow-issues/canonical-v1-completion-artifact.md
