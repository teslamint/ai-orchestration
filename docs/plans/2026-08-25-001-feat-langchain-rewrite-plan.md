---
schema: plan/v1
title: Implement the Multi-model Agent Orchestration Rewrite
type: feat
status: draft
date: 2026-08-25
execution: code
origin: docs/specs/2026-08-24-orchestration-rewrite-design-v2.md
deepened: true
---

# Goal

Replace the committed 2235-line monolithic CLI at `8ee3c4c` with a tested `src/ai_orchestration`
package that runs the six sequential stages through proxy-first, per-stage routing and explicit
CLI fallback. Preserve the observable legacy behavior, add `agy` as the brainstormer CLI with its
own argument and structured-output contracts, and leave the repository ready for a clean root
module cutover.

# Architecture notes

The implementation uses a small standard-library stage engine rather than LangChain or LangGraph.
The engine owns ordered execution, durable state, resume, approval gates, and the two existing
review/fix loops. Providers expose one protocol so stages do not branch on transport.

Each stage resolves three independent slots: a proxy `model`, an optional same-endpoint
`fallback_model`, and a `fallback_binary` for endpoint transport failure. A reachable model catalog
validates proxy-valued slots. An unreachable catalog is logged and skipped so the call-time CLI
fallback remains executable. A binary-valued slot is validated through PATH lookup, never through
`/v1/models`.

The default model table is fixed by the approved design: brainstormer `gemini-3.1-pro-low`,
reviewer and planner `gpt-5.5`, executor and fixer `claude-sonnet-5`, and code reviewer `gpt-5.5`.
Default fallback binaries are `agy`, `codex`, `codex`, `claude`, `codex`, and `claude` in stage
order. A stage flag overrides the config file's stage object, which overrides the built-in table.

The HTTP provider uses the official `openai` SDK against the CLIProxyAPI OpenAI-compatible URL.
The CLI provider owns one argv builder per binary. `agy` receives `-p <prompt>`, requests native
JSON with `--json-schema` and `--output-format json` for structured calls, and falls back to text
extraction. Codex keeps `codex exec <prompt>`; Claude keeps its existing print-mode arguments.

Structured output always validates into the requested Pydantic model. HTTP and `agy` first request
native structure, then use the existing extraction helpers on malformed, ignored, or unsupported
responses. Codex and Claude use extraction directly. A second failure raises a stage-specific
error.

The clean cutover happens only after behavior tests are green. Root modules and the four legacy
suites are removed in the final unit; no re-export shim is used because the approved A4 evidence
shows monkeypatching a re-export does not patch the module that owns the call.

# Assumption Recheck

The origin spec retains live assumptions. Fresh planning evidence was collected on 2026-08-25.

| Row | Fresh evidence | Outcome | Plan consequence |
|---|---|---|---|
| A1 | The compound-loop checkout still has no `pyproject.toml`, `setup.py`, or `setup.cfg` within depth two | match to approved contradiction | No path dependency and no compound-loop code in this cycle |
| A2 | Importing `ChatOpenAI`, `ChatAnthropic`, and `ChatGoogleGenerativeAI` from `langchain_community.chat_models` raised `ImportError`; the package emitted its sunset warning | match to approved contradiction | Do not use `langchain-community`; use the official OpenAI SDK and custom providers |
| A3 | `uv run python` is Python 3.13; approved PyPI evidence says the planned LangChain-era floor must be at least 3.10 | match to approved contradiction | Set `requires-python >=3.10` and Ruff target `py310` |
| A4 | The committed evidence probe returned `A4 REPRODUCED: shim loses the patch; owning module keeps it` | match to approved contradiction | Delete root modules only after successor tests pass; do not use shims |
| A5 | The committed evidence probe exists; the approved two-process stdlib result remains the design basis | match to approved contradiction | Keep the engine custom and test resume across real subprocesses |
| A6 | A live catalog request returned HTTP 401 without accepted credentials | unavailable | Use a stub catalog in automated tests; remeasure the live endpoint before Ship |
| A7 | A live schema probe was not safely repeatable without accepted proxy credentials | unavailable | Test model capability variance with deterministic HTTP stubs and flat schemas; remeasure live before Ship |
| A8 | `uv run python -c 'import httpx'` raised `ModuleNotFoundError` | match to approved contradiction | Do not rely on preinstalled `httpx`; add only required dependencies through `pyproject.toml` |
| A9 | The live proxy call could not be authenticated in this planning pass | unavailable | Preserve the approved OpenAI-compatible design and verify against a stub plus a live call before Ship |
| A10 | The committed file has 2235 lines; `:1598-1600` is the max-fix option, `:1953` the main loop, `:1634-1638` the Ralph option, and `orchestration_context.py:279` applies the threshold | match to corrected evidence | Port two loops separately and preserve their distinct acceptance rules |
| A11 | `agy`, `codex`, and `claude` are present; `gemini` is absent | match to corrected evidence | Default fallback binaries are executable on this host; deployment validation remains fail-closed |
| A12 | `agy` is now version 1.1.19 rather than the approved row's 1.1.18, but exposes the required options | contradiction for version only | Capability-detect `agy`; do not pin implementation to 1.1.18; see `docs/deviations/2026-08-25-agy-version-drift-001.md` |

