---
title: agy version drift during plan assumption recheck
type: deviation
status: recorded
date: 2026-08-25
origin: docs/specs/2026-08-24-orchestration-rewrite-design-v2.md
---

## Claim

Approved assumption A12 records `agy` version 1.1.18 and uses that observation to define the
CLI contract: prompts use `-p`/`-i`/stdin, positional prompts fail, and `--json-schema` with
`--output-format json` returns `structured_output`.

## Fresh command

```bash
command -v agy
agy --version
agy --help
```

## Observation

On 2026-08-25 the installed binary resolved to `/opt/homebrew/bin/agy` and reported version
1.1.19, not 1.1.18. The binary still exposes the documented `--json-schema` option. The exact
version recorded in A12 therefore drifted, while the capability contract remains the intended
implementation boundary.

## Outcome

`contradiction` for the exact version claim; no contradiction established for the CLI capability
claim.

## Resolution

Do not pin implementation to `agy` 1.1.18. The plan treats `agy` as a capability-detected CLI
provider: verify the executable, probe its supported flags in provider tests, invoke prompts via
`-p`, and parse `structured_output` when present with extraction fallback. The approved spec
remains unchanged; this addendum records the planning-time evidence that prevents a stale version
number from becoming an implementation requirement.
