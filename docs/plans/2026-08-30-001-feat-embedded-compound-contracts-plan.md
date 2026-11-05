---
schema: plan/v1
title: Embedded compound-loop contract adapter
type: feat
status: approved
date: 2026-08-30
execution: code
origin: docs/specs/2026-08-30-embedded-compound-contracts-design.md
body_seal: f8cce09dc91b7ad7a3cc1dabc70d9ae3c9ea723b926e8a3e15059ca748cbd351
---

# Embedded Compound-loop Contract Adapter Plan

## Goal

Add an opt-in contract adapter that validates compound-loop source data, sealed `plan/v1` documents, and release-loop phase transitions around the existing sequential orchestrator. Keep Git lifecycle operations and Ship/Retro actions outside the adapter, then prove every supported and rejected transition with a deterministic C3-CT fixture.

## Architecture notes

- Add `ai_orchestration.compound_loop` as a package with source loading, plan validation, and ledger operations separated by responsibility.
- Use `Path` containment and symlink rejection for configured source paths. Read source files as UTF-8 data and compute SHA-256; never import or execute code from the configured checkout.
- Represent the manifest with a Pydantic model so unknown fields, unsupported schema literals, and incomplete source lists fail before any ledger write.
- Parse the release-loop frontmatter with a small closed parser for the fields this adapter owns. Preserve the original ledger text outside the managed frontmatter and write through a same-directory temporary file plus `os.replace`.
- Implement the phase function as `next_transition(state, event) -> TransitionResult`. The table covers `design`, `plan`, `implement`, `review`, `ship`, `retro`, `done`, and `blocked`; model stages remain a separate ordered tuple under `implement`.
- Make `--compound-loop-path` opt-in. When absent, the current CLI path and state file behavior remain unchanged. When present, validate the manifest and optional `--compound-plan` before the first release-loop ledger write.
- Use a fixture-only C3-CT runner with standard-library subprocesses. It creates no live model call, no remote, no branch, and no worktree. Nine runs compare normalized reports and artifact scores.

## Assumption Recheck

Origin spec retains no live assumptions; no assumption recheck required.

## File structure

| File | Responsibility | Owner |
|---|---|---|
| `src/ai_orchestration/compound_loop/__init__.py` | Public adapter exports | U1-U3 |
| `src/ai_orchestration/compound_loop/source.py` | Manifest model, source containment, digest and schema literal validation | U1 |
| `src/ai_orchestration/compound_loop/plan.py` | `plan/v1` frontmatter and canonical body-seal validation | U1 |
| `src/ai_orchestration/compound_loop/ledger.py` | Release-loop state model, atomic persistence, closed transitions, and resume markers | U2 |
| `src/ai_orchestration/config.py` | Optional contract-source and contract-plan configuration | U3 |
| `src/ai_orchestration/cli.py` | Opt-in preflight and phase/stage integration | U3 |
| `tests/test_compound_loop_contracts.py` | Source, plan, ledger, transition, and forbidden-operation tests | U1-U3 |
| `tests/integration/test_compound_loop_dogfood.py` | C3-CT fixture, mutation matrix, nine-run gate, and artifact scoring | U4 |
| `tests/fixtures/compound_loop/manifest.json` | Versioned C3-CT contract manifest | U1, U4 |
| `scripts/test-compound-loop-contracts.sh` | Hermetic fixture runner and bounded report | U4 |
| `docs/specs/2026-08-30-embedded-compound-contracts-design.md` | Approved source specification | U1 |
| `docs/plans/2026-08-30-001-feat-embedded-compound-contracts-plan.md` | This plan and traceability record | U1 |

## Scenario coverage map

| S-ID | Ordered unit chain | Integration evidence |
|---|---|---|
| S1 | U1 -> U3 | `test_opt_in_source_preflight` validates a compatible manifest before the first ledger write. Covers S1. |
| S2 | U1 | `test_missing_or_changed_contract_fails_before_write` checks absent, symlinked, and digest-mismatched sources. Covers S2. |
| S3 | U2 -> U3 | `test_restart_resume_skips_completed_stage` persists `implement` and resumes the same next stage without Ship/Retro calls. Covers S3. |
| S4 | U1 -> U2 -> U4 | `test_c3_ct_matrix_and_nine_run_gate` covers every valid/rejected tuple, mutation outcomes, restart/resume, and artifact scores. Covers S4. |

## Implementation Units

## U1: Add read-only contract and sealed-plan validation
Execution note: test-first
Files:
  Create: `src/ai_orchestration/compound_loop/__init__.py`; `src/ai_orchestration/compound_loop/source.py`; `src/ai_orchestration/compound_loop/plan.py`; `tests/test_compound_loop_contracts.py`; `tests/fixtures/compound_loop/manifest.json`
  Modify: none
  Test: `tests/test_compound_loop_contracts.py`
