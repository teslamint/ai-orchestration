# Retro: stabilize-routing-locking

- Date: 2026-09-03
- Source: local merge (no PR)
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

Measurements below ran fresh during this Retro against merged `master` at `0de811f320f98ff038cf06d0c2b17ac6e4d7047d`.

| # | Declared criterion | Measurement (command / rubric) | Measured result | Verdict |
|---|---|---|---|---|
| 1 | The three legacy API literals select their direct-vendor adapters. | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_config.py tests/test_routing.py tests/test_legacy_api.py` | verified: 154 passed, 2 skipped in 1.81s | Met |
| 2 | Proxy model identifiers and exact CLI binary identifiers retain their existing behavior. | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_config.py tests/test_routing.py tests/test_legacy_api.py tests/test_providers.py` | verified: 228 passed, 2 skipped in 7.13s | Met |
| 3 | Missing direct-vendor credentials or extras cause typed failures before a network call. | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_legacy_api.py::test_validate_direct_vendor_rejects_missing_credential_before_socket_creation tests/test_legacy_api.py::test_validate_direct_vendor_rejects_only_selected_missing_extra` | verified: 6 passed in 0.22s; socket creation and unselected dependency probes were denied | Met |
| 4 | A second live process cannot mutate one project's held run state. | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/integration/test_live_run_lock.py` | verified: 7 passed in 3.25s; the suite asserted unchanged durable state and no contender provider events | Met |
| 5 | A later process resumes after the first process releases the lock. | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/integration/test_live_run_lock.py` | verified: 7 passed in 3.25s; normal-release and forced-kill resume cases passed | Met |
| 6 | The full deterministic quality gate stays green. | `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q && UV_CACHE_DIR=/private/tmp/uv-cache uv run ruff check . && UV_CACHE_DIR=/private/tmp/uv-cache uv run ruff format --check .` | verified: 566 passed, 2 skipped in 23.90s; Ruff passed; 54 files already formatted | Met |
| 7 | Dogfooding has no stability failure across the repeated release corpus. | `.release-loop/runs/stabilize-routing-locking/evidence/V1/complete.json` checks | verified: complete report records candidate stability 9/9 and all 18 matrix results are present | Met |
| 8 | Dogfooding output quality remains usable. | `.release-loop/runs/stabilize-routing-locking/evidence/V1/complete.json` rubric | verified: candidate usable artifacts 9/9; no rubric dimension is zero | Met |
| 9 | Cost and latency remain bounded. | `.release-loop/runs/stabilize-routing-locking/evidence/V1/complete.json` comparison report | verified: latency ratio 0.8423195084485408 and latency passed; cost status is explicitly `not_measurable` as allowed by the criterion | Met |

## Carry-forward from previous retro

| Item | Status | Evidence |
|---|---|---|
| Legacy `*_API` stage-slot semantics | Done | Merge `0de811f320f98ff038cf06d0c2b17ac6e4d7047d`; focused routing/provider tests passed (T7) |
| Live concurrent-process locking | Done | `tests/integration/test_live_run_lock.py` passed 7 tests; candidate C1-L is 6/6 in the V1 report (T7) |
| Standing test-integrity review lane | Not started | No compound-loop review-protocol change landed here; Cycle 2 issue [teslamint/compound-loop#28](https://github.com/teslamint/compound-loop/issues/28) owns it (T7) |

- Reconciliation: registered 3, accounted for 3
- Previous doc shape: conformant

## Interview Transcript

- Independence level: same-model fresh-context
- Rounds used: 2
- Facilitator round 1: `.release-loop/runs/stabilize-routing-locking/reviews/facilitator/round-1.md`, SHA-256 `1aa28264564afac51e95fb2f78194617a2361b760f87724d0a670acaccc40c54`
- Facilitator round 2: `.release-loop/runs/stabilize-routing-locking/reviews/facilitator/round-2.md`, SHA-256 `5ba6bb5812f17c5fd7fff83b4718a31e74d8858cef80679c5eab2ae564d3d255`

| ID | Round | Phase | Probe | Answer | Evidence | Verdict (verbatim) |
|---|---|---|---|---|---|---|
| T1 | 1 | 5 | Why did a green deterministic gate fail to close the release? | U6 passed 534 tests, yet review found twelve P1/P2 evidence gaps. Later, 566 tests passed while final:branch:5 still listed R1, R6, and R14 as P1 blockers. Final:branch:6 became clean only after the V1 evidence claim. | `.release-loop/runs/stabilize-routing-locking/reviews/facilitator/round-1.md`; progress evidence | accepted |
| T2 | 1 | 5 | Did the cycle resolve the reserved legacy API routing ambiguity? | The specification reserves gemini_api, openai_api, and anthropic_api for direct-vendor providers; CLI binaries and other strings retain separate meanings. The ledger records all three reserved values bypassing proxy routing in text and structured paths, with an exact four-call C1-R matrix. | `docs/specs/2026-08-28-stabilize-routing-locking-design.md`; routing evidence | accepted |
| T3 | 1 | 5 | What concrete lock behavior is evidenced? | The plan requires contender exclusion, unchanged durable state, provider non-invocation, release after normal and forced termination, and later resume. The ledger records the lock fork harness passing exclusion, normal release, and forced release, with current candidate C1-L trials at 6/6. | `docs/plans/2026-08-28-001-feat-stabilize-routing-locking-plan.md`; integration evidence | accepted |
| T4 | 1 | 5 | Does the permitted record prove the exact V1 acceptance criterion? | The ledger says V1 was accepted with SHA-256 681402c…, but cites grading-current8/complete.json. The requested evidence/V1/complete.json is absent. The plan requires that exact file and its recorded hash. | `.release-loop/runs/stabilize-routing-locking/reviews/facilitator/round-1.md`; plan acceptance path | no evidenced answer (3 rejections): "The alternate grading-current8 path does not prove the required evidence/V1/complete.json artifact or its manifest fields." |
| T5 | 1 | 4 | How were the three carry-forward items from the previous retro reconciled by name? | The previous retro registered three items: legacy API stage semantics, live concurrent-process locking, and a standing test-integrity review lane. The permitted current artifacts show routing and locking progress, but provide no current carry-forward table or name-by-name status for all three items. | `.release-loop/runs/stabilize-routing-locking/reviews/facilitator/round-1.md`; previous Retro | no evidenced answer (3 rejections): "Partial implementation evidence is not a carry-forward reconciliation; the three registered item names lack a current row-by-row accounting." |
| T6 | 2 | 5 | Does the exact V1 gate now have sufficient completion evidence? | Yes. The required path contains a complete dogfood manifest. It records candidate stability 9/9, candidate usability 9/9, no zero rubric dimension, passed latency, complete process cleanup, sanitized results, and cost_status not_measurable. The respondent also reports cmp exit 0 and matching SHA-256 values. | `.release-loop/runs/stabilize-routing-locking/reviews/facilitator/round-2.md`; canonical V1 report | accepted |
| T7 | 2 | 4 | Are all three prior carry-forward items now accounted for by name, including the unresolved process item? | Yes. Legacy *_API stage semantics are Done at merge commit 0de811f with focused routing/provider evidence. Live concurrent-process locking is Done with the fresh integration run and candidate C1-L at 6/6. The standing test-integrity review lane is Not started in this repository and is assigned to Cycle 2 through teslamint/compound-loop#28. | `.release-loop/runs/stabilize-routing-locking/reviews/facilitator/round-2.md`; previous Retro; issue #28 | accepted |

## Findings

### What worked well

- **What happened**: The final review stayed blocked after the deterministic suite passed, then closed only after the complete current-head V1 evidence was accepted.
  **Why**: The review gate separated product test results from live corpus, grading, and evidence-integrity proof.
  **How to apply**: Keep deterministic tests, live evidence, grading, and review closure as separate gates.
  **Cites**: T1; Phase 3 criteria 6–9; review events `final:branch:5` and `final:branch:6`
- **What happened**: Reserved legacy API values and live lock behavior both passed fresh focused measurements after merge.
  **Why**: The implementation used one classification boundary and a real two-process fixture.
  **How to apply**: Test compatibility routing and process contention at their real boundaries.
  **Cites**: T2, T3; Phase 3 criteria 1–5
- **What happened**: The canonical V1 report was made byte-identical to the accepted grading report before Retro measurement.
  **Why**: The release plan named an exact path that the attempt-specific grading output did not use.
  **How to apply**: Verify and materialize the plan path before accepting a generated report.
  **Cites**: T6; Phase 3 criteria 7–9

### What to improve

- **What happened**: The first facilitator pass rejected V1 because `grading-current8/complete.json` did not satisfy the plan's exact `evidence/V1/complete.json` path.
  **Why**: Report generation and acceptance authority used different paths.
  **How to apply**: Make the runner publish the canonical report path as part of the V1 completion transition.
  **Cites**: T4, T6; Phase 2 canonical-path correction; plan lines 323–329
- **What happened**: The approved plan still cites missing `docs/solutions/workflow-issues/review-introduced-state-machine-deviation.md`.
  **Why**: A prior compounding claim named a deliverable that is not present in this checkout.
  **How to apply**: Validate every solution path cited by an approved plan before terminal completion.
  **Cites**: Phase 2 related-doc scan; `docs/plans/2026-08-28-001-feat-stabilize-routing-locking-plan.md:264`
- **What happened**: The standing test-integrity review lane remains unimplemented in the compound-loop repository.
  **Why**: This cycle changed ai-orchestration evidence but did not change the shared review protocol.
  **How to apply**: Complete the Cycle 2 issue before relying on a passing test suite as evidence-quality proof.
  **Cites**: T7; issue [teslamint/compound-loop#28](https://github.com/teslamint/compound-loop/issues/28)

### Process observations

- **What happened**: Exact-payload live retries, quota failures, and stale-head reviews were retained until one complete current-head generation passed.
  **Why**: The runner bound receipts and reports to immutable packet, state, and candidate identities.
  **How to apply**: Preserve failed attempts and require a new receipt whenever an external trial changes.
  **Cites**: T1, T6; V1 evidence and progress ledger
- **What happened**: Scoped handoff completed before Retro, and the merged-result suite ran from base `master`.
  **Why**: The handoff marker and post-merge verification made the base owner authoritative.
  **How to apply**: Record merge, merged-result verification, handoff, and Retro as ordered lifecycle evidence.
  **Cites**: Phase 2 release data; progress ledger Ship and handoff entries

## Carry-forward items registered

| Item | Type | Priority | Tracked at |
|---|---|---|---|
| Publish V1 completion reports at the plan's canonical `evidence/V1/complete.json` path. | feature | P2 | [teslamint/ai-orchestration#3](https://github.com/teslamint/ai-orchestration/issues/3) |
| Materialize and validate the missing deviation-process solution referenced by the approved plan. | process | P2 | [teslamint/ai-orchestration#3](https://github.com/teslamint/ai-orchestration/issues/3) |
| Add the standing test-integrity review lane. | process | P2 | [teslamint/compound-loop#28](https://github.com/teslamint/compound-loop/issues/28) |

## Lessons

- A complete nested grading report is not release proof until the exact plan-declared path exists and its bytes match the recorded digest.
- Six review rounds and 566 passing tests did not close live evidence; only the complete current-head corpus, grading report, and clean re-review closed Ship.
- `cost_status: not_measurable` is a valid result when the plan allows it; never convert missing usage data into an invented cost.

## Compounding

- compound invocation: Documentation complete — docs/solutions/workflow-issues/canonical-v1-completion-artifact.md
- Discoverability: `AGENTS.md` now points agents to `docs/solutions/`; `CONCEPTS.md` was scanned and no qualifying new vocabulary was added.
