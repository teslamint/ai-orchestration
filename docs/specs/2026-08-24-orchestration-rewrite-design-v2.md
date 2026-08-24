---
title: Multi-model Agent Orchestration Rewrite
status: approved
date: 2026-08-24
schema: spec/v1
supersedes: docs/specs/2026-08-24-langchain-rewrite-design.md
---

# Multi-model Agent Orchestration Rewrite Design

_Created 2026-08-24._

## Overview

Rewrite `ai-orchestration` from a 2235-line monolithic CLI (`orchestrator_cli.py` at `8ee3c4c`,
the committed baseline this cycle reads) into a package with an explicit
stage engine and provider abstraction. Models are reached primarily through the existing
CLIProxyAPI OpenAI-compatible endpoint (110 models), which enforces output schemas on some
models but not all (A7), with the current CLI-subprocess path retained as an automatic
fallback. No agent framework is adopted. compound-loop embedding is deliberately deferred to a
follow-up cycle — see §Scope/Out and §Deferred.

This supersedes `docs/specs/2026-08-24-langchain-rewrite-design.md`, which was approved on
evidence later found to be wrong. That spec is preserved unchanged as the record of what was
originally approved; §Assumptions records each contradiction with its proving command.

## User Scenarios

### S1: Run the pipeline with per-stage model routing
A user runs `uv run ai-orchestration "build a CLI todo app" --planner opus-5 --executor claude`.
Stages resolve independently, and the two flags take different paths on purpose: `opus-5` is a
proxy model id, while `claude` is an exact binary name and so pins that stage to the subprocess
provider (§Stage resolution). Unflagged stages use their proxy defaults. The planner receives a
validated task list whether the chosen model honours the requested schema or only emits prose,
because `complete_structured()` degrades to extraction before validating (decision 3).

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

### S4: Iterate review → fix
Per A10 the tool has **two separate loops**, and the rewrite preserves each exactly rather
than merging them:

| Loop | Trigger | Accepts when | Iteration cap |
|---|---|---|---|
| Main Stage 5→6 | always (unless `--skip-review`) | reviewer reports no `requires_fixes` items; the user selects which items to fix unless `--auto-fix` | `--max-fix-iterations`, default **1** |
| Ralph Wiggum | opt-in `--enable-ralph-wiggum` | `decision == ACCEPTED` or `confidence_score >= --ralph-wiggum-threshold` (default 0.8) | `--ralph-wiggum-max-iterations`, default 3 |

A user running the default pipeline sees one review pass and one optional fix pass — not two.
The separate text-only `ralph-loop` command is out of scope for this cycle (§Scope/Out).

### S5: Work with the proxy unreachable
Off the tailnet the proxy is unreachable. This is an **endpoint** failure, so a second proxy
model is no remedy — they share the transport that just died. Only a binary helps, and per A12
the brainstormer's is `agy`, which unlike the `gemini` it replaces is actually present on this
host. A binary still cannot be assumed in general: proxy-only ids such as `opus-5` have none at
all. Each stage therefore carries an explicit `fallback_binary`, configured separately from its
model id. When a stage has a reachable one the run continues and logs the downgrade naming the
stage, the attempted model, and the substituted binary. When it does not, the run **fails at
that stage** with a diagnostic naming the stage, the model id, the missing binary, and the
resolved endpoint — it never silently substitutes a different model.

### S6: A model fails while the endpoint is healthy
`--planner opus-5` exhausts its retries against a 429, or returns output no extraction can
validate. The endpoint itself is answering, so dropping to a subprocess would be a heavier
downgrade than the fault requires. When the
stage configures a `fallback_model`, the run retries there, logs the substitution naming both
ids, and continues on the proxy. With no `fallback_model` configured the stage fails rather
than picking a model the user did not choose.


## Scope

### In
- `src/ai_orchestration/` package; stage engine; provider layer.
- CLIProxyAPI HTTP provider requesting `response_format` where supported, with extraction
  fallback and validation on every path.
- Automatic fallback to CLI-subprocess providers.
- Durable run state enabling resume; approval gates.
- Port of all existing test coverage to the new suite; deletion of legacy root modules.
- **`gemini` CLI → `agy`** (A12). The brainstormer's default binary changes, `ToolType.GEMINI`
  becomes `ToolType.AGY`, and the provider gains its own argv builder plus native schema
  support. This is user-visible — `AGENTS.md` documents `gemini` as a tool type and
  `--brainstormer gemini` as an invocation — so both `README.md` and `AGENTS.md` are updated in
  the unit that makes the change. The proxy model ids `gemini-*` are unaffected; only the
  subprocess binary is replaced.