The A1 and A12 planning contradictions have committed addenda at
`docs/deviations/2026-08-25-plan-assumption-recheck-001.md` and
`docs/deviations/2026-08-25-agy-version-drift-001.md`. The approved design is unchanged.
Unavailable A6, A7, and A9 evidence remains a planning-time unknown carried to Ship; it does not
become a false match.

# File structure

## Package foundation

- Create `src/ai_orchestration/__init__.py`: package version and public entrypoint exports.
- Create `src/ai_orchestration/config.py`: `StageConfig`, `ProviderConfig`, `OrchestratorConfig`,
  CLI/config precedence, workspace anchor, and six default model chains.
- Create `src/ai_orchestration/errors.py`: typed provider, routing, gate, state, and stage errors.
- Modify `pyproject.toml`: Python floor, Ruff target, `openai` dependency, package discovery, and
  `ai-orchestration` console script.

## Models and pure utilities

- Create `src/ai_orchestration/models/context.py`: port the Pydantic context models without
  changing field names, enum values, or Ralph Wiggum semantics.
- Create `src/ai_orchestration/models/__init__.py`: stable package exports.
- Create `src/ai_orchestration/utils/extract.py`: JSON-list, JSON-object, and fenced-code
  extraction helpers.
- Create `src/ai_orchestration/utils/diff.py`: new, modified, and unchanged file diffs.
- Create `src/ai_orchestration/utils/slug.py`: project slug and workspace path helpers.
- Create `src/ai_orchestration/prompts/__init__.py` and stage prompt modules: verbatim prompt
  constants and templates from `agent_prompts.py`.

## Providers and routing

- Create `src/ai_orchestration/providers/base.py`: `Provider` protocol, structured capability,
  provider result metadata, and failure classification.
- Create `src/ai_orchestration/providers/http.py`: OpenAI-compatible CLIProxyAPI provider,
  timeout/retry handling, flat schema serialization, and response extraction.
- Create `src/ai_orchestration/providers/cli.py`: `AgyProvider`, `CodexProvider`, and
  `ClaudeProvider`, binary discovery, per-binary argv, streaming parsing, and structured output.
- Create `src/ai_orchestration/providers/legacy_api.py`: compatibility adapters for the committed
  `APIResponse`, `OpenAITool`, `AnthropicTool`, and `GoogleAITool` contracts, plus the
  `ToolType.GEMINI_API`, `ToolType.OPENAI_API`, and `ToolType.ANTHROPIC_API` factory paths. These
  direct-vendor adapters remain available when an existing tool-config file selects them; the
  new proxy model slots do not silently reinterpret those values.
- Create `src/ai_orchestration/providers/routing.py`: catalog validation, slot resolution,
  primary/fallback state transitions, downgrade audit events, and terminal CLI failures.
- Create `src/ai_orchestration/providers/__init__.py`: provider exports.

## Engine and CLI

- Create `src/ai_orchestration/engine/state.py`: atomic run-state persistence, snapshots,
  previous-output context, and resume records.
- Create `src/ai_orchestration/engine/gates.py`: interactive gates, non-TTY fail-closed behavior,
  exact authorizing flag diagnostics, and selected fix-item handling.
- Create `src/ai_orchestration/engine/loops.py`: executor self-healing, main review/fix loop,
  Ralph Wiggum loop, and top-three fix propagation.
- Create `src/ai_orchestration/engine/stages.py`: stage registry, approach parsing, six-stage order,
  command execution/audit logs, and stage-to-provider calls.
- Create `src/ai_orchestration/engine/__init__.py`: engine exports.
- Create `src/ai_orchestration/cli.py`: Typer commands, all existing options, config loading,
  run/resume dispatch, and user-facing diagnostics.
- Modify `README.md` and `AGENTS.md`: console-script usage, `agy` replacement, model/fallback
  configuration, legacy `*_api` compatibility tool values, workspace anchor, and verification
  commands.
- Delete root modules and legacy test files only in U6 after successor coverage is green.

## Scenario coverage map

The approved origin spec contains six User Scenarios. Each row names the ordered units and an
integration scenario that walks the user-visible path. `AE<N>` denotes approved spec Success
Criterion N in `docs/specs/2026-08-24-orchestration-rewrite-design-v2.md` §Success Criteria.

