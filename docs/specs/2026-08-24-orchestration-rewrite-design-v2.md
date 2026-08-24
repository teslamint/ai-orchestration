---
title: Multi-model Agent Orchestration Rewrite
status: draft
date: 2026-08-24
schema: spec/v1
supersedes: docs/specs/2026-08-24-langchain-rewrite-design.md
---

# Multi-model Agent Orchestration Rewrite Design

_Created 2026-08-24._

## Overview

Rewrite `ai-orchestration` from a 2611-line monolithic CLI into a package with an explicit
stage engine, provider abstraction, and an embedded compound-loop contract layer. Models are
reached primarily through the existing CLIProxyAPI OpenAI-compatible endpoint (110 models,
schema-enforced structured output), with the current CLI-subprocess path retained as an
automatic fallback. No agent framework is adopted.

This supersedes `docs/specs/2026-08-24-langchain-rewrite-design.md`, which was approved on
evidence later found to be wrong. That spec is preserved unchanged as the record of what was
originally approved; §Assumptions records each contradiction with its proving command.

## User Scenarios

### S1: Run the pipeline with per-stage model routing
A user runs `uv run ai-orchestration "build a CLI todo app" --planner opus-5 --executor claude`.
Each stage resolves to its own model through one endpoint; the planner returns a schema-valid
task list rather than prose that must be regex-scraped.

### S2: Approve a command before it executes
The pipeline stops before running a generated shell command and prints it. The user answers.
With `--auto-run --auto-approve`, the same code path proceeds without prompting.

### S3: Re-run or resume a project
A run is interrupted (Ctrl-C, crash, closed laptop) after the executor stage. Re-invoking the
same project name **starts fresh by default**, because "run it again" is the established
meaning of that command today. When incomplete state exists the tool says so and names the
resumable stage; `--resume` continues from it, skipping completed stages and preserving their
output. Under `--auto-*` (non-interactive) the default remains fresh, so an unattended rerun
can never silently emit an artifact built from a stale goal.

### S4: Iterate review → fix until accepted
The code reviewer returns a decision and a confidence score. The loop accepts when
`decision == ACCEPTED or confidence_score >= 0.8`, otherwise dispatches the fixer and
re-reviews, stopping at `max_iterations` (default 3).

### S5: Work with the proxy unreachable
Off the tailnet, the proxy is unreachable. Each stage falls back to its configured CLI binary
(`gemini`/`codex`/`claude`), logging the downgrade explicitly. The run completes.

### S6: Drive the text-only ralph-loop
`ai-orchestration ralph-loop input.txt output.txt --goal "..." --completion-promise DONE`
runs review→fix over a text file, stopping when the promise appears or iterations are exhausted.

## Scope

### In
- `src/ai_orchestration/` package; stage engine; provider layer; compound-loop bridge.
- CLIProxyAPI HTTP provider with `response_format` structured output.
- Automatic fallback to CLI-subprocess providers.
- Durable run state enabling resume; approval gates.
- Port of all existing test coverage to the new suite; deletion of legacy root modules.

### Out
- Any agent framework (LangChain / LangGraph / CrewAI / AutoGen / Burr) — see §Assumptions A5.
- Autonomous tool-calling loops; the pipeline stays a fixed 6-stage sequence.
- Changing prompt wording; prompts move verbatim.
- Publishing to PyPI; remote CI changes beyond the Python floor bump.

## Assumptions and Preconditions

