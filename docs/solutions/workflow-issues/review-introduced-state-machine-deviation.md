---
module: release
date: "2026-07-16"
problem_type: workflow_issue
component: release-ceremony
severity: medium
symptoms:
  - "full-branch review adds an observable recovery branch absent from the approved spec and plan"
  - "released behavior diverges from approved lifecycle artifacts without a committed deviation record"
root_cause: stateful release planning modeled the happy commit-to-tag path but omitted durable post-commit failure states
resolution_type: workflow_change
applies_when:
  - "review discovers a new observable state-machine branch after spec and plan approval"
  - "a ceremony can fail after creating a durable intermediate artifact but before completing its terminal action"
related_components:
  - designing
  - planning
  - reviewing
tags:
  - release
  - state-machine
  - failure-recovery
  - implementation-deviation
  - spec-plan-drift
---

## Context

Approved specifications and plans are historical records of what was reviewed
and authorized. They should not be rewritten after implementation merely to
make them match the final code. Review can nevertheless uncover necessary
behavior that the approved artifacts did not model. When that behavior is
observable — especially a new state-machine branch, recovery path, gate, side
effect, or terminal outcome — it is a scope deviation and must be documented
before release.

The `release` skill exposed this gap:

- The approved spec modeled seven phases and a direct transition from release
  commit to annotated tag, with no incomplete-release recovery branch
  (`docs/specs/2026-07-16-release-skill-design.md:71-81`).
- The approved plan's Preflight list likewise went from already-released
  detection to previous-tag discovery without recognizing an untagged release
  commit (`docs/plans/2026-07-16-002-feat-release-skill-plan.md:203-208`).
- Full-branch review identified a durable intermediate state: the release
  commit may succeed while a later pre-tag verification or tag command fails.
- Commit `640bb59` added fail-fast command packets and gated recovery for that
  untagged release commit.
- Commit `420483a` completed the contract with headless recovery behavior and
  an exact `Release v<version>` subject shared by creation and recognition.

The recovery was necessary and safe, but it was not ordinary planned
implementation. The released behavior intentionally differs from the approved
source artifacts.

## Guidance

Preserve the approved spec and plan unchanged as the record of the original
decision. Add a separate, committed **deviation addendum** before release when
implementation or review introduces observable behavior those artifacts did
not authorize or describe.

A useful addendum contains:

1. **Original contract** — identify the approved artifact and behavior.
2. **Discovered state or contradiction** — name the missing transition,
   invalid assumption, or newly observed failure state.
3. **Necessity** — explain why the behavior cannot be removed or handled only
   by moving an existing check.
4. **Observable behavior** — list new branches, gates, outputs, side effects,
   retry semantics, and recovery choices.
5. **Safety and consent boundaries** — identify actions requiring fresh
   authorization and modes that must remain non-interactive.
6. **Verification changes** — add success, forced-failure, rerun, rollback,
   and headless scenarios for the branch.
7. **Traceability** — reference the review finding, implementing commits,
   affected files, and acceptance evidence.

Treat the addendum as a release prerequisite. The release gate should verify
that every review-introduced observable deviation is represented by a
committed addendum and corresponding tests, or was removed before release.

Do not require an addendum for internal refactoring that leaves interfaces,
state transitions, persistence, consent boundaries, and terminal behavior
unchanged.

## Why This Matters

Keeping the original artifacts and recording a separate deviation satisfies
two different truth requirements:

- **Historical truth** — the spec and plan continue to show what was actually
  approved.
- **Operational truth** — the addendum shows what users and maintainers will
  actually observe in the released system.

Rewriting an approved spec after implementation erases review history.
Shipping a new observable branch without an addendum hides scope drift. Both
make later audits, retrospectives, rollback decisions, and maintenance less
reliable.

This is especially important at durable boundaries. Once a commit, database
write, deployment, payment, tag, or external request succeeds, later failure
does not restore the earlier state. Recovery behavior is therefore part of the
product contract even when review discovers it late.

## When to Apply

Require a deviation addendum when post-approval work introduces or materially
changes:

- a state-machine branch or durable intermediate state;
- recovery, retry, resume, cancellation, or rollback behavior;
- a USER gate or who may authorize an action;
- interactive versus headless behavior;
- commits, tags, files, external calls, or other persistent side effects;
- terminal signals, error classifications, or idempotent rerun behavior;
- an invariant used to recognize or recover prior work;
- a contradiction between an approved live assumption and later evidence; or
- the acceptance matrix needed to verify failure and recovery states.

Apply the check after each review round and immediately before the release
commit or tag. Do not defer it until the retrospective; by then the
undocumented behavior has shipped.

## Examples

### Incomplete release recovery

**Approved behavior:** write release files, create one release commit, verify,
then create the annotated tag.

**Review-discovered state:** the commit can succeed while pre-tag verification
or tag creation fails, leaving an untagged release commit at `HEAD`.

The deviation addendum should specify:

- recognition using the exact release subject, exact changed path set, both
  manifest versions, and newest CHANGELOG section;
- ambiguous partial evidence failing safely;
- a fresh USER gate for revalidate-and-tag, revert, or cancel;
- headless mode writing complete tag and revert packets while executing neither;
- complete fail-fast recovery programs; and
- forced-failure tests for post-commit failure, tag recovery, revert recovery,
  cancellation, rerun, and headless handoff.

### Prevention matrix

Before approving a stateful ceremony, enumerate every durable boundary:

| State | Forced failure | Expected next invocation |
|---|---|---|
| No release files written | write or validation fails | Normal release may restart |
| Files written, no commit | staging or validation fails | Clean or explicitly repair working state |
| Release commit exists, no tag | pre-tag check or tag command fails | Enter incomplete-release recovery |
| Release commit and tag exist | post-tag verification fails | Verify or repair the existing tag; never silently propose another release |

For each row, specify interactive, headless, cancellation, rerun, and rollback
outcomes. If review adds a row or changes an outcome, commit a deviation
addendum before release.