### Out
- Any agent framework (LangChain / LangGraph / CrewAI / AutoGen / Burr) — see §Assumptions A5.
- Autonomous tool-calling loops; the pipeline stays a fixed 6-stage sequence.
- Changing prompt wording; prompts move verbatim.
- Publishing to PyPI; remote CI changes beyond the Python floor bump.
- **compound-loop embedding** — deferred whole, by decision, to the follow-up cycle described
  in §Deferred. No `compound_loop/` package, no partial stub, and no empty directory ships in
  this cycle; a scaffold with no scenario and no success criterion would read as delivered
  work while enforcing nothing.
- **The text-only `ralph-loop` command** — by user decision it is not needed. It exists only as
  uncommitted work in the base checkout's working tree (never committed to any branch), so
  dropping it now costs nothing and removes a command, ten helper functions, four prompt
  templates, and a state-file lifecycle from the rewrite. The working-tree diff is preserved by
  the user if it is ever wanted again. This does **not** touch the Ralph Wiggum feedback loop
  (`--enable-ralph-wiggum`), which is committed, shipped in 0.4.0a0, and stays in scope.

## Assumptions and Preconditions

| Claim | Command | Observed at | Observed result | Evidence source |
|---|---|---|---|---|
| A1: compound-loop is installable as a pyproject path dependency | `find ~/workspace/compound-loop -maxdepth 2 \( -name pyproject.toml -o -name setup.py -o -name setup.cfg \)` | 2026-08-24T11:58Z | **contradiction** — no output; repo is markdown skills + 2 standalone scripts | working tree |
| A2: `langchain-community` supplies the chat-model classes | `uv run --with langchain-community python -c "import langchain_community.chat_models as m"` | 2026-08-24T12:40Z | **contradiction** — ChatOpenAI/ChatAnthropic/ChatGoogleGenerativeAI all MISSING; package emits sunset DeprecationWarning | isolated venv, langchain-community 0.4.2 |
| A3: `requires-python = ">=3.9"` is compatible with LangChain | PyPI JSON for langchain/langchain-core/langgraph | 2026-08-24T12:05Z | **contradiction** — all require `>=3.10`; repo declares `>=3.9`, ruff targets `py39` | pypi.org JSON API |
| A4: the four legacy test suites keep passing via root re-export shims | `uv run --no-project --python 3.13 --with pytest python docs/evidence/2026-08-24-shim-monkeypatch-probe.py` | 2026-08-24T14:37:52Z | **contradiction** — exit 0, "A4 REPRODUCED": patching a name on a re-export shim leaves the caller bound to the real function (`REAL`), while patching the module that owns both works (`FAKE`). A suite that stubs a stage would silently invoke the real one | `docs/evidence/2026-08-24-shim-monkeypatch-probe.py`, committed and repo-independent |
| A5: an agent framework is needed for cycles, gates, resume, fan-out | 50-line stdlib engine exercised across two processes | 2026-08-24T14:02Z | **contradiction** — all four satisfied with 0 dependencies; PID 41911 paused at gate, PID 41917 resumed and completed without re-running finished stages | measured, this session |
| A6: CLIProxyAPI serves multiple models over one OpenAI-compatible endpoint | `curl $EP/v1/models` | 2026-08-24T14:00Z | **match** — HTTP 200, 110 models (claude 16, gemini 10, gpt 10, deepseek/kimi/qwen/glm/grok/minimax) | `https://cliproxyapi.tailnet-0a4d.ts.net:8317/v1` |
| A7: the proxy enforces JSON schema output uniformly | `curl` with `response_format.json_schema.strict=true`, then the `openai` SDK's `chat.completions.parse()` across three models | 2026-08-24T14:01Z / 14:22Z | **partial contradiction** — enforcement is per-model, not endpoint-wide. `gpt-5.5`: both forms OK. `gemini-3.1-pro-low`: flat inline schema OK, but the SDK's `$ref`/`$defs` schema returns `{}` per item. `claude-sonnet-5`: ignores `response_format` in both forms, returns prose. Drives decision 3's mandatory extract fallback | same endpoint; openai SDK 3.3.1 |
| A8: `httpx` is already present, so adding the SDK costs ≈0 | `uv run python -c "import httpx"` then `uv pip install --dry-run openai` against the project venv | 2026-08-24T14:45Z | **contradiction** — `ModuleNotFoundError: No module named 'httpx'`. The lock's httpx entries arrive only via the optional `api` extra, so the default environment lacks it. Real cost is **9 packages**: openai, anyio, h11, httpcore2, httpx2, idna, jiter, sniffio, truststore. Still far below LangGraph's 38, but not free | project venv at `8ee3c4c` |
| A9: the official OpenAI SDK reaches non-OpenAI models through the proxy | `OpenAI(base_url=...).chat.completions.create(model="claude-sonnet-5", ...)` | 2026-08-24T14:22Z | **match** — returned `'OK'`; SDK also raises typed `APIConnectionError` on a closed port, which is the S5 fallback trigger. Transitive cost: openai 14, anthropic 15, all three vendor SDKs 40 | openai SDK 3.3.1, isolated venv |
| A10: the pipeline has one review→fix loop accepting at `confidence >= 0.8`, default 3 | `git show 8ee3c4c:orchestrator_cli.py \| grep -n 'max_fix_iterations'`; `grep -n 'confidence_score >=' orchestration_context.py` | 2026-08-24T14:41Z, citations re-pinned to `8ee3c4c` 2026-08-25 | **contradiction** — the committed pipeline has **two** distinct loops, and neither matches that description. Main Stage 5→6 iterates on `requires_fixes` plus user selection, `--max-fix-iterations` default **1** (`:1598-1600`, loop at `:1953`). The `0.8` threshold belongs to the optional Ralph Wiggum flow (`--ralph-wiggum-threshold` at `:1634-1638`, max-iterations at `:1637`, applied at `orchestration_context.py:279`). Collapsing them would change primary-pipeline behaviour | `orchestrator_cli.py` and `orchestration_context.py` at `8ee3c4c` |
| A11: every stage can fall back to its configured `gemini`/`codex`/`claude` binary | `for b in gemini agy codex claude; do command -v $b; done` | 2026-08-24T14:41Z, re-run 2026-08-25 | **contradiction, and partly superseded by A12.** Originally: `gemini` is **not on PATH**; only `codex` and `claude` resolve. After A12 replaced `gemini` with `agy`, the re-run shows `agy`, `codex`, `claude` all present and `gemini` still absent — so every stage's *default* `fallback_binary` now resolves on this host. The surviving half of the contradiction is the general one, which still shapes S5: a binary cannot be assumed, since proxy-only ids such as `opus-5` have none at all, and a deployment may lack any of the three | host PATH, re-measured 2026-08-25 |
| A12: the brainstormer CLI is `gemini`, invoked as `[binary, prompt]` | `command -v agy; agy -p "…"; agy "…"` | 2026-08-25 | **contradiction** — by user decision `gemini` is replaced by `agy` (1.1.18, `/opt/homebrew/bin/agy`, present on PATH where `gemini` is not). The call contract differs: `agy` reads prompts only from `-p`/`-i`/stdin and **rejects a positional argument**, so today's `[binary, prompt]` shape fails. It also returns a `structured_output` object when given `--json-schema` with `--output-format json` — a CLI provider that actually enforces schemas | measured, this session |

