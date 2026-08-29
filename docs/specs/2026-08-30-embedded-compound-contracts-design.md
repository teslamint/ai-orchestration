---
title: Embedded compound-loop contract adapter
status: approved
date: 2026-08-30
schema: spec/v1
---

# Embedded Compound-loop Contract Adapter Design

## Overview

The orchestrator already persists its own six-stage run state and command gates, but it has no explicit boundary for the release-loop contracts that govern phase transitions and sealed plans. A future caller cannot distinguish a valid contract source from an absent or incompatible checkout, and it cannot resume a release-loop phase without interpreting the ledger itself.

This cycle adds an opt-in adapter. It reads the configured compound-loop checkout as source data, validates the pinned contract manifest and `plan/v1` body seal, and persists a small release-loop ledger around the existing sequential pipeline. It never imports compound-loop as a Python package, mutates Git branches or worktrees, or performs Ship and Retro actions.

## User Scenarios

### S1: A caller enables a compatible contract source

The caller supplies `--compound-loop-path` and a sealed contract manifest. The adapter verifies source paths, source commit, schema literals, and SHA-256 digests before any phase write.

### S2: An incompatible source fails loudly

The configured path is absent, missing a required contract file, or has a digest mismatch. Startup exits with a typed diagnostic naming the contract failure and does not create a release-loop ledger.

### S3: A run persists and resumes an implementation phase

The adapter records `implement` with a current model-stage marker, survives a process restart, and resumes the next unfinished stage without replaying completed work. A `waiting-user` or `blocked` state remains durable and does not invoke Ship or Retro.

### S4: The adapter is evaluated by the C3-CT matrix

The deterministic fixture enumerates every supported `(phase, status, event)` transition and every rejection. Nine repeated fixture runs produce the same transition results and at least eight generated artifacts score 8/10 or higher.

## Scope

### In

- Read-only contract-source loading from a configured checkout path.
- Versioned contract manifest with source commit, supported literals, source paths, and SHA-256 digests.
- `plan/v1` frontmatter and canonical `body_seal` validation without importing the checkout.
- Atomic release-loop ledger read/write with gated phase transitions and resume.
- Opt-in CLI integration around the existing sequential six-stage pipeline.
- C3-CT valid/rejected transition matrix, mutation fixtures, restart/resume test, and nine-run stability gate.

### Out

- Python package dependency on compound-loop.
- Automatic Git branch or worktree creation, switching, merge, push, archive, or cleanup.
- Ship and Retro implementation; the adapter records `waiting-user` or `blocked` and returns control to the caller.
- Concurrent whole-run supervision or prompt message migration.
- Any behavior change when `--compound-loop-path` is absent.

## Requirements

- **R1**: The adapter accepts only a configured repo-relative contract source root and fails loudly for absent, incompatible, symlinked, or incomplete source files.
- **R2**: The manifest binds a full source commit, supported schema literals, source-relative paths, and SHA-256 digests for all required contract files.
- **R3**: The adapter reads the source checkout as data and does not import its modules or invoke its branch/worktree automation.
- **R4**: `plan/v1` validation uses UTF-8 universal-newline reads, literal `text.split('---', 2)[2]` body extraction, and lowercase SHA-256 `body_seal` comparison.
- **R5**: The ledger uses an explicit schema boundary, atomic same-directory replacement, and a closed transition table for `design`, `plan`, `implement`, `review`, `ship`, `retro`, `done`, and `blocked` states.
- **R6**: The six model stages remain sequential children of `implement`; they are not renamed as release phases or reordered.
- **R7**: Resume after restart skips completed stage markers and reproduces the same next phase and stage; malformed or stale state fails closed.
- **R8**: Ship and Retro remain external caller responsibilities. The adapter never pushes, merges, publishes, archives, or claims shipment.
- **R9**: C3-CT covers every supported and rejected transition, mutation detection, interrupted/resumed workflow, and nine-run stability.
- **R10**: Package tests, integration tests, Ruff checks, format checks, and the C3-CT gate pass without live model calls.

## Design Decisions

1. Use an explicit `--compound-loop-path` opt-in. Existing users retain the current workflow and cannot accidentally depend on a machine-local contract checkout.
2. Keep the adapter in `ai_orchestration.compound_loop` with three narrow responsibilities: source contracts, plan validation, and release-loop ledger. The CLI composes them but does not own parsing details.
3. Store the manifest as JSON in the consuming repository or fixture. The manifest is the authority for allowed source files and literals; the checkout is not trusted merely because it is a Git repository.
4. Model release phases separately from model stages. `implement` owns the six sequential stages; `ship` and `retro` have no internal runner in this adapter.
5. Use a closed transition function and reject unknown phase, status, or event values before writing. Resume uses the persisted transition marker and never infers a transition from filesystem contents.
6. Use a standard-library validator for `plan/v1`. The configured source checkout remains a data source, so the adapter must not add a path import or execute a validator from that checkout.

## Verification Contract

The C3-CT report names the matrix version and reports passed/total for valid and rejected rows, restart/resume parity, mutation results, nine-run stability, artifact quality, and zero live-model calls. A missing or malformed result is an infrastructure failure, not a pass. The report also proves no Git branch/worktree operation or Ship/Retro action was attempted.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A source checkout is present but has changed contract text. | Require source commit and per-file SHA-256 manifest equality before ledger writes. |
| A sealed plan is parsed with different newline or delimiter rules. | Implement and test the exact canonical read and split expression from `plan/v1`. |
| Resume replays a completed model stage. | Persist a stage marker in the ledger and test a fresh process against a deterministic transition matrix. |
| The adapter grows into Git lifecycle automation. | Keep branch/worktree operations outside its interface and add forbidden-operation tests. |
| A valid transition table hides an untested rejection. | Generate the matrix inventory and mutation fixtures from the same finite event vocabulary, with a nine-run stability gate. |