Interfaces:
  Consumes: configured source root, JSON manifest, required source-relative paths, and optional plan path
  Produces: `ContractSource`, `ContractManifest`, and `validate_plan(path) -> PlanValidation`
Test scenarios:
  happy: a compatible fixture source matches commit, paths, literals, and SHA-256 digests; a plan body seal validates
  edge: source roots with spaces pass containment, symlinked source components fail, and universal-newline body extraction remains byte-defined
  error: absent source, unknown manifest schema, missing required file, changed digest, malformed frontmatter, and bad body seal raise typed contract errors before writes
  integration: `test_opt_in_source_preflight` and `test_missing_or_changed_contract_fails_before_write` walk S1 and S2
Steps:
  1. Add failing tests for compatible manifest loading, absent source, digest mismatch, symlink rejection, and canonical plan body seal.
  2. Run the focused tests; confirm they fail because the adapter package does not exist.
  3. Implement manifest and source-root validation with no package imports or subprocess execution.
  4. Implement exact universal-newline `text.split('---', 2)[2]` body extraction and SHA-256 validation.
  5. Run the focused tests and confirm every failure names its contract boundary.
  6. Commit: `feat(compound-loop): validate external contract sources`
Acceptance: all U1 tests pass; a source mutation and plan-seal mutation fail before any ledger path is created.

## U2: Add the atomic release-loop ledger and transition matrix
Execution note: test-first
Files:
  Create: `src/ai_orchestration/compound_loop/ledger.py`
  Modify: `src/ai_orchestration/compound_loop/__init__.py`
  Test: `tests/test_compound_loop_contracts.py`
Interfaces:
  Consumes: phase state, status, finite event name, current model-stage marker, and ledger path
  Produces: `ReleaseLoopState`, `TransitionResult`, `load_ledger`, `save_ledger`, and `next_transition`
Test scenarios:
  happy: every supported transition records the expected next phase and stage marker through an atomic write
  edge: `implement` holds the six model stages in order, `waiting-user` and `blocked` preserve caller control, and same-state resume is idempotent
  error: unknown phase/status/event, stale stage marker, malformed JSON/frontmatter, illegal Ship/Retro action, and failed replace leave the prior ledger intact
  integration: `test_restart_resume_skips_completed_stage` walks S3
Steps:
  1. Add failing transition-table, atomic-write, malformed-state, and restart/resume tests.
  2. Run them; confirm invalid transitions and missing ledger code fail by named event.
  3. Implement the closed transition table and separate model-stage marker.
  4. Implement atomic same-directory persistence and fail-closed load validation.
  5. Run focused tests, including a forced replace failure that preserves the previous ledger.
  6. Commit: `feat(compound-loop): persist gated phase transitions`
Acceptance: every matrix row has one deterministic result; resume skips completed model stages and never invokes Ship or Retro operations.

## U3: Integrate the adapter into the opt-in CLI path
Execution note: test-first
Files:
  Modify: `src/ai_orchestration/config.py`; `src/ai_orchestration/cli.py`; `src/ai_orchestration/compound_loop/__init__.py`
  Test: `tests/test_compound_loop_contracts.py`; `tests/integration/test_user_scenarios.py`
Interfaces:
  Consumes: `--compound-loop-path`, optional `--compound-plan`, existing `OrchestrationConfig`, and current six-stage runner
  Produces: preflight diagnostics, phase ledger updates, resume-safe stage markers, and unchanged default behavior when opt-in is absent
Test scenarios:
  happy: opt-in fake-provider run validates source, records implement progress, and completes without external phase actions
  edge: resume with the same contract source skips completed stages; absent opt-in does not read a compound-loop checkout
  error: changed manifest, changed plan seal, invalid transition, or attempted Ship/Retro event exits nonzero before mutation
  integration: `test_opt_in_orchestration_preserves_stage_order` walks S1 and S3
Steps:
  1. Add failing CLI and integration tests for the two options, preflight, stage-order preservation, and resume.
  2. Run them; confirm the options and adapter calls are absent.
  3. Add configuration fields and invoke the adapter only when `--compound-loop-path` is supplied.
  4. Wrap the existing sequential stage runner with `implement` ledger transitions and current-stage markers.
  5. Reject any Ship or Retro event from the adapter and preserve the existing default path.
  6. Run package and integration tests with fake providers; confirm no live model call.
  7. Commit: `feat(cli): opt into compound-loop phase contracts`
Acceptance: default and opt-in workflows pass, resume preserves stage order, and branch/worktree/Ship/Retro operations remain absent.

