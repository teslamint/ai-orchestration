---
title: Plan assumption recheck confirms non-package and integration assumptions
type: deviation
status: recorded
date: 2026-08-25
origin: docs/specs/2026-08-24-orchestration-rewrite-design-v2.md
---

## Purpose

The approved design retains assumption rows whose observed conclusions are contradictions of
their original claims. The plan re-ran those checks rather than treating the approved table as
permanent truth. This addendum records the fresh evidence and the implementation resolutions
without changing the approved design.

## Fresh evidence

| Row | Command/result | Outcome | Resolution in the plan |
|---|---|---|---|
| A1 | `find /Users/teslamint/workspace/compound-loop -maxdepth 2 ( -name pyproject.toml -o -name setup.py -o -name setup.cfg )` produced no package metadata | match to approved contradiction | No path dependency; compound-loop stays Deferred and no bridge is implemented |
| A2 | `uv run --with langchain-community python -c 'from langchain_community.chat_models import ChatOpenAI, ChatAnthropic, ChatGoogleGenerativeAI'` raised `ImportError`; the package emitted its sunset warning | match to approved contradiction | No `langchain-community` integration; use the official OpenAI SDK for the proxy and custom CLI providers |
| A3 | `uv run python -c 'import sys; print(sys.version_info[:2])'` returned `(3, 13)`; the approved PyPI evidence still requires the implementation floor to move to Python 3.10 | match to approved contradiction | Update `requires-python` to `>=3.10` and Ruff target to `py310` |
| A8 | `uv run python -c 'import httpx'` raised `ModuleNotFoundError` | match to approved contradiction | Add only the OpenAI SDK dependency required by the proxy path; do not assume `httpx` is preinstalled |
| A12 | `agy --version` returned `1.1.19`, while approved A12 recorded `1.1.18`; `agy` remains present and exposes `--json-schema` | contradiction for version only; capability conclusion retained | Capability-detect the binary and test the `-p` and structured-output contracts; do not pin to 1.1.18 |
| A6/A7/A9 | The current catalog probe returned HTTP 401 without accepted credentials | unavailable | Retain the approved evidence as historical; validate against a stub server in the plan and remeasure the live proxy before Ship |

## Resolution

The deviations are implementation inputs, not a reason to alter the approved design. The plan
uses custom provider protocols, an official OpenAI-compatible client, a Python 3.10 floor,
capability tests for `agy`, and a stubbed HTTP provider suite. Live proxy criteria remain
explicitly remeasurable before Ship when credentials and endpoint access are available.