| Scenario | Ordered unit chain | Scenario evidence |
|---|---|---|
| S1 per-stage routing | U1 → U3 → U4 → U5 → U6 | `tests/integration/test_user_scenarios.py::test_run_routes_each_stage`, written in U6 step 1; stub catalog plus six-stage run with proxy model flags and `--executor claude` (**Covers S1, Covers AE3**) |
| S2 command approval | U1 → U4 → U5 → U6 | `tests/integration/test_user_scenarios.py::test_non_tty_gate_requires_flag`; no command executes without `--auto-run`, paired authorized run proceeds (**Covers S2, Covers AE9**) |
| S3 fresh rerun and resume | U1 → U4 → U5 → U6 | `tests/integration/test_user_scenarios.py::test_fresh_rerun_and_resume`; two real subprocesses interrupt after executor, fresh rerun repeats executor, `--resume` skips completed stages (**Covers S3, Covers AE4**) |
| S4 review/fix loops | U2 → U4 → U5 → U6 | `tests/integration/test_user_scenarios.py::test_review_fix_loops`; main loop and Ralph Wiggum fixtures exercise separate caps and acceptance rules (**Covers S4, Covers AE2**) |
| S5 proxy unreachable | U1 → U3 → U4 → U5 → U6 | `tests/integration/test_user_scenarios.py::test_proxy_unreachable_cli_fallback`; closed-port run skips catalog validation, invokes fallback binaries, logs downgrade, and terminates a missing-binary case (**Covers S5, Covers AE6**) |
| S6 model-level failure | U1 → U3 → U4 → U5 → U6 | `tests/integration/test_user_scenarios.py::test_model_fallback_without_cli`; stub returns 429 for primary and valid output for `fallback_model`; CLI provider spy remains unused (**Covers S6, Covers AE7**) |

# Implementation Units

## U1: Scaffold package and configuration contracts

Execution note: test-first

Files:
  Create: `src/ai_orchestration/__init__.py`, `src/ai_orchestration/config.py`,
  `src/ai_orchestration/errors.py`, `tests/test_config.py`, `tests/test_package_layout.py`
  Modify: `pyproject.toml`
  Test: `tests/test_config.py`, `tests/test_package_layout.py`

Interfaces:
  Consumes: approved default model table; existing `OrchestratorConfig` fields; existing CLI
  option names; `ORCHESTRATOR_WORKSPACE` and `--workspace` precedence. U1 does not call the
  network or implement catalog probing; it defines slot-kind validation and an injectable
  `CatalogStatus` protocol boundary that U3 supplies as `probe_catalog()`.
  Produces: `StageConfig`, `ProviderConfig`, `OrchestratorConfig`, `CatalogStatus` protocol,
  `resolve_workspace_base()`, `resolve_stage_config()`, typed error classes, package metadata,
  and console-script declaration.

Test scenarios:
  happy: config file loads a stage object with `model`, `fallback_model`, and `fallback_binary`;
  bare stage string loads as `{"model": value}`.
  edge: CLI stage flag overrides config, config overrides built-in defaults, absolute workspace
  error: invalid proxy slot, invalid binary slot, malformed config, and unknown stage fail with
  stage-specific diagnostics; catalog status is accepted through the injectable boundary without
  making a network call.
  integration: installed package exposes `ai-orchestration` and writes beneath the resolved
  workspace anchor (**Covers S1, Covers S3**; component prerequisite for AE3, whose live
  enforcement is U6).


Steps:
  1. Write failing config tests for defaults, precedence, bare strings, workspace anchor, and
     slot-kind validation in `tests/test_config.py`.
  2. Run `uv run pytest tests/test_config.py -q`; confirm imports fail because package modules do
     not exist.
  3. Create the package modules and update `pyproject.toml` to Python 3.10+, Ruff py310,
     `openai`, package discovery, and the console script; implement only the declared contracts.
  4. Run the focused tests and `uv run python -m build` if the build module is available; confirm
     the console entrypoint metadata and diagnostics.
  5. Commit: `feat: scaffold package configuration and routing contracts`.

Acceptance: focused tests pass; config precedence is deterministic; no binary name is sent to the
model catalog; `pyproject.toml` declares the console script and Python floor.
## U2: Port models, prompts, and pure utilities

Execution note: characterization-first

Files:
  Create: `src/ai_orchestration/models/context.py`, `src/ai_orchestration/models/__init__.py`,
  `src/ai_orchestration/prompts/__init__.py`, `src/ai_orchestration/prompts/stages.py`,
  `src/ai_orchestration/utils/extract.py`, `src/ai_orchestration/utils/diff.py`,
  `src/ai_orchestration/utils/slug.py`, `docs/evidence/legacy-successor-inventory.md`,
  `tests/test_models.py`, `tests/test_utils.py`, `tests/test_prompts.py`
  Modify: none
  Test: `tests/test_models.py`, `tests/test_utils.py`, `tests/test_prompts.py`