## U4: Add C3-CT fixture, mutation checks, and nine-run dogfood gate
Execution note: test-first
Files:
  Create: `tests/integration/test_compound_loop_dogfood.py`; `scripts/test-compound-loop-contracts.sh`
  Modify: `tests/fixtures/compound_loop/manifest.json`
  Test: `tests/integration/test_compound_loop_dogfood.py`; `scripts/test-compound-loop-contracts.sh`
Interfaces:
  Consumes: U1-U3 adapter, finite transition matrix, fixture source, and fake provider setup
  Produces: versioned C3-CT report with matrix counts, rejected counts, mutation results, restart/resume result, nine-run stability, artifact scores, and model-operation count
Test scenarios:
  happy: all valid and rejected tuples pass, at least 8 of 9 generated artifacts score 8/10 or higher, and nine reports normalize identically
  edge: source paths with spaces, interrupted ledger write, and a resumed process preserve the same report identity
  error: every transition mutant, forbidden operation, missing source, and live-model call is rejected with a named diagnostic
  integration: `test_c3_ct_matrix_and_nine_run_gate` walks S4
Steps:
  1. Add failing C3-CT inventory and mutation tests for each supported/rejected tuple plus restart/resume.
  2. Run them; confirm missing matrix rows and absent report fields fail by ID.
  3. Add the hermetic fixture runner, finite matrix, forbidden-operation audit, and artifact scorer.
  4. Run the fixture nine times and compare normalized report hashes and artifact scores.
  5. Run `uv run pytest -v`, `uv run ruff check .`, `uv run ruff format --check .`, and the C3-CT script.
  6. Commit: `test(compound-loop): dogfood contract transitions across nine runs`
Acceptance: C3-CT reports all valid/rejected rows, mutation failures, interrupted/resumed workflows, 8/9 artifact scores at least 8/10, nine-run stability, and zero live model calls.

## Mutation/failure-state matrix

| Transition | Pre-state | Action | Expected post-state | Unit | Evidence owner | Success | Forced failure | Rerun | Rollback/compensation | Headless | Cancellation/abort |
|---|---|---|---|---|---|---|---|---|---|---|---|
| T1-contract-preflight | opt-in, no ledger | validate manifest and optional plan | compatible source record; no ledger on failure | U1 | U4 | `C3-CT-T1-success` records matching digests | `C3-CT-T1-forced-failure` corrupts one source digest; no ledger write; fixture root | `C3-CT-T1-rerun` repeats unchanged failure with no write | `C3-CT-T1-compensation` restores disposable source only; no production path | `C3-CT-T1-headless` rejects absent source before write | `C3-CT-T1-cancellation` cancels before write; source and ledger unchanged |
| T2-phase-persist | valid source, phase implement, stage pending | apply one valid phase/stage event and atomically save | next phase/status and current stage persisted | U2-U3 | U4 | `C3-CT-T2-success` reads exact post-state in a new process | `C3-CT-T2-forced-failure` injects `os.replace` failure; prior ledger remains byte-identical | `C3-CT-T2-rerun` retries after restoring replace and writes one next state | `C3-CT-T2-compensation` restores the disposable prior ledger; no base file | `C3-CT-T2-headless` rejects missing event authorization before write | `C3-CT-T2-cancellation` aborts before replace; temp file is removed and prior ledger remains |
| T3-resume-stage | implement with completed stage marker | restart and request next stage | same next stage, no replay, no Ship/Retro call | U2-U3 | U4 | `C3-CT-T3-success` compares two-process next-stage result | `C3-CT-T3-forced-failure` truncates disposable state; load fails closed | `C3-CT-T3-rerun` resumes only after restoring the prior valid state | `C3-CT-T3-compensation` replaces disposable state from its sealed snapshot | `C3-CT-T3-headless` returns blocked for missing resume context | `C3-CT-T3-cancellation` aborts between load and save; no stage call is recorded |

## Carry-forward trigger audit

No durable carry-forward tracker in this repo; no trigger audit possible.

## Deferred to Follow-Up Work

- Git branch/worktree automation requires a separate approved design and remains outside this adapter.
- Concurrent run supervision belongs to Cycle 5 and must not be added to this cycle.
- Prompt payload measurement and unified stage message types belong to Cycle 4 and remain unchanged here.

## Open unknowns

### Planning-time

None. The configured contract paths, source schema literals, existing state/gate primitives, and opt-in boundary are identified in the approved issue scope and local design record.

### Implementation-time

- The exact frontmatter keys needed for the release-loop ledger are limited to the adapter-owned state and will be fixed by U2 tests before implementation.
- The C3-CT artifact scoring rubric will use the issue's 10-point quality scale and record each score with a fixture identity; no live model score will be treated as evidence.