| Claim | Command | Observed at | Observed result | Evidence source |
|---|---|---|---|---|
| A1: compound-loop is installable as a pyproject path dependency | `find ~/workspace/compound-loop -maxdepth 2 \( -name pyproject.toml -o -name setup.py -o -name setup.cfg \)` | 2026-08-24T11:58Z | **contradiction** — no output; repo is markdown skills + 2 standalone scripts | working tree |
| A2: `langchain-community` supplies the chat-model classes | `uv run --with langchain-community python -c "import langchain_community.chat_models as m"` | 2026-08-24T12:40Z | **contradiction** — ChatOpenAI/ChatAnthropic/ChatGoogleGenerativeAI all MISSING; package emits sunset DeprecationWarning | isolated venv, langchain-community 0.4.2 |
| A3: `requires-python = ">=3.9"` is compatible with LangChain | PyPI JSON for langchain/langchain-core/langgraph | 2026-08-24T12:05Z | **contradiction** — all require `>=3.10`; repo declares `>=3.9`, ruff targets `py39` | pypi.org JSON API |
| A4: the four legacy test suites keep passing via root re-export shims | `grep -n 'monkeypatch.setattr\|inspect.signature' tests/` | 2026-08-24T13:10Z | **contradiction** — `monkeypatch.setattr(orchestrator_cli, "_run_ralph_loop_review", …)` needs call-time lookup in the root namespace; a plain re-export shim breaks the seam silently | tests/test_orchestrator_cli.py:241-242 |
| A5: an agent framework is needed for cycles, gates, resume, fan-out | 50-line stdlib engine exercised across two processes | 2026-08-24T14:02Z | **contradiction** — all four satisfied with 0 dependencies; PID 41911 paused at gate, PID 41917 resumed and completed without re-running finished stages | measured, this session |
| A6: CLIProxyAPI serves multiple models over one OpenAI-compatible endpoint | `curl $EP/v1/models` | 2026-08-24T14:00Z | **match** — HTTP 200, 110 models (claude 16, gemini 10, gpt 10, deepseek/kimi/qwen/glm/grok/minimax) | `https://cliproxyapi.tailnet-0a4d.ts.net:8317/v1` |
| A7: the proxy enforces JSON schema output uniformly | `curl` with `response_format.json_schema.strict=true`, then the `openai` SDK's `chat.completions.parse()` across three models | 2026-08-24T14:01Z / 14:22Z | **partial contradiction** — enforcement is per-model, not endpoint-wide. `gpt-5.5`: both forms OK. `gemini-3.1-pro-low`: flat inline schema OK, but the SDK's `$ref`/`$defs` schema returns `{}` per item. `claude-sonnet-5`: ignores `response_format` in both forms, returns prose. Drives decision 3's mandatory extract fallback | same endpoint; openai SDK 3.3.1 |
| A8: `httpx` is a new dependency | `grep -c 'name = "httpx"' uv.lock` | 2026-08-24T14:06Z | **match (already present)** — httpx is already in the lock as a transitive dep; net new runtime deps ≈ 0 | uv.lock |
| A9: the official OpenAI SDK reaches non-OpenAI models through the proxy | `OpenAI(base_url=...).chat.completions.create(model="claude-sonnet-5", ...)` | 2026-08-24T14:22Z | **match** — returned `'OK'`; SDK also raises typed `APIConnectionError` on a closed port, which is the S5 fallback trigger. Transitive cost: openai 14, anthropic 15, all three vendor SDKs 40 | openai SDK 3.3.1, isolated venv |

Environment invariants: Python 3.13.11 runtime, `.python-version` = 3.13, `.devcontainer/Dockerfile`
uses `python:3.13-slim`. Base branch `master`. `CLIPROXYAPI_KEY` is present in the environment.

## Architecture

```
src/ai_orchestration/
├── __init__.py
├── cli.py                  # Typer app: main + ralph-loop, all existing options
├── config.py               # OrchestratorConfig, per-stage model routing
├── engine/
│   ├── state.py            # RunState, atomic os.replace persistence
│   ├── stages.py           # stage registry, ordered execution, resume
│   ├── gates.py            # ApprovalGate / Paused, honours --auto-* flags
│   └── loops.py            # threshold loop: accept-or-iterate, max_iterations
├── providers/
│   ├── base.py             # Provider protocol + StructuredSupport capability
│   ├── http.py             # CLIProxyAPI via official openai SDK
│   ├── cli.py              # gemini/codex/claude subprocess providers
│   └── routing.py          # per-stage resolution + proxy→CLI fallback
├── models/                 # pydantic models (ported verbatim)
├── prompts/                # prompt templates (moved verbatim)
├── compound_loop/
│   ├── progress.py         # .release-loop/progress.md reader/writer
│   ├── plan_validator.py   # plan/v1 frontmatter + body_seal validation
│   └── release_loop.py     # ReleaseLoopBridge
└── utils/                  # slug, extract, diff helpers
```

### Key design decisions

1. **No agent framework.** Per A5 the four needed behaviours — threshold cycles, approval
   gates, cross-process resume, parallel fan-out — are ~50 lines of stdlib. The workload has no
   tool-calling and a fixed stage order, so a graph runtime would be carried weight. `engine/`
   owns control flow; a later swap to LangGraph stays local to that package.

2. **Proxy-first providers, CLI fallback.** `providers/http.py` targets the CLIProxyAPI
   endpoint; `providers/cli.py` keeps today's subprocess behaviour. `routing.py` resolves each
   stage to a provider and falls back on unreachability, logging the downgrade (S5). Both
   satisfy one `Provider` protocol, so stages never branch on transport.

3. **Structured output is a probed capability, not a guarantee.** Per A7 schema enforcement
   is per-model, not endpoint-wide: `gpt-5.5` honours it, `gemini-3.1-pro-low` honours only a
   flattened inline schema, and `claude-sonnet-5` ignores `response_format` entirely through
   the proxy. Therefore `complete_structured()` is defined for **every** provider as: request
   a schema when the model is known to support one, then validate; on refusal, malformed JSON,
   or validation failure, fall back to the `utils/extract.py` helpers and validate again;
   raise only if both fail. The CLI providers implement the same contract with the extract
   path alone. Schemas are emitted flat — no `$ref`/`$defs` — because the SDK's derived
   indirection is what `gemini-3.1-pro-low` fails on.