Interfaces:
  Consumes: committed `orchestration_context.py`, `agent_prompts.py`, and pure helpers in
  `orchestrator_cli.py` at `8ee3c4c`.
  Produces: identical context-model enums and Pydantic field contracts, prompt constants with
  unchanged text, `extract_json_list()`, `extract_json_object()`, `extract_code_content()`,
  `generate_diff()`, `generate_project_name()`, and `generate_command_slug()`. The inventory
  artifact records every legacy test name and its named successor; ToolType CLI rename remains
  explicitly owned by U3.

Test scenarios:
  happy: every legacy model fixture, prompt format, JSON extraction, fenced-code extraction, and
  new/modified/unchanged diff case passes.
  edge: empty extraction, embedded prose, duplicate options, non-English project names, long
  slugs, optional Ralph fields, and empty review results preserve current values.
  error: malformed JSON returns the legacy empty result where specified; structured extraction
  raises only at the provider boundary when validation cannot recover.
  integration: prompt templates format with all required fields and produce byte-equivalent
  prompt text to the committed constants (**Covers S4, Covers AE1**).

Steps:
  1. Port the four legacy model suites' assertions into the new test paths, write
     `docs/evidence/legacy-successor-inventory.md` with all 93 legacy test names and successor
     paths, and add pure utility characterization fixtures before deleting any root file.
  2. Run the focused tests; confirm each failure identifies a missing package symbol or mismatch.
  3. Copy behavior into the new modules without changing context-model enum values, field defaults,
     prompt text, extraction precedence, or diff output. The provider-owned `ToolType.GEMINI` to
     `ToolType.AGY` rename is the explicit exception assigned to U3 and is tested there.
  4. Run focused tests plus the committed four-suite baseline; confirm 93 baseline cases pass and
     every baseline test name appears exactly once in the inventory artifact.
  5. Commit: `feat: port orchestration models prompts and utilities`.

Acceptance: all pure-port tests pass; prompt text is unchanged; the model public surface matches
all legacy imports needed by U3 and U6 migration; the successor inventory contains all 93 legacy
test names with one named successor each; no root module is deleted.

## U3: Implement HTTP, CLI, legacy API, and routing providers

Execution note: test-first

Files:
  Create: `src/ai_orchestration/providers/base.py`, `src/ai_orchestration/providers/http.py`,
  `src/ai_orchestration/providers/cli.py`, `src/ai_orchestration/providers/legacy_api.py`,
  `src/ai_orchestration/providers/routing.py`, `src/ai_orchestration/providers/__init__.py`,
  `tests/test_providers.py`, `tests/test_routing.py`, `tests/test_legacy_api.py`,
  `tests/fixtures/provider_responses/`
  Modify: `src/ai_orchestration/config.py`, `README.md`, `AGENTS.md`
  Test: `tests/test_providers.py`, `tests/test_routing.py`, `tests/test_legacy_api.py`

Interfaces:
  Consumes: U1 config contracts; U2 Pydantic schemas, extraction helpers, and legacy tool enums;
  OpenAI-compatible completion responses; `agy -p`, `codex exec`, and Claude print-mode argv.
  Produces: `Provider`, `ProviderResult`, `AgyProvider`, `CodexProvider`, `ClaudeProvider`,
  `HttpProvider`, `LegacyAPITool`, `APIResponse`, `OpenAITool`, `AnthropicTool`, `GoogleAITool`,
  `CatalogStatus`, `probe_catalog(base_url, api_key)`, `resolve_provider_chain()`, and typed
  provider failures. `probe_catalog` is the explicit seam consumed by U1 config validation and
  returns `reachable_with_models`, `reachable_without_id`, or `unreachable`. This unit also owns
  the approved `ToolType.GEMINI` → `ToolType.AGY` rename: `AGY = "agy"` replaces the CLI enum
  value, `LLMToolConfig.brainstormer` defaults to `AGY`, and the three `*_API` enum values remain
  unchanged. It also ports `validate_tool_config(config) -> list[str]` unchanged as a
  non-fatal compatibility API: it returns PATH warnings for configured CLI tools and never
  replaces them with startup errors. New startup validation is a separate caller and may fail
  fast without changing this legacy function's return type or warning text contract.

