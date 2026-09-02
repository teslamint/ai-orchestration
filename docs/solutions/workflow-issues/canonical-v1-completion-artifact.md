---
module: V1 release evidence
date: 2026-09-03
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
---

# Canonical V1 completion artifacts

## Context

Release plans can name one exact path for a completion report. A runner can also write reports under attempt-specific directories.

In Cycle 1, the accepted report first existed at `evidence/V1/grading-current8/complete.json`. The plan required `evidence/V1/complete.json`.

## Guidance

Treat the plan path as the acceptance authority. Do not accept a nearby report path by inference.

Before V1 acceptance, verify all of these conditions:

1. The exact plan path exists.
2. The report has the expected schema and complete status.
3. The report SHA-256 matches the ledger.
4. The report binds to the current candidate and grading packet.
5. Stability, usability, latency, cleanup, and redaction checks pass.

If the runner uses an attempt-specific path, materialize the canonical report as a byte-identical artifact. Record both paths and one digest in the release evidence.

## Why This Matters

Path agreement prevents a complete child report from being mistaken for the plan-required release proof. It also makes archive and resume behavior deterministic.

## When to Apply

Apply this before V1 acceptance, Ship entry, Retro measurement, and archive. Repeat the check after a candidate commit, packet change, grading retry, or handoff.

## Examples

The Cycle 1 plan required `.release-loop/runs/stabilize-routing-locking/evidence/V1/complete.json`. The accepted report was initially under `grading-current8/`.

The canonical report now has SHA-256 `681402c24d1c5b49279beddf07adf8084a6d48ce948a335aa6ba9003c3288a86`. `cmp` confirms byte identity with `grading-current8/complete.json`.

The report records 9/9 candidate stability, 9/9 usable artifacts, passing latency, complete cleanup, sanitized results, and `cost_status: not_measurable`.