Environment invariants: Python 3.13.11 runtime, `.python-version` = 3.13, `.devcontainer/Dockerfile`
uses `python:3.13-slim`. Base branch `master`. `CLIPROXYAPI_KEY` is present in the environment.

## Architecture

```
src/ai_orchestration/
├── __init__.py
├── cli.py                  # Typer app: main command, all existing options
├── config.py               # OrchestratorConfig, per-stage model routing
├── engine/
│   ├── state.py            # RunState, atomic os.replace persistence
│   ├── stages.py           # stage registry, ordered execution, resume
│   ├── gates.py            # ApprovalGate / Paused, honours --auto-* flags
│   └── loops.py            # threshold loop: accept-or-iterate, max_iterations
├── providers/
│   ├── base.py             # Provider protocol + StructuredSupport capability
│   ├── http.py             # CLIProxyAPI via official openai SDK
│   ├── cli.py              # agy/codex/claude subprocess providers
│   └── routing.py          # per-stage resolution + proxy→CLI fallback
├── models/                 # pydantic models (ported verbatim)
├── prompts/                # prompt templates (moved verbatim)
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
   raise only if both fail. CLI providers are **not** uniformly extract-only: per A12 `agy`
   enforces a schema natively via `--json-schema` with `--output-format json`, returning a
   `structured_output` object, so its provider requests the schema first and falls back to
   extraction exactly like the HTTP path; `codex` and `claude` use the extract path alone.
   Schemas are emitted flat — no `$ref`/`$defs` — because the SDK's derived indirection is what
   `gemini-3.1-pro-low` fails on.

3a. **Official OpenAI SDK as the HTTP client.** The proxy speaks the OpenAI wire protocol, so
   one SDK reaches all 110 models (verified: `claude-sonnet-5` answers through it). It
   supplies typed failures — `APIConnectionError` is the exact S5 fallback trigger — plus
   retry and timeout handling. The `anthropic` and `google-generativeai` SDKs are **not**
   added: their native wire formats are what the proxy abstracts away, and all three together
   cost 40 transitive packages against the OpenAI SDK's 14, most already in `uv.lock`.
   `chat.completions.parse()` is avoided in favour of `create()` with flat schemas, per
   decision 3.

3b. **CLI invocation is per-binary, not one shape.** Today's three providers already differ,
   though all embed the prompt in argv (`llm_tools.py` at `8ee3c4c`): gemini
   `[binary, prompt]`, codex `[binary, "exec", prompt]`, claude
   `[binary, prompt, "--print", …]`. Per A12 `agy` breaks the shared assumption that a prompt
   can be positional at all — it reads only from `-p`/`-i`/stdin and **rejects** a positional
   argument — so it becomes `[binary, "-p", prompt]`. Flattening these into one builder would
   fail at runtime for a reason no current test catches. Each CLI provider therefore owns its
   argv builder, and a test asserts the exact argv per binary rather than a generic pattern.

4. **No compound-loop code this cycle.** A1 established that nothing there is installable, and
   the embedding's real target — running the pipeline as gated phases — is a larger change
   than the rewrite it would ride along with. Shipping the bridge half-built would leave a
   package whose contracts nothing verifies. §Deferred records the full target so it cannot be
   lost, and the engine keeps that future in reach: `engine/gates.py` already models an
   approval gate, which is the primitive a phase gate is built from.

5. **Clean cutover.** Root modules (`orchestrator_cli.py`, `orchestration_context.py`,
   `llm_tools.py`, `api_tools.py`, `agent_prompts.py`) and the four legacy suites are deleted
   after their coverage is ported (A4 makes shims unsafe). The entrypoint becomes
   `[project.scripts] ai-orchestration`; `README.md` and `AGENTS.md` are updated in the same
   unit that removes the old invocation.

6. **Python floor.** `requires-python` → `>=3.10`, ruff `target-version` → `py310` (A3).

## Interface

Stage roles are unchanged (6): brainstormer, reviewer, planner, executor, code_reviewer, fixer.
Every existing CLI option is preserved. The six per-stage flags — `--brainstormer`,
`--reviewer`, `--planner`, `--executor`, `--code-reviewer`, `--fixer` — additionally accept any
model id the proxy exposes, not just `agy|codex|claude`. Each stage's two fallback slots are
set in the tool-config file rather than as further flags (§Stage resolution).

```python
class Provider(Protocol):
    def complete(self, prompt: str, *, system: str | None = None) -> str: ...
    def complete_structured(self, prompt: str, *, schema: type[BaseModel]) -> BaseModel: ...
    def is_available(self) -> bool: ...