Test scenarios:
  happy: HTTP completion validates flat JSON; `agy` structured output validates; Codex and Claude
  text output extracts a valid model; each binary builder returns exact argv; legacy API adapters
  preserve `generate`, `generate_stream`, `is_available`, and `APIResponse` fields; the
  brainstormer default resolves to `ToolType.AGY` and all three `*_API` values still construct;
  `validate_tool_config()` returns a list and includes the committed warning for a missing CLI.
  edge: prose around JSON, malformed structured output, absent catalog, timeout, empty stderr,
  flat schemas without `$ref`/`$defs`, a bare-string stage config, each legacy API tool's
  missing-key path, and an empty warning list when all configured tools are available.
  error: `ToolType("gemini")` no longer selects a CLI provider; reachable catalog rejects unknown
  proxy ids; missing binaries fail startup; 401/403 fails without fallback; CLI nonzero, spawn
  error, timeout, and unparseable output terminate with binary-specific diagnostics. The new
  fail-fast diagnostic path is tested separately from the preserved warning-list API.
  integration: primary 429 uses `fallback_model` and never invokes CLI; closed endpoint skips
  catalog validation, uses `fallback_binary`, and records a downgrade; a child emitting no output
  for the heartbeat interval emits a heartbeat marker and stream-JSON chunks are extracted
  (**Covers S5, Covers S6, Covers AE5, Covers AE6, Covers AE7, Covers AE8**).

Steps:
  1. Write failing provider tests for exact argv, HTTP stub responses, flat schema serialization,
     catalog outcomes, legacy API compatibility, heartbeat/stream-JSON behavior, and every
     proxy/CLI failure row.
  2. Run focused provider tests; confirm failures occur before any live network call.
  3. Implement the shared protocol, official OpenAI client adapter, `agy` capability path, Codex
     and Claude extract paths, legacy API adapters, catalog probe seam, and provider state machine.
  4. Run focused tests with a local stub server, subprocess spies, and heartbeat fixtures; confirm
     no test requires the live proxy and fallback failures never escalate to another transport.
  5. Commit: `feat: add proxy cli legacy-api providers and fallback routing`.

Acceptance: provider tests pass offline; all slot kinds validate correctly; 429/5xx/model-output
faults use `fallback_model`; transport faults use `fallback_binary`; CLI failures are terminal;
`agy` never receives a positional prompt; legacy API tool tests pass; heartbeat and stream-JSON
tests fail under mutation of their respective provider paths.

## U4: Implement durable engine, gates, loops, and stages

Execution note: test-first

Files:
  Create: `src/ai_orchestration/engine/state.py`, `src/ai_orchestration/engine/gates.py`,
  `src/ai_orchestration/engine/loops.py`, `src/ai_orchestration/engine/stages.py`,
  `src/ai_orchestration/engine/__init__.py`, `tests/test_state.py`, `tests/test_gates.py`,
  `tests/test_loops.py`, `tests/test_stages.py`, `tests/fixtures/engine/`
  Modify: `src/ai_orchestration/config.py`, `src/ai_orchestration/providers/base.py`
  Test: `tests/test_state.py`, `tests/test_gates.py`, `tests/test_loops.py`, `tests/test_stages.py`

Interfaces:
  Consumes: U1 config and errors; U2 context/prompt/util contracts; U3 provider chain; stage role
  order and behavior inventory from the approved design.
  Produces: `RunState`, atomic save/load/resume functions, `ApprovalGate`, `PausedRun`,
  `execute_stage()`, `run_pipeline()`, `run_main_review_fix_loop()`,
  `run_ralph_wiggum_loop()`, `parse_approach_options()`, `CommandExecutor`, and structured
  stage audit events. `CommandExecutor` preserves the committed `retries=1` default, which means
  two total attempts (`1 + retries`) and records both attempts in its audit output.

Test scenarios:
  happy: six stages execute in order; selected fixes apply; command audit JSON is written with two
  attempts when the first command fails; Ralph Wiggum accepts by decision or threshold; top three
  fixes carry forward.
  edge: fresh rerun ignores stale state by default, resume skips completed stages, executor
  self-healing stops at four attempts, duplicate/template approaches are rejected, and empty
  review results pass.
  error: executor syntax errors retry four attempts with diff capture; command failure records
  stderr; non-TTY missing authorization exits nonzero without executing; malformed stage output
  preserves a resumable failure state.
  integration: two real subprocesses interrupt and resume after executor; a non-TTY command gate
  persists state and a following authorized resume continues (**Covers S2, Covers S3, Covers S4,
  Covers AE2, Covers AE4, Covers AE9**).

Steps:
  1. Write failing tests for atomic state transitions, gate fail-closed behavior, approach parsing,
     executor self-healing, exact two-attempt command retry/audit logs, both loops, and two-process
     resume.
  2. Run focused tests; confirm failures identify absent engine behavior rather than provider
     transport.
  3. Implement state persistence with `os.replace`, gate decisions, stage registry, command
     executor with `retries=1`, loop semantics, and the six-stage ordered pipeline.
  4. Run focused tests and the two-process fixture; inject failures at save, gate, provider, and
     child-process boundaries and inspect the expected partial states.
  5. Commit: `feat: implement durable six-stage orchestration engine`.