3a. **Official OpenAI SDK as the HTTP client.** The proxy speaks the OpenAI wire protocol, so
   one SDK reaches all 110 models (verified: `claude-sonnet-5` answers through it). It
   supplies typed failures — `APIConnectionError` is the exact S5 fallback trigger — plus
   retry and timeout handling. The `anthropic` and `google-generativeai` SDKs are **not**
   added: their native wire formats are what the proxy abstracts away, and all three together
   cost 40 transitive packages against the OpenAI SDK's 14, most already in `uv.lock`.
   `chat.completions.parse()` is avoided in favour of `create()` with flat schemas, per
   decision 3.

4. **compound-loop is a contract, not a dependency.** Per A1 nothing is installable.
   `compound_loop/` implements the progress-schema and plan/v1 contracts in Python. The
   framework checkout is consulted only as data at a configurable path; absence degrades
   loudly, never silently.

5. **Clean cutover.** Root modules (`orchestrator_cli.py`, `orchestration_context.py`,
   `llm_tools.py`, `api_tools.py`, `agent_prompts.py`) and the four legacy suites are deleted
   after their coverage is ported (A4 makes shims unsafe). The entrypoint becomes
   `[project.scripts] ai-orchestration`; `README.md` and `AGENTS.md` are updated in the same
   unit that removes the old invocation.

6. **Python floor.** `requires-python` → `>=3.10`, ruff `target-version` → `py310` (A3).

## Interface

Stage roles are unchanged (6): brainstormer, reviewer, planner, executor, code_reviewer, fixer.
Every existing CLI option is preserved; `--brainstormer`/`--planner`/etc. additionally accept
any model id the proxy exposes, not just `gemini|codex|claude`.

```python
class Provider(Protocol):
    def complete(self, prompt: str, *, system: str | None = None) -> str: ...
    def complete_structured(self, prompt: str, *, schema: type[BaseModel]) -> BaseModel: ...
    def is_available(self) -> bool: ...
```

## Testing

Offline by default: a `FakeProvider` implements the protocol; the HTTP provider is tested
against a stub server; no test performs live network I/O. Every behaviour asserted by the four
legacy suites is ported and must pass before those files are deleted. Resume is tested across
two real subprocesses, not simulated in-process. Structured-output degradation is tested
explicitly: a stub returning prose instead of JSON must still yield a validated model via the
extract fallback, and a stub returning neither must raise.

## Risks

1. **Proxy single point of failure** → mitigated by decision 2 (automatic CLI fallback, S5).
2. **Losing coverage during cutover** → the deletion unit is separate from and later than the
   port unit; the suite must be green before deletion.
3. **CLI agents vs. text generation**: `claude`/`codex` CLIs edit files autonomously while the
   HTTP path only generates text. The executor stage therefore keeps writing files itself, as
   it already does today; behaviour is unchanged.
4. **Endpoint config drift** → base URL and key are configurable; unreachable endpoints fail
   with an explicit diagnostic naming the resolved URL.
5. **Per-model structured-output variance** (A7) → mitigated by decision 3's mandatory
   extract fallback on every provider; no stage may assume schema compliance.

## Success Criteria

1. All behaviour covered by the four legacy suites still passes in the new suite.
   - **Measured by**: `uv run pytest -v`, with a reviewer confirming each ported assertion maps to a legacy one.
2. The pipeline runs end to end through the proxy with per-stage model routing.
   - **Measured by**: `uv run ai-orchestration "create a hello world script" --planner opus-5 --skip-review` exits 0 and writes the project.
3. Re-running a project is fresh by default; `--resume` continues without re-running completed stages.
   - **Measured by**: interrupt after executor, then (a) re-invoke plainly and confirm the executor runs again against the current goal, and (b) re-invoke with `--resume` and confirm the log shows completed stages skipped.
4. Typed data survives models that ignore schemas, without regressing to silent breakage.
   - **Measured by**: a test where the planner runs against (a) a schema-honouring stub and (b) a prose-only stub, and returns an equally valid model in both cases; and a third stub returning unparseable output raises rather than yielding a partial plan.
5. With the proxy unreachable, the run completes via CLI fallback.
   - **Measured by**: point the base URL at a closed port; confirm exit 0 and a logged downgrade.
6. Lint and format are clean.
   - **Measured by**: `uv run ruff check .` and `uv run ruff format --check .`.

## Open Decisions

- Whether to expose `--base-url`/`--model` as top-level flags or confine them to the tool
  config file. Owner: `planning`.
- Whether parallel fan-out is enabled for independent stages in this cycle or deferred.
  Owner: `planning`; deferring does not affect any success criterion.
- Whether the per-model structured-output capability map is a static table, a probe cached per
  endpoint, or simply always-try-then-degrade. Owner: `planning`; decision 3's fallback makes
  all three behaviourally safe, so this is a cost/complexity choice, not a correctness one.