```

### Stage resolution

Each stage resolves a **chain**, not a single target. One endpoint serving 110 models (A6)
makes "another model" a different remedy from "another transport", so a stage has three slots:

| Slot | Purpose | Default |
|---|---|---|
| `model` | primary target: a proxy model id, or a CLI binary name | a **proxy model id** per stage (below) |
| `fallback_model` | second attempt on the **same** endpoint, for model-level faults (S6) | none |
| `fallback_binary` | subprocess attempt when the **endpoint** is unreachable (S5) | the stage's historical CLI tool |

Defaulting `model` to the historical CLI tool would mean a default run never contacts the
proxy, contradicting decision 2 and leaving criterion 6 with no downgrade to observe. The
historical tools are therefore the default `fallback_binary`, and `model` defaults to a proxy
id chosen to match each stage's established character:

| Stage | default `model` | default `fallback_binary` |
|---|---|---|
| brainstormer | `gemini-3.1-pro-low` | `agy` |
| reviewer | `gpt-5.5` | `codex` |
| planner | `gpt-5.5` | `codex` |
| executor | `claude-sonnet-5` | `claude` |
| code_reviewer | `gpt-5.5` | `codex` |
| fixer | `claude-sonnet-5` | `claude` |

These ids come from the catalog A6 observed; if the deployment exposes different ones, startup
validation fails naming the stage and the id rather than silently substituting.

A value resolves with no guessing: an exact CLI binary name (`agy`, `codex`, `claude`) selects
that subprocess provider; any other value is a proxy model id. Validation is per slot kind, not
uniform: proxy-valued `model` and every `fallback_model` are checked against `/v1/models`,
while `fallback_binary` — and a `model` that names a binary — are checked with executable
discovery on PATH. Sending a binary name to the model catalog would reject a valid config, so
the two checks never cross. Both run at startup, so a typo fails before any stage runs rather
than at the moment the fallback is needed.

One case must not be confused with a typo: when `/v1/models` is **unreachable**, the catalog
check is skipped, not failed. An unreachable endpoint is exactly the S5 condition and is
handled at call time by the downgrade; failing startup instead would make the offline path
unreachable and criterion 6 unmeasurable. The run logs that catalog validation was skipped and
why, so an unknown id then surfaces as a runtime stage failure rather than passing silently.
Only a reachable catalog that does not list the id is a startup failure.
Precedence, highest first: a per-stage flag, then the tool-config file, then the built-in
default from the table above.

```json
{"planner": {"model": "opus-5", "fallback_model": "gpt-5.5", "fallback_binary": "codex"}}
```

A bare string remains valid shorthand for `{"model": "<value>"}`, so configs written against
today's format keep working unchanged.

### Failure classes

Fallback is not "any error", and not one remedy. The two fallback slots answer different
faults, and choosing the wrong one either hides a cause or downgrades further than needed:

The table covers both transports. Proxy attempts may degrade; a CLI attempt is always terminal,
because it is already the last resort and a second binary would be a model the user never
chose.

**Proxy attempts**

| Failure | Behaviour |
|---|---|
| connection refused / DNS / timeout | endpoint-level → skip `fallback_model`, which shares the dead transport; go straight to `fallback_binary` and log the downgrade (S5) |
| 401 / 403 authentication | fail immediately; a credential fault is not fixed by another model |
| 429 rate limit | retry with backoff up to the SDK's `max_retries`; then `fallback_model` if configured; then fail the stage (S6) |
| 5xx | same as 429 |
| model returns unusable output | decision 3's extract fallback first; then `fallback_model` if configured; raise only if both fail |

**CLI attempts** — reached either because `model` names a binary, or as the S5 downgrade. Every
row terminates the stage; there is no onward fallback, and in particular a failed
`fallback_binary` never escalates back to the proxy that was already unreachable.

| Failure | Behaviour |
|---|---|
| binary absent from PATH | fail the stage naming the missing binary (A11) |
| spawn fails (permission, not executable) | fail the stage naming the binary and the OS error |
| exits nonzero | fail the stage naming the binary, the exit code, and captured stderr — today's runner already raises here (`orchestrator_cli.py:389-470` at `8ee3c4c`) |
| exceeds the stage timeout | terminate the child, then fail the stage naming the binary and the elapsed limit; a hung subprocess must not stall the run |
| output unusable after the extract fallback | fail the stage naming the binary and what failed to parse; `agy` additionally attempts its native schema first (decision 3) |

**Startup**

| Failure | Behaviour |
|---|---|
| proxy `model`/`fallback_model` absent from a **reachable** `/v1/models` | fail before any stage runs, naming the stage and the id |
| `/v1/models` unreachable | skip the catalog check, log that it was skipped and why, and continue — this is the S5 condition, handled at call time |
| `fallback_binary` (or a binary-valued `model`) not on PATH | fail before any stage runs, naming the stage and the binary |

A downgrade applies to the single stage that triggered it, not the rest of the run; the next
stage re-attempts the proxy. `is_available()` is a cheap pre-flight check used to choose a
provider, never a substitute for handling these errors at call time.

### Non-interactive gates

`engine/gates.py` fails closed. When a gate is reached, stdin is not a TTY, and the
authorising flag is absent (`--auto-run` for command execution, `--auto-approve` for the
execution confirmation, `--auto-fix` for applying review items), the run does not block waiting
for input. It persists resumable state, exits non-zero, and prints which flag would have
authorised it. This keeps CI and cron runs from hanging forever.

### Workspace anchor

Per RiskAuditor's finding, `Path(__file__).parent` stops meaning "repo root" once the
entrypoint moves into `src/ai_orchestration/cli.py` or an installed console script. The anchor
becomes, in order: `--workspace`, else `ORCHESTRATOR_WORKSPACE`, else the current working
directory's `workspace/`. Absolute paths are used as-is. A smoke test asserts the exact
directory an installed `ai-orchestration` writes into, and the change is called out in
`README.md` and `AGENTS.md` since it is user-visible.

### Behaviour preservation inventory

Six-stage parity is not sufficient: the current CLI carries control flow that no stage sketch
implies. Each row below must land in a named module with a test, or be explicitly descoped in
the plan — an implementation that omits one while passing the stage tests has not preserved the
tool.

All line numbers are against `orchestrator_cli.py` (2235 lines) at `8ee3c4c`, the committed
baseline, and were re-measured 2026-08-25. Citing a range that does not exist would make a row
unverifiable, which is the failure this table is meant to prevent.

| Behaviour | Current location | Owner after rewrite |
|---|---|---|
| Approach option parsing (`option_pattern` `:1795`, placeholder rejection `:1803`, dedup `:1821`) and the `--auto-select` / custom-choice path | `:1790-1848`, selection at `:1849-1889`, flag at `:1604` | `engine/stages.py` |
| Executor syntax self-healing (retry on `SyntaxError`, diff capture) | `:859-1000`, loop at `:972-997` | `engine/loops.py` |
| Interactive fix-item selection (`_prompt_fix_selection`) | `:1393-1420`, call site `:2007-2013` | `engine/gates.py` |
| Debug streaming, heartbeats, stream-JSON extraction | `:389-470`, invoked at `:917` | `providers/` + `utils/` |
| Command retry and JSON audit logs (`CommandExecutor`) | `:131-335`, log write at `:154-167` | `engine/stages.py` |
| Ralph Wiggum state file, snapshots, previous-output context, top-three fixes carried forward (`items[:3]`) | `_write_ralph_state_file` `:1343-1365`, `:2203`, `orchestration_context.py:288-316` | `engine/state.py` + `engine/loops.py` |

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
6. **Uncommitted work in the main checkout**: at design time it carries 744 insertions and 75
   deletions across 8 files (measured 2026-08-25). Most of it is the `ralph-loop` feature —
   385 lines in `orchestrator_cli.py`, 96 in `agent_prompts.py`, 72 in
   `tests/test_orchestrator_cli.py`, plus README/AGENTS docs — but not all: `.serena/project.yml`
   (+154/−66), `.gitignore` (+3), and a `__future__` import in `llm_tools.py` are unrelated
   tooling changes, and the CLI diff also refactors the committed `run_codex_code_review` and
   `run_ralph_wiggum_reviewer` onto a shared `_select_review_json`. Whoever decides the diff's
   fate should know it is not one clean feature.

   None of it is committed, so the rewrite cannot inherit it and the cutover would destroy it
   on merge. Per §Scope/Out the `ralph-loop` feature is not wanted, so the resolution is
   separation, not absorption: the rewrite treats **committed** state as its only input and
   never stages, commits, or reverts anything in the user's working tree.

   Before the cutover unit deletes `orchestrator_cli.py`, it MUST run this exact check — note
   that `git status` reports a *working tree*, not a branch, so running it from this worktree
   would inspect the wrong checkout and pass while the work it protects sits elsewhere:

   ```bash
   MAIN=$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')
   git -C "$MAIN" status --porcelain
   ```

   Non-empty output halts the cutover so the user decides, rather than a merge deciding
   silently. The 93-test baseline at `8ee3c4c` correspondingly excludes the `ralph-loop` tests.
   That is consistent rather than a shortfall: the feature is out of scope, so a baseline
   without it is the right bar.

## Success Criteria

1. All behaviour covered by the legacy suites still passes in the new suite.
   - **Measured by**: `uv run pytest -v` green, and an inventory diff showing every legacy test name maps to a named successor. Both are taken from **committed** state only, per Risk 6. The bar is the 93 tests passing at `8ee3c4c`; that baseline excludes the uncommitted `ralph-loop` tests, which is now correct rather than a gap, since §Scope/Out removes that feature from this cycle.
   - **Not sufficient on its own.** Measured 2026-08-25: those 93 are almost entirely pydantic constructors and CLI help-string assertions. Zero of them exercise executor self-healing, approach parsing, interactive fix selection, debug streaming, or `CommandExecutor` retry — `--auto-select` is asserted to *exist* as a flag, never to parse anything. A rewrite could delete all six §Behaviour preservation inventory rows and still turn this criterion green, so criterion 2 carries the real bar.
2. Every row of §Behaviour preservation inventory lands in its named module with a test that fails when the behaviour is removed.
   - **Measured by**: one test per row, each asserting observable behaviour rather than presence — executor self-healing retries a syntax error and stops at its attempt cap; approach parsing rejects template placeholders and duplicates; fix selection applies only chosen items; `CommandExecutor` retries and writes its audit record; debug streaming emits heartbeats; the Ralph Wiggum state file is created and cleaned up. Each is verified by mutation: delete or neuter the behaviour and confirm that specific test fails. A row explicitly descoped in the plan is struck from the inventory in the same commit, never silently dropped.
   - **These are net-new tests, not renames.** The legacy suite asserts none of them; interactive fix selection and debug streaming have zero coverage today (`grep -c stream_json tests/*.py` → 0 in all four files). Criterion 1's "every legacy test maps to a successor" and §Testing's "every behaviour asserted by the legacy suites is ported" both still hold — this criterion adds coverage that never existed, written against the committed behaviour at the cited lines.
3. The pipeline runs end to end through the proxy with per-stage model routing.
   - **Measured by**: `uv run ai-orchestration "create a hello world script" --planner opus-5 --skip-review` exits 0 and writes the project. This is the only criterion that needs the proxy reachable. If the endpoint is down at review time it is **deferred with a named re-measurement** — not waived, not permanently failed: the reviewer records the attempt and it is re-run before the Ship gate resolves. Criterion 6 covers the offline path and does not substitute for this one.
4. Re-running a project is fresh by default; `--resume` continues without re-running completed stages.
   - **Measured by**: interrupt after executor, then (a) re-invoke plainly and confirm the executor runs again against the current goal, and (b) re-invoke with `--resume` and confirm the log shows completed stages skipped.
5. Typed data survives models that ignore schemas, without regressing to silent breakage.
   - **Measured by**: a test where the planner runs against (a) a schema-honouring stub and (b) a prose-only stub, and returns an equally valid model in both cases; and a third stub returning unparseable output raises rather than yielding a partial plan.
6. With the proxy unreachable, the run completes via CLI fallback.
   - **Measured by**: point the base URL at a closed port and confirm exit 0 with a logged downgrade per stage. This is executable because catalog validation is skipped rather than failed when `/v1/models` is unreachable (§Startup), and because per A12 the brainstormer's binary `agy` is present on this host — the default configuration runs offline with no hand-picked binary set. A second case, with a stage whose fallback binary is genuinely missing, must fail naming that binary rather than hanging or silently skipping the stage.
7. A model-level fault falls back to another model on the same endpoint, not to a subprocess.
   - **Measured by**: a stub endpoint that returns 429 for the primary id and a valid response for the `fallback_model` id; the stage completes on the fallback model, the log names both ids, and the CLI provider is never invoked. A second case with no `fallback_model` configured fails the stage instead of substituting one.
8. Each CLI provider invokes its binary with that binary's own argument contract.
   - **Measured by**: a test asserting the exact argv per binary — `agy` receives its prompt via `-p` and never as a positional argument (A12: a positional prompt is rejected), while `codex` and `claude` keep their current shapes. Verified by mutation: changing `agy`'s builder to the positional form must fail that test. A live smoke run of the brainstormer stage against `agy` confirms the contract holds outside the test double.
9. Approval gates fail closed when they cannot ask, and proceed when authorised.
   - **Measured by**: run to a command-approval gate with stdin not a TTY and `--auto-run` absent; assert no command executed, the run exited non-zero, the diagnostic names `--auto-run` specifically, and the persisted state is resumable (a following `--resume` continues rather than restarting). Paired positive case: the same run with `--auto-run --auto-approve` proceeds without prompting. This covers S2 and §Non-interactive gates, which no other criterion measures — without it an implementation could hang forever in CI, or execute an unapproved command, and still pass every other criterion.
10. Lint and format are clean.
    - **Measured by**: `uv run ruff check .` and `uv run ruff format --check .`.

## Open Decisions

- Whether to expose `--base-url` as a top-level flag or confine it to the tool config file.
  Owner: `planning`. Narrowed 2026-08-25: `--model` is no longer part of this question — the
  six per-stage flags set `model` directly, and the two fallback slots are config-file fields
  per §Stage resolution.
- ~~Whether parallel fan-out is enabled for independent stages in this cycle.~~ **Closed
  2026-08-25: not applicable.** Reading what each stage consumes from the context shows the six
  form a strict chain — `brainstorm_review` reads `brainstorming_ideas`, `planning` reads
  `refined_brainstorming`, `code_review` reads `generated_diffs` and `execution_logs`. No two
  stages are independent, so there is no fan-out to enable. The parallel axis that does exist
  is across whole runs, not within one; see §Deferred.
- Whether the per-model structured-output capability map is a static table, a probe cached per
  endpoint, or simply always-try-then-degrade. Owner: `planning`; decision 3's fallback makes
  all three behaviourally safe, so this is a cost/complexity choice, not a correctness one.

## Deferred: compound-loop embedding (follow-up cycle)

Recorded here so the deferral is a decision with a target, not an omission. The user's original
request named three goals; this is the third, and it is descoped by explicit choice rather than
dropped. Approved scope for the follow-up cycle, in dependency order:

1. **Contract I/O.** Read and write `.release-loop/progress.md` per its schema, and validate
   `plan/v1` documents (frontmatter fields plus `body_seal`, whose canonical extraction is
   `open(path, encoding="utf-8", newline=None).read()` then `text.split('---', 2)[2]`,
   SHA-256).
2. **Gated orchestration.** Run the six pipeline stages as compound-loop phases, recording each
   transition in the ledger and enforcing the gates — built on `engine/gates.py` from this
   cycle.

Schema sources, verified 2026-08-24 (v1 cited two paths that do not exist; these are the real
ones):

| Contract | Actual path in the compound-loop checkout | Size |
|---|---|---|
| progress ledger | `skills/release-loop/references/progress-schema.md` | 70 lines |
| plan/v1 | `skills/planning/schemas/plan-schema.md` | 298 lines |
| headless behaviour | `schemas/headless-contract.md` | 23 lines |
| executable validator | `skills/planning/scripts/validate-plan-frontmatter.py` | — |

Two constraints that cycle inherits. First, A1 stands: nothing in compound-loop is pip
installable, so the contracts are reimplemented in Python and the checkout is consulted only as
data at a configurable path, degrading loudly when absent. Second, branch and worktree
automation — which v1 folded into `ReleaseLoopBridge` — is **not** in the approved scope above;
it lets the tool mutate git state on its own and needs its own design pass before it is
considered.

## Deferred: concurrent runs (follow-up cycle)

Proposed 2026-08-25: run several goals at once behind one front-facing orchestrator. Deferred
by decision, with the measurement that shapes it recorded so the next cycle does not restate
the question.

The parallel unit is a **run**, not a stage. Stages cannot overlap (see the closed Open
Decision above); separate runs already carry their own `project_name` and `workspace_path` and
share no in-memory state, so they are the natural unit. That makes this an execution-model
change, not a new stage — which is why it does not ride along with a rewrite whose approved
shape is a fixed sequential pipeline.

What that cycle must design, none of which this one answers:

| Question | Why it is not free |
|---|---|
| Workspace isolation | `workspace_path` is per project, but the CWD-relative anchor (§Workspace anchor) and any shared cache are not |
| Output interleaving | Six stages already stream Rich output; N runs writing one terminal needs a per-run channel or a supervisor view |
| Failure isolation | One run failing must not abort its siblings, and the exit status has to mean something across N results |
| Approval gates under concurrency | `engine/gates.py` blocks on one TTY; N runs cannot each own stdin, so gates need queueing or a non-interactive contract |
| Provider limits | N runs multiply request rate against one proxy; the 429 row in §Failure classes is written for one caller |

This cycle leaves the ground prepared rather than half-built: `engine/state.py` persists run
state atomically and resume already works across two real processes (A5), which is the
primitive a supervisor would coordinate.

## Deferred: prompt payload measurement (follow-up cycle)

Proposed 2026-08-25 as "token-minimal communication between agents". Recorded with a
correction, so the follow-up targets the real cost centre.

Stage-to-stage handoff is `OrchestrationContext` field access — ordinary Python attributes,
zero tokens — so a transport-level protocol between stages would create a cost that does not
exist today. (The separate question of whether the *message type* should be unified is its own
deferred item below; it is a typing question, not a token one.) The genuine spend is what gets
**embedded into prompts**: `code_review` receives `generated_diffs` and `execution_logs`,
`planning` receives `refined_brainstorming`, and none of these are currently bounded.

Deferred as a measurement task, not a redesign: after the rewrite, instrument per-stage prompt
sizes on a real run, rank the stages by payload, and only then decide what to truncate,
summarise, or reference by path. Prompts move verbatim this cycle (§Scope/Out), so measuring
first also keeps the behaviour-preservation evidence interpretable — changing prompt content
and the engine at once would leave no clean baseline.

## Deferred: unified stage message type (follow-up cycle)

Raised 2026-08-25 as "do we really not need inter-agent messaging?". Measuring the committed
code answered it: **messaging already exists, asymmetrically.**

`RalphWiggumFeedback` (`orchestration_context.py:117-133`) is a structured message in all but
name — `reviewer_id` (sender), `decision`, `comments`, `suggestions`, `confidence_score` — sent
via `submit_ralph_wiggum_feedback()` and delivered into the next iteration's prompt through
`get_self_reference_context()`. That is a working reverse channel from reviewer to fixer.
`CodeReviewResult` expresses nearly the same concept for the main pipeline as a separate type.
The other four stages have no message concept at all; they write context fields directly.

So the open question is not whether to *add* messaging. It is whether to promote the concept
that already exists into one first-class `StageMessage` type — sender stage, decision, body,
confidence — that both existing types collapse into and every stage emits.

Deferred by decision, because this cycle's contract is behaviour preservation (criteria 1 and
2). Unifying the two types changes no observable behaviour while making it materially harder to
prove nothing was lost: every assertion about `RalphWiggumFeedback` and `CodeReviewResult`
would have to be re-expressed against the new type in the same commit that moves the engine.
Doing it after the engine has landed keeps that diff readable and the port evidence clean.

What the follow-up cycle inherits, so it does not restate the analysis:

| Fact | Location at `8ee3c4c` |
|---|---|
| Reverse-channel message already exists | `orchestration_context.py:117-133`, `:256-287` |
| Its delivery into the next prompt | `get_self_reference_context()`, called at `orchestrator_cli.py:1265` |
| The near-duplicate concept | `CodeReviewResult`, `orchestration_context.py:103-116` |
| Stages with no message concept | brainstormer, brainstorm_reviewer, planner, executor |

Out of scope for that cycle too, unless separately designed: letting a stage send *backwards*
to an earlier stage (executor asking planner to clarify). That turns the fixed six-stage
sequence into a graph and collides with §Scope/Out's exclusion of autonomous loops.