Acceptance: engine tests pass; every behavior inventory row has a behavior test including the
heartbeat/stream-JSON row owned by U3; the six stages remain sequential; command retries total
two attempts; resume, fresh rerun, fail-closed gates, audit logs, and both loops preserve the
approved semantics.
## U5: Wire the Typer CLI and documentation

Execution note: characterization-first

Files:
  Create: `src/ai_orchestration/cli.py`, `tests/test_cli.py`, `tests/test_smoke.py`
  Modify: `src/ai_orchestration/__init__.py`, `src/ai_orchestration/config.py`, `README.md`,
  `AGENTS.md`, `pyproject.toml`
  Test: `tests/test_cli.py`, `tests/test_smoke.py`

Interfaces:
  Consumes: U1 config, U2 prompts/utilities, U3 provider routing, U4 pipeline and gate APIs;
  all existing options including `--project-name`, `--auto-select`, `--auto-run`, `--auto-approve`,
  `--auto-fix`, `--skip-review`, six stage selectors, tool config, Ralph Wiggum options, debug,
  workspace, and resume behavior.
  Produces: `main()` Typer command, installed `ai-orchestration` entrypoint, help text, config
  loading, exact workspace resolution, and user-facing diagnostics.

Test scenarios:
  happy: `--help` lists every preserved option; a fake-provider run creates the requested project
  under the workspace anchor; `--executor claude` selects the CLI provider while `--planner opus-5`
  selects a proxy model.
  edge: config-file fallback objects, environment workspace, absolute workspace, non-English goals,
  `--auto-*` combinations, and `--resume` produce deterministic paths and output.
  error: unknown model, unavailable catalog, missing binary, non-TTY gate, provider failure, and
  malformed config return nonzero with the stage and required flag or slot in the message.
  integration: installed command smoke test writes exactly beneath `--workspace` and a default
  help invocation exits 0 (**Covers S1, Covers S2, Covers S3, Covers S5, Covers AE9**; component
  prerequisite for AE3, whose live enforcement is U6).

Steps:
  1. Write CLI option-parity and workspace smoke tests against U1–U4 fakes.
  2. Run the focused CLI tests; confirm the old root command is still untouched and the new entry
     point is the only missing layer.
  3. Implement Typer wiring, config loading, output formatting, console entrypoint, and docs for
     `agy`, proxy model ids, fallback slots, workspace anchor, and fail-closed gates.
  4. Run `uv run python -m ai_orchestration.cli --help` and the installed-entrypoint smoke test;
     confirm the exact output directory and all preserved options.
  5. Commit: `feat: wire orchestration cli and user documentation`.

Acceptance: CLI tests and smoke checks pass; README and AGENTS describe `agy` rather than
`gemini` as the subprocess; the command is discoverable through the project script; user-facing
messages name stage, model, endpoint, binary, exit code, or authorizing flag as applicable.

## External prerequisite before U6

U6 has a blocked boundary that no unit can clear: the main checkout at
`/Users/teslamint/workspace/ai-orchestration` contains protected uncommitted user work. U1–U5
must not stage, commit, revert, delete, or otherwise decide that work. Before U6 starts, the user
must independently choose the fate of every main-checkout change and leave that checkout clean.

The prerequisite evidence is the exact Risk 6 command, run from the feature worktree:

```bash
MAIN=$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')
git -C "$MAIN" status --porcelain
```

U6 is **blocked**, not partially complete, while this command produces any output. A clean result
is an external precondition, not an acceptance outcome produced by U1–U5. The user may preserve
the work by committing or moving it elsewhere, discard it only by their own decision, or defer
U6; this plan authorizes none of those actions.

## U6: Port full coverage and perform clean cutover

Execution note: characterization-first

Files:
  Create: `tests/integration/test_user_scenarios.py`, `tests/integration/test_failure_matrix.py`,
  `.release-loop/evidence/U6/` fixture outputs
  Modify: `README.md`, `AGENTS.md`
  Delete: `orchestrator_cli.py`, `orchestration_context.py`, `llm_tools.py`, `api_tools.py`,
  `agent_prompts.py`, `tests/test_orchestrator_cli.py`, `tests/test_orchestration_context.py`,
  `tests/test_llm_tools.py`, `tests/test_api_tools.py`
  Test: all new tests, integration tests, and the final full suite

Interfaces:
  Consumes: U1–U5 public APIs; `docs/evidence/legacy-successor-inventory.md` containing all
  committed legacy test names and named successors; approved behavior inventory; main checkout
  cutover guard from Risk 6.
  Produces: no legacy root import surface; complete package-only distribution; scenario evidence,
  mutation evidence, a verified 93-name successor inventory, and a clean final test/lint/format
  state.

