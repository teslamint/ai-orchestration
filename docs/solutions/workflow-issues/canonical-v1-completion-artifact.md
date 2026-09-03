---
module: V1 release evidence
date: 2026-09-03
last_updated: 2026-09-03
problem_type: workflow_issue
component: release-loop
severity: high
applies_when:
  - "A release plan names a canonical completion artifact path."
  - "A grading report is generated inside a run-specific directory."
  - "V1 or Ship acceptance depends on an artifact and its SHA-256."
symptoms:
  - "The accepted report exists only under grading-current8/."
  - "The plan-required evidence/V1/complete.json path is absent."
root_cause: The acceptance authority and generated report used different artifact paths.
resolution_type: canonical-artifact-materialization
related_components:
  - dogfood-evaluation
  - packet-bound-grading
tags:
  - release-evidence
  - canonical-artifact
  - v1-acceptance
  - grading-output
  - sha256
  - archive-integrity
---

# Canonical V1 completion artifacts

## Context

Release plans can name one exact path for a completion report. A runner can also write reports under attempt-specific directories.

In Cycle 1, the accepted report first existed at `evidence/V1/grading-current8/complete.json`. The plan required `evidence/V1/complete.json`.

An archive can expose a second digest mismatch after the canonical path is fixed. The archive source manifest records the `progress.md` bytes moved during the first archive attempt. V2 acceptance can then change those bytes before the archive becomes terminal.

## Guidance

Treat the plan path as the acceptance authority. Do not accept a nearby report path by inference.

Before V1 acceptance, verify all of these conditions:

1. The exact plan path exists.
2. The report has the expected schema and complete status.
3. The report SHA-256 matches the ledger.
4. The report binds to the current candidate and grading packet.
5. Stability, usability, latency, cleanup, and redaction checks pass.

If the runner uses an attempt-specific path, materialize the canonical report as a byte-identical artifact. Record both paths and one digest in the release evidence.

When V2 changes an archived `progress.md`, update the exact `progress.md` entry in `.archive-source-manifest.json`. Recompute that manifest's SHA-256. Update the matching `.owned[".archive-source-manifest.json"]` value in `.phase-artifact-ownership.json`. Rerun the archive verifier with the archived progress path and the same destination. Require `state: archived` and `moved: []`.

## Why This Matters

Path agreement prevents a complete child report from being mistaken for the plan-required release proof. It also makes archive and resume behavior deterministic.

The archive verifier checks payload bytes and ownership metadata separately. Updating only one digest creates a new integrity failure. Recording both updates preserves the terminal evidence chain.

## When to Apply

Apply this before V1 acceptance, Ship entry, Retro measurement, and archive. Repeat the check after a candidate commit, packet change, grading retry, handoff, or post-move V2 transition.

## Examples

The Cycle 1 plan required `.release-loop/runs/stabilize-routing-locking/evidence/V1/complete.json`. The accepted report was initially under `grading-current8/`.

The canonical report now has SHA-256 `681402c24d1c5b49279beddf07adf8084a6d48ce948a335aa6ba9003c3288a86`. `cmp` confirms byte identity with `grading-current8/complete.json`.

The report records 9/9 candidate stability, 9/9 usable artifacts, passing latency, complete cleanup, sanitized results, and `cost_status: not_measurable`.

For the Cycle 1 archive, the first terminalization attempt returned `source manifest mismatch` after V2 changed the archived ledger. The corrected progress digest was `1be087c062fe103fc1e9534e9c1150cfd156c232401a67ae62242b4584ee155c`. The manifest digest was then updated to `3e4e3bdcd5508f7f02083c92dbc63585063d4e00e7fd54b87faf62a06c4fb818`. The verifier returned `state: archived` with `moved: []`.