Test scenarios:
  happy: all ten acceptance criteria and S1–S6 integration paths pass with fake/stub providers;
  edge: installed entrypoint and direct module invocation resolve the same workspace and options.
  error: mutate each inventory behavior, provider routing branch, gate guard, and `agy` argv token;
  the corresponding test fails; a missing or duplicate inventory successor fails before deletion;
  missing main-checkout cleanliness aborts before deletion.
  integration: run the complete suite, verify the inventory has 93 unique legacy names with one
  successor each, run `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run python -m ai_orchestration.cli --help`, and run the live proxy criterion when credentials
  are available (**Covers S1, Covers S2, Covers S3, Covers S4, Covers S5, Covers S6, Covers AE1–AE10**).

Steps:
  1. Write the six scenario tests and failure-matrix fixtures before deleting root files; include
     same-kind invariant/changed-axis pairs for routing, gate, and workspace guards.
  2. Run the full pre-cutover suite; confirm 93 committed baseline cases plus all new contract
     tests pass and verify the successor inventory has exactly 93 unique legacy names.
  3. Run the exact main-checkout guard from Risk 6; if output is non-empty, stop and report the
     paths without staging, reverting, or deleting anything.
  4. Delete root modules and legacy test files, update package-only imports, then run the full
     suite, Ruff checks, format check, and CLI smoke command.
  5. Run mutation checks for every inventory row and provider/gate guard; capture disposable
     outputs under `.release-loop/evidence/U6/`.
  6. Commit: `refactor: cut over to package orchestration engine`.

Acceptance: all ten acceptance criteria pass or criterion 3 has a recorded live remeasurement
before Ship; all six scenarios have integration evidence; root modules are gone; no test imports
those roots; the main checkout was verified clean before deletion; full suite, Ruff, format, and
CLI smoke pass.

# Mutation/failure-state matrix

The deliverable contains a stateful ceremony because it persists run state and can cross process
boundaries. Evidence owners write disposable fixtures under `.release-loop/evidence/U<N>/`.

| Transition | Pre-state | Action | Expected post-state | Unit | Evidence owner | success | forced failure | rerun | rollback or compensation | headless | cancellation or abort |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Run initialization | No run-state file for project | Create state directory and atomically write initial state | One valid state file naming goal, stage zero, config snapshot | U4 | U4 state tests | Reload returns identical state | Inject write failure before replace; no target state file appears | Retry creates one valid file without duplicate records | Remove only fixture state and recreate from clean pre-state | Non-TTY initialization writes state without prompting | Cancel before write leaves no state file |
| Stage completion checkpoint | Valid state with current stage | Persist output, logs, and next stage with `os.replace` | State names completed stage and resumable next stage | U4 | U4 two-process fixture | Second process skips completed stage | Inject failure before replace; HEAD and prior state remain valid | Rerun from prior state writes one next checkpoint | Restore fixture state from its pre-transition copy | Headless execution uses same atomic checkpoint | Ctrl-C before replace leaves prior checkpoint |
| Approval gate pause | Stage reaches command or fix gate | Persist pause reason and required flag, exit nonzero | No command/fix action; resumable paused state | U4/U6 | U6 failure-matrix fixture | Authorized resume proceeds once | Inject gate decision failure; no command runs | Re-run without authorization remains paused with no duplicate execution | Delete fixture state only; no external command compensation needed because none ran | Non-TTY fails closed with exact flag diagnostic | Cancel at gate leaves paused state for explicit user cleanup |
| CLI fallback downgrade | Proxy call fails transport; fallback binary exists | Record downgrade and invoke binary | Stage output and audit log name primary model and binary | U3/U6 | U6 routing fixture | Closed-port fixture completes through fallback | Make fallback binary exit nonzero; stage fails with exit/stderr and no proxy retry | Retry from saved pre-stage state makes one new attempt | Restore pre-stage state; generated files are fixture-local | No prompt required for fallback | Cancellation terminates child and preserves pre-stage checkpoint |
| Model fallback | Primary proxy returns 429/invalid output; fallback model configured | Call same endpoint with fallback model | Stage output names both model ids and CLI spy remains unused | U3/U6 | U6 routing fixture | Stub primary/fallback pair completes | Make fallback model fail; stage terminates with both diagnostics | Retry from pre-stage state does not reuse partial primary output | Restore pre-stage state; no external side effect | Stub run is non-interactive | Cancel between calls preserves pre-stage checkpoint |
| Clean cutover | U6 external prerequisite satisfied: main checkout status command returns empty output; package tests green | Verify main checkout status, delete roots, run final checks, commit | Package-only tree with clean validation and one cutover commit | U6 | U6 cutover evidence | Guard empty, deletion and checks pass, commit exists | Guard non-empty stops before deletion; any later check failure leaves an explicit package/root diff for operator recovery | Rerun only after user satisfies the prerequisite and, if needed, restores a partial deletion; no second delete commit | Restore deleted roots from HEAD before retry; never revert main checkout user work | Headless mode refuses deletion when guard output is non-empty | Cancellation before commit leaves explicit package/root diff for operator review |

# Carry-forward trigger audit

The durable tracker examined was `~/workspace/compound-loop/ROADMAP.md` plus its carry-forward table.
No row is fired by the planned files. The audit below records the rows whose triggers are relevant
or observable rather than silently ignoring the tracker.

| Tracker row | Trigger class | What fired it / current state | Disposition |
|---|---|---|---|
| Success criterion after Retro cannot be measured inside Retro | event-based | No post-Retro criterion is introduced by this plan | Deferred; unrelated to implementation |
| Facilitator/reviewer output persistence | edit-based | This plan does not touch compound-loop review protocol files | Deferred; no planned file match |
| Review verifies invariant, not sealed plan | event-based | This plan has no reviewing contract change | Deferred; no event fired |
| Severity graded against threatened criterion | event-based | This plan does not change review triage | Deferred; no event fired |
| Dispatched committer SSH socket evidence | event-based | No implementation unit is authorized to dispatch committing subagents; all planned commits are local user-gated commits. If implementation dispatch is later enabled, pass `SSH_AUTH_SOCK` explicitly and verify `%G?` for every commit before accepting it | Deferred conditionally; no event fired in this plan |
| Forced-failure matrix partial-state and shell-syntax rule | edit-based | This plan edits a stateful mutation matrix and therefore fires the durable rule from the 2026-08-05 retro | Folded into this plan: every matrix row names exact partial state, safe injection boundary, compensation, and all six outcomes; U6 validates the matrix fixtures |
| Next isolated-worktree Retro final action | event-based | Retro is not part of this implementation unit | Deferred to release-loop Retro |
| `gh pr merge` cleanup collision | event-based | No remote merge or branch cleanup is planned in these units | Deferred to Ship |
| Canonical evidence generation | event-based | No compound-loop evidence publisher is changed | Deferred; no event fired |
| Outside-diff approval finding inventory | event-based | This plan has no merge gate implementation | Deferred to Ship/review |
| Next PR merge requires Retro | event-based | No PR merge occurs in this plan | Deferred to Ship |

Attestation: all open ROADMAP carry-forward rows were read; no edit-based trigger names a planned
file or section, no drift-based trigger has an observable record in this repository, and every
event-based row is explicitly classified as not fired or routed to the later Ship/Retro phase.

# Deferred to Follow-Up Work

- Compound-loop contract I/O and gated phase integration remain deferred exactly as approved; this
  cycle creates no `compound_loop/` package.
- Concurrent whole-run supervision remains deferred; this cycle does not add a multi-run scheduler,
  output multiplexer, or shared approval queue.
- Unified `StageMessage` remains deferred; current `RalphWiggumFeedback` and `CodeReviewResult`
  are ported independently to preserve behavior, while the existing Ralph reverse channel remains
  intact.
- Prompt payload reduction remains deferred until per-stage prompt sizes are measured on a real
  run; this cycle preserves prompt wording.
- Live CLIProxyAPI catalog and model capability evidence remains a Ship remeasurement because the
  planning environment returned HTTP 401 without accepted credentials.
- Packaging publication, remote CI changes, and branch/worktree automation remain out of scope.

# Open unknowns

## Planning-time

None. The package boundary, provider contracts, stage defaults, fallback semantics, test strategy,
cutover guard, and user scenario evidence are fixed by the approved design and this plan. Live
proxy evidence is unavailable now but has a defined Ship remeasurement and does not change the
local implementation contract.

## Implementation-time

- Exact helper names inside each new module may differ from the names listed in the Interfaces
  fields; the externally observable signatures and behavior remain fixed.
- The final OpenAI SDK timeout and retry constructor arguments will be selected from the installed
  SDK version after dependency resolution; the provider failure categories and retry boundaries
  remain fixed.
- The exact serialized run-state schema will be chosen while implementing U4; it must contain
  goal, config snapshot, completed stages, current stage, outputs, logs, pause reason, and schema
  version so fresh rerun and resume are distinguishable.
- The precise `agy` structured-output envelope parser will be selected after its installed output
  is captured in a provider fixture; accepted inputs are `structured_output` objects and the
  existing extraction fallback.
- The local temporary directory layout under `.release-loop/evidence/U6/` may use fixture-specific
  filenames; every transition identity and outcome in the matrix remains represented.
