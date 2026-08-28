---
schema: plan/v1
title: Stabilize Legacy API Routing and Live Run Locking
type: feat
status: approved
date: 2026-08-28
execution: code
origin: docs/specs/2026-08-28-stabilize-routing-locking-design.md
deepened: true
body_seal: 837155c438dd9df0330b2e3e9839637b67ccf6f9fbbb58e96f11f142dccb4d6b
---

# Goal

Make the three reserved legacy API literals select direct-vendor providers without changing proxy or CLI behavior. Add measurable call events, a reproducible dogfooding contract, and real two-process evidence for the existing project lock.

# Architecture notes

Configuration classifies every stage value once as a CLI binary, proxy model, or direct-vendor provider. Routing consumes that classification and chooses one of three provider factories. The values `gemini_api`, `openai_api`, and `anthropic_api` are reserved before proxy catalog lookup.

The existing `Provider` return types remain the runtime boundary. Each provider factory receives an optional `UsageSink`. HTTP and direct-vendor providers attach SDK usage to that sink before returning the unchanged content value. CLI providers attach null because their transports expose no stable usage contract. `LegacyProviderAdapter` wraps `APIBasedTool`, maps vendor failures into the shared taxonomy, and uses the common structured-output extraction path. Direct-vendor authentication and model faults are terminal. Only a direct-vendor transport fault may invoke `fallback_binary`.

Instrumentation lands before production routing changes. Run-state schema v2 supplies one stable `run_id`. `CallRecorder` writes content-free `call-event/v1` JSON Lines records. The instrumentation-only commit is the baseline build identity for later dogfooding.

The token estimate is deterministic: UTF-8 prompt bytes divided by four and rounded up. An empty prompt reports zero. `outcome` is `success` or `error`. `error_kind` is null on success and one of `authentication`, `transport`, `model`, `cli`, `provider`, or `recorder` on error. A reachable proxy catalog containing a reserved ID emits a typed warning; it does not change the reserved routing rule.

Production locking remains unchanged. A POSIX subprocess fixture proves exclusion, state immutability, lock release after normal and forced termination, and resume from the saved boundary.

The release eval records nine task trials per build: three each for `C1-R`, `C1-L`, and `C1-E`. Only the three `C1-E` orchestrator trials per build require external model egress. Each orchestrator trial may make several routing-layer provider invocations. Pilot and full packets bind the schedule, destinations, models, maximum invocations, retries, token caps, and cost caps. They do not authorize an unknown derived payload. Before every provider invocation, a routing-level gate suspends the same live process, renders the exact outbound payload bytes, rejects secrets, and requires a separate packet-bound first-hand receipt. The pilot runs baseline and candidate `C1-E-01`. The full run retains those results and runs the four remaining `C1-E` trials. Deterministic routing and locking trials remain local. Blinded model grading has its own packet and receipt because its exact artifact payload exists only after generation.

# Assumption Recheck

Fresh evidence was collected in the isolated feature worktree on 2026-08-28.

| Approved claim | Fresh command evidence | Outcome | Plan consequence |
|---|---|---|---|
| The baseline suite passes with isolated Git signing. | `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q` returned `373 passed, 2 skipped in 14.98s` at `2026-08-27T23:16:52Z`. | match | Use this environment for deterministic gates. |
| Default host Git signing breaks one fixture before assertions. | `UV_CACHE_DIR=.uv-cache uv run pytest -q` returned `1 failed, 372 passed, 2 skipped` at `2026-08-27T23:17:15Z`; the fixture commit could not use its Secretive key. | match | Keep this as Cycle 2 scope. Do not change fixture signing in Cycle 1. |
| The three CLI provider binaries exist. | `command -v agy; command -v codex; command -v claude` resolved all three under `/opt/homebrew/bin` at `2026-08-27T23:16:34Z`. | match | Live fallback evidence can use the installed binaries after its separate approval. |
| External-model dogfooding requires explicit egress approval. | The release ledger still records the rejected escalated baseline attempt. The current shell has no `CLIPROXYAPI_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GOOGLE_AI_API_KEY`. | match | V1 must disclose exact payloads, destinations, caps, and commands before each live gate. |

# File structure

## Eval contract and runner

- Create `evals/dogfood/v1/dataset.json`: fixed `C1-R`, `C1-L`, and `C1-E` task definitions.
- Create `evals/dogfood/v1/grader.yaml`: rubric, thresholds, comparability rules, and pinned grader version.
- Create `evals/dogfood/v1/call-event.schema.json`: content-free event contract.
- Create `evals/dogfood/v1/result.schema.json`: per-trial and aggregate result contract.
- Create `src/ai_orchestration/evals/dogfood.py`: deterministic validation, scoring, comparison, and sanitized report generation.
- Create `scripts/run_dogfood_eval.py`: preflight, local trials, approval-packet generation, one-shot receipt validation, and live pilot/full/grading execution.
- Create `tests/test_dogfood_eval.py`: schema, grader, report, redaction, and threshold tests.

## State and call instrumentation

- Modify `src/ai_orchestration/engine/state.py`: schema v2, UUID validation, fresh persistence, and v1 migration.
- Create `src/ai_orchestration/providers/call_events.py`: `CallEvent`, attempt token, and concurrency-safe recorder.
- Create `src/ai_orchestration/providers/call_control.py`: transport-adjacent outbound payload model and optional authorization gate.
- Modify `src/ai_orchestration/providers/routing.py`: optional recorder and usage-sink hooks without changing provider selection.
- Modify `src/ai_orchestration/providers/http.py`: attach reported SDK usage through the optional sink.
- Modify `src/ai_orchestration/providers/cli.py`: apply the optional gate after argv construction and before process creation.
- Modify `src/ai_orchestration/cli.py`: stable run context, recorder construction, and task metadata propagation.
- Modify `tests/test_state.py` and `tests/test_cli.py`: migration and first-call ordering evidence.
- Create `tests/test_call_events.py` and `tests/test_call_control.py`: event, concurrency, authorization, and default no-op tests.

## Slot classification and direct-vendor adapter

- Modify `src/ai_orchestration/config.py`: `DIRECT_VENDOR`, reserved literals, fallback validation, and conditional catalog rules.
- Modify `src/ai_orchestration/providers/legacy_api.py`: adapter, startup validation, and fault mapping.
- Modify `src/ai_orchestration/providers/routing.py`: direct-vendor factory and per-attempt instrumentation.
- Modify `src/ai_orchestration/providers/base.py`: provider-kind metadata required by call events.
- Modify `src/ai_orchestration/cli.py`: conditional catalog probe and direct-vendor factory wiring.
- Modify `tests/test_config.py`, `tests/test_legacy_api.py`, `tests/test_routing.py`, and `tests/test_providers.py`: routing and compatibility matrices.

## Process-level lock evidence

- Create `tests/integration/live_run_lock_child.py`: bounded holder, contender, and resumer driver.
- Create `tests/integration/test_live_run_lock.py`: normal-release and forced-kill process scenarios.
- Modify `.github/workflows/orchestrator-tests.yml`: run the process test on Ubuntu and macOS.

# Scenario coverage map

| Scenario | Ordered unit chain | Scenario evidence |
|---|---|---|
| S1 select a legacy direct-vendor stage | U1 → U2 → U3 → U4 → U5 | `tests/test_routing.py::test_reserved_primary_uses_direct_vendor_factory` and `tests/integration/test_user_scenarios.py::test_direct_vendor_stage_bypasses_proxy_catalog` walk CLI resolution to adapter selection. **Covers S1** |
| S2 fail safely when a legacy provider is unavailable | U3 → U4 → U5 | `tests/integration/test_user_scenarios.py::test_direct_vendor_missing_requirement_mixed_config` proves one required proxy probe, typed startup failure, zero vendor requests, and zero provider events. **Covers S2** |
| S3 preserve proxy and CLI routing | U2 → U4 → U5 | `tests/test_routing.py::test_proxy_and_cli_routing_regression_matrix` preserves proxy, CLI, fallback-model, fallback-binary, and call-event paths. **Covers S3** |
| S4 reject a simultaneous run | U2 → U6 | `tests/integration/test_live_run_lock.py::test_contender_cannot_mutate_held_run` uses real orchestrator processes and compares complete durable-tree manifests. **Covers S4** |
| S5 resume after lock release | U2 → U6 | `tests/integration/test_live_run_lock.py::test_resume_after_holder_release` covers normal release and forced process termination. **Covers S5** |
| S6 compare dogfooding quality | U1 → U2 → U3 → U4 → U5 → U6 → V1 | `tests/test_dogfood_eval.py::test_candidate_comparison_enforces_release_thresholds` plus V1's approved repeated corpus produce the release report. **Covers S6** |

# Implementation Units

## U1: Define the dogfooding contract and deterministic gate

Execution note: test-first

Files:
  Create: `evals/dogfood/v1/dataset.json`, `evals/dogfood/v1/grader.yaml`, `evals/dogfood/v1/call-event.schema.json`, `evals/dogfood/v1/result.schema.json`, `src/ai_orchestration/evals/__init__.py`, `src/ai_orchestration/evals/dogfood.py`, `scripts/run_dogfood_eval.py`, `tests/test_dogfood_eval.py`
  Modify: none
  Test: `tests/test_dogfood_eval.py`

Interfaces:
  Consumes: repository paths, full build commit IDs, task and trial IDs, call-event JSONL, artifact manifests, elapsed milliseconds, usage values, an optional pinned price snapshot, approval packet digests, and one-shot receipt files.
  Produces: `load_eval_contract(path: Path) -> EvalContract`, `validate_call_events(path: Path, expected_attempts: Sequence[ExpectedAttempt]) -> EventValidation`, `grade_artifact(path: Path, rubric: Rubric) -> ArtifactScore`, `compare_builds(baseline: Sequence[TrialResult], candidate: Sequence[TrialResult]) -> ComparisonReport`, and `consume_receipt(receipt: Path, packet_sha256: str, session_id: str) -> ReceiptReservation`. `grader.yaml` is JSON-compatible YAML parsed with `json.loads`; schema validation uses repository code and adds no dependency. The CLI subcommands are `preflight --contract --baseline-manifest --candidate-manifest`, `local --contract --build-manifest --output`, `approval-packet --scope pilot|full --contract --baseline-manifest --candidate-manifest --output`, `authorize-invocation --packet --receipt --state`, `grading-packet --state --artifact-manifest --output`, `pilot --packet --receipt --state`, `full --packet --receipt --state`, and `grading --packet --receipt --state`. U1 defines their schemas and fail-closed dispatch. Live commands report `adapter_eligibility: open` until U2 supplies the authorization gate.

Test scenarios:
  happy: fixed trial IDs validate, a score of 8 with no zero dimension is usable, and comparable latency within 25 percent passes. **Covers AE7, AE8, AE9**
  edge: unavailable usage or pricing yields `not_measurable`; unmatched terminal states are excluded from paired latency; every success exits 0 and every contract, approval, or incomplete-run failure exits 1.
  error: malformed or missing events, prompt or response content fields, current credential values, unknown schema versions, incomplete manifests, an invocation without exact-payload approval, stale or replayed receipts, packet mismatch, concurrent receipt consumption, symlinked output roots, and an orphaned payload whose owner still exists fail closed.
  integration: a synthetic baseline and candidate produce one sanitized comparison report. **Covers S6**

Steps:
  1. Write failing schema and grader tests for all fixed tasks, trial IDs, rubric dimensions, thresholds, and redaction rules.
  2. Run `uv run pytest -q tests/test_dogfood_eval.py`; confirm contract loading and comparison symbols are absent.
  3. Add the versioned data files, pure validator and grader, and a CLI that rejects live subcommands without a current packet-bound receipt. Require a session nonce, issuer, scope, packet digest, 30-minute expiry, null `consumed_at`, mode `0600`, and atomic one-shot reservation.
  4. Run the focused tests and a synthetic `local` command; confirm the sanitized manifest validates against `result.schema.json`.
  5. Commit: `feat(eval): define cycle one dogfood contract`

Acceptance: `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_dogfood_eval.py` passes. Synthetic preflight performs no network call. Receipt replay and concurrent consumption exit 1, and live commands report open adapter eligibility before model execution. **Covers AE7, AE8, AE9**

## U2: Add stable run IDs and content-free call events

Execution note: test-first

Files:
  Create: `src/ai_orchestration/providers/call_events.py`, `src/ai_orchestration/providers/call_control.py`, `tests/test_call_events.py`, `tests/test_call_control.py`
  Modify: `src/ai_orchestration/engine/state.py`, `src/ai_orchestration/providers/routing.py`, `src/ai_orchestration/providers/http.py`, `src/ai_orchestration/providers/cli.py`, `src/ai_orchestration/cli.py`, `tests/test_state.py`, `tests/test_cli.py`, `scripts/run_dogfood_eval.py`
  Test: `tests/test_call_events.py`, `tests/test_call_control.py`, `tests/test_state.py`, `tests/test_cli.py`, `tests/test_dogfood_eval.py`

Interfaces:
  Consumes: v1 or v2 `run_state.json`, `Path` for `<project>/.ai_orchestration/call_events.jsonl`, stage name, provider kind, provider identifier, optional task and executor-step IDs, prompt bytes, usage, latency, outcome, typed error, and transport-ready HTTP request arguments or CLI argv.
  Produces: `RunState.run_id: str`, `migrate_and_persist_state(path: Path, state: RunState) -> RunState`, `UsageSink = Callable[[Mapping[str, int] | None], None]`, `CallRecorder.start_attempt(...) -> CallAttempt`, `OutboundPayload`, `AuthorizationGate.authorize(payload: OutboundPayload) -> None`, default `NoOpAuthorizationGate`, eval-only `SocketAuthorizationGate`, `CallAttempt.attach_usage(...) -> None`, and `CallAttempt.finish(...) -> None`. HTTP and CLI providers call the gate after constructing the exact application payload and before opening a socket or process. Event stages use the six routing slot names; the `brainstorming_reviewer` checkpoint maps to the `reviewer` slot.

Test scenarios:
  happy: fresh state persists schema v2 before the first provider call; v1 resume persists one UUID and every later resume reuses it. **Covers AE4, AE5**
  edge: process-safe append locking produces complete JSON Lines records; nullable task metadata and unavailable usage remain null; UTF-8 token estimates are deterministic; a trailing partial row is quarantined under the project lock before the next append; the no-op gate preserves normal behavior; one socket-gated process continues at the same call after authorization.
  error: malformed UUID, unsupported schema, failed migration persistence, a forced append interruption, missing finalized events, rejected payload authorization, and an authorization timeout fail before transport; recorder write failure only warns during a product call.
  integration: an instrumentation-only synthetic run emits one event per routing invocation and produces the baseline manifest without changing routing classification. **Covers S6**

Steps:
  1. Write failing state migration, call-event, and authorization-gate tests. Include provider and transport spies that remain untouched when migration or authorization fails.
  2. Run the focused tests; confirm schema v1 lacks `run_id` and the recorder module is absent.
  3. Implement fresh v2 persistence and v1 migration under the existing project lock. Add content-free attempt recording, optional HTTP usage reporting, a default-no-op transport gate, and the eval-only socket gate. Wire the runner so one process suspends and continues at the same transport call without changing provider selection.
  4. Commit: `feat(observability): add stable call event identity`
  5. Create a clean detached worktree at that full commit. Direct all corpus output to the feature worktree's external `.release-loop/runs/stabilize-routing-locking/evidence/U2/` root, never inside the detached checkout. Verify the detached tracked tree is clean before and after, then record commit, source-tree digest, contract digest, command digest, and `dirty: false` in the external manifest and release ledger.

Acceptance: focused tests pass. The evidence manifest names the exact U2 commit recorded in the release ledger; production classification still reports `proxy_model` for all three reserved literals. No event contains prompt or response content. **Covers AE6**

## U3: Adapt and validate legacy direct-vendor providers

Execution note: test-first

Files:
  Create: none
  Modify: `src/ai_orchestration/providers/legacy_api.py`, `src/ai_orchestration/providers/base.py`, `tests/test_legacy_api.py`, `tests/test_providers.py`
  Test: `tests/test_legacy_api.py`, `tests/test_providers.py`

Interfaces:
  Consumes: `APIBasedTool.generate(prompt: str, system_prompt: str | None) -> APIResponse`, `APIResponse.usage`, `LLMToolFactory.create_api_tool(tool_type: ToolType)`, `UsageSink`, `AuthorizationGate`, `flatten_json_schema()`, and existing JSON extraction helpers.
  Produces: `LegacyProviderAdapter(Provider)`, `validate_direct_vendor(tool_type: ToolType, environ: Mapping[str, str]) -> None`, `create_direct_vendor_provider(tool_type: ToolType, usage_sink: UsageSink | None = None, authorization_gate: AuthorizationGate | None = None) -> LegacyProviderAdapter`, and mapped `AuthenticationError`, `TransportError`, `ModelFaultError`, or caused `ProviderError`. The adapter calls the gate with its exact vendor application payload before `generate()`.

Test scenarios:
  happy: all three vendors preserve text, system prompts, usage callbacks, flattened structured schemas, and transport-adjacent authorization. **Covers AE1**
  edge: malformed structured text uses the common extraction path and reports schema validation without vendor-specific parsing.
  error: missing extras or credentials raise `ConfigError` before socket creation; 401/403, timeout, 429/5xx, and unknown SDK errors map to the exact shared classes. **Covers AE3**
  integration: existing compatibility factory tests remain unchanged and the adapter satisfies the runtime-checkable `Provider` protocol. **Covers S2**

Steps:
  1. Write failing vendor matrix tests with fake tools, denied socket creation, fake SDK exception shapes, and a usage-sink spy.
  2. Run them; confirm no shared-provider adapter or startup validator exists.
  3. Implement the smallest adapter and validator around the existing factory, usage sink, and extraction utilities.
  4. Run legacy and provider tests; confirm no optional SDK import happens before the selected vendor is validated.
  5. Commit: `feat(providers): adapt legacy vendor tools`

Acceptance: `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_legacy_api.py tests/test_providers.py` passes with socket creation denied in preflight cases. Adapter calls report usage without changing `Provider` return types. **Covers AE1, AE3**

## U4: Atomically reserve and route direct-vendor slots

Execution note: test-first

Files:
  Create: none
  Modify: `src/ai_orchestration/config.py`, `src/ai_orchestration/providers/routing.py`, `src/ai_orchestration/cli.py`, `tests/test_config.py`, `tests/test_routing.py`, `tests/test_cli.py`
  Test: `tests/test_config.py`, `tests/test_routing.py`, `tests/test_cli.py`

Interfaces:
  Consumes: `_KNOWN_BINARIES`, the three reserved literals, raw stage overrides, `CatalogStatus`, HTTP, CLI, and direct-vendor factories, `CallRecorder`, `AuthorizationGate`, and shared provider failures.
  Produces: `SlotKind.DIRECT_VENDOR`, `select_stage_config(...) -> StageConfig`, `validate_stage_config(stage_name: str, stage: StageConfig, catalog: CatalogStatus) -> None`, `requires_catalog_probe(stages: Mapping[str, StageConfig]) -> bool`, three-kind routing functions, and typed reserved-catalog warnings. Startup order is six selects, pre-probe reserved fallback rejection, zero or one catalog probe, six validations, direct-vendor preflight, then provider execution.

Test scenarios:
  happy: every reserved primary bypasses catalog membership and uses the matching direct-vendor factory in text and structured paths. **Covers AE1**
  edge: direct-only startup probes zero times; mixed startup probes once; catalog collision warns; direct transport failure uses only `fallback_binary`; attempt ordinals are one then two.
  error: every reserved `fallback_model` fails before a probe; direct authentication and model faults never fall back; no committed state can route a reserved primary to `HttpProvider`. **Covers AE3**
  integration: CLI flags, config shapes, proxy IDs, CLI binaries, fallback policies, and SDK-internal retry treatment remain unchanged. **Covers S1, Covers S2, Covers S3**

Steps:
  1. Write failing split-selection, classification, fallback rejection, probe-count, direct-factory, fallback-policy, and attempt-order tests.
  2. Run them; confirm reserved values classify as proxy models and production sends them to `HttpProvider`.
  3. Implement selection before validation and commit classification, routing, startup preflight, and CLI wiring as one atomic code change.
  4. Run focused tests; confirm every reserved route works and all proxy and CLI regression rows remain green.
  5. Commit: `feat(routing): route reserved vendor slots directly`

Acceptance: `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_config.py tests/test_routing.py tests/test_cli.py tests/test_legacy_api.py tests/test_providers.py` passes. Direct-only startup has zero catalog calls, mixed startup has one, and reserved values never reach `HttpProvider`. **Covers AE1, AE2, AE3**

## U5: Prove full-path routing and candidate observability

Execution note: test-first

Files:
  Create: none
  Modify: `tests/integration/test_user_scenarios.py`, `tests/test_call_events.py`, `tests/test_dogfood_eval.py`, `scripts/run_dogfood_eval.py`
  Test: `tests/integration/test_user_scenarios.py`, `tests/test_call_events.py`, `tests/test_dogfood_eval.py`

Interfaces:
  Consumes: the complete CLI entrypoint, fake provider seam, call events, baseline manifest, direct-vendor preflight, and the local `C1-R` trial contract.
  Produces: end-to-end scenario evidence, candidate routing observations, and a sanitized local candidate fragment for later U6 aggregation.

Test scenarios:
  happy: a fake direct-vendor stage runs through CLI resolution and records provider kind, reserved model, usage, and success. **Covers AE1**
  edge: a mixed proxy/direct configuration probes once and preserves all stage-slot names in events.
  error: missing Anthropic configuration exits with typed `ConfigError`, performs one required proxy probe, starts no vendor request, and writes no provider event. **Covers AE3**
  integration: separate CLI scenarios walk S1, the complete S2 startup path, and existing proxy/CLI S3 behavior. **Covers S1, Covers S2, Covers S3**

Steps:
  1. Write failing CLI scenarios for direct success, mixed missing-requirement failure, and proxy/CLI regression.
  2. Run them; confirm no current scenario walks the complete direct-vendor startup path.
  3. Add only test seams and local eval aggregation needed to observe the shipped U4 behavior.
  4. Run scenario, event, and local eval tests; confirm one finalized event exists per routing invocation.
  5. Commit: `test(routing): prove direct vendor user scenarios`

Acceptance: `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/integration/test_user_scenarios.py tests/test_call_events.py tests/test_dogfood_eval.py` passes. The S2 scenario proves typed startup failure, one mixed catalog probe, zero vendor requests, and zero provider events. **Covers AE1, AE2, AE3**

## U6: Prove live locking and prepare the candidate eval

Execution note: test-first

Files:
  Create: `tests/integration/live_run_lock_child.py`, `tests/integration/test_live_run_lock.py`
  Modify: `.github/workflows/orchestrator-tests.yml`, `src/ai_orchestration/cli.py`, `scripts/run_dogfood_eval.py`, `tests/test_dogfood_eval.py`
  Test: `tests/integration/test_live_run_lock.py`, `tests/test_dogfood_eval.py`

Interfaces:
  Consumes: three real `uv run ai-orchestration` processes, `AI_ORCHESTRATION_FAKE_PROVIDERS=1`, a test-only planner barrier disabled by default, persisted planner-boundary v2 state, full project-tree manifests, a network spy, process groups, and baseline build identity.
  Produces: bounded holder, contender, and resumer results; contender no-mutation evidence; deterministic candidate manifest; and exact pilot, full, and grading approval packets.

Test scenarios:
  happy: a third process resumes at the saved planner boundary after holder release. **Covers AE5**
  edge: normal controlled provider failure and forced orchestrator kill both release the directory lock; Ubuntu and macOS run the same fixture. Whole-run descendant supervision remains Cycle 5 scope.
  error: contender exits 1, prints the lock diagnostic, invokes no provider, performs no network call, and leaves the complete durable project-tree manifest unchanged. **Covers AE4**
  integration: the local nine-trial corpus compares the U2 baseline identity with the candidate and emits the exact V1 command without sending data. **Covers S4, Covers S5, Covers S6**

Steps:
  1. Write the failing three-entrypoint fixture with bounded waits, test-only planner barrier, project-tree hashing, network and provider spies, and process-group cleanup.
  2. Run it; confirm existing in-process lock tests do not supply the required process evidence.
  3. Add the disabled-by-default barrier and CI OS matrix without changing `acquire_run_lock()`.
  4. Commit: `test(locking): prove live process exclusion`
  5. Create a clean detached worktree at that full commit. Direct all output to the feature worktree's external `.release-loop/runs/stabilize-routing-locking/evidence/U6/` root. Verify the detached tracked tree is clean before and after, then record commit, tree, contract, and command digests with `dirty: false` in the external manifest and release ledger.

Acceptance: `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/integration/test_live_run_lock.py tests/test_dogfood_eval.py` passes on POSIX. Both termination cases leave no owned test orchestrator process. The candidate manifest validates, and V1 packet generation is offline. **Covers AE4, AE5, AE6**

# Mutation/failure-state matrix

The matrix follows `skills/planning/references/stateful-ceremony-matrix-example.md`. A changed approved row requires the deviation process in `docs/solutions/workflow-issues/review-introduced-state-machine-deviation.md`. Disposable evidence belongs under `.release-loop/runs/stabilize-routing-locking/evidence/U<N>/`.

| Transition | Pre-state | Action | Expected post-state | Owner | Evidence owner | Success | Forced failure | Rerun | Rollback or compensation | Headless | Cancellation or abort |
|---|---|---|---|---|---|---|---|---|---|---|---|
| T1 fresh v2 persistence | No state file; project lock held | Generate UUID and atomically save schema v2 | One readable v2 file exists before any provider invocation | U2 | `tests/test_state.py::test_fresh_v2_persists_before_provider` | v2 file exists and provider spy starts afterward | `test_fresh_state_replace_failure_blocks_provider` patches `os.replace` in a temp project; no final state, event, or provider call exists; U2 removes stale temp files | A rerun creates one new run ID because no valid state was committed | Remove isolated temp evidence; no production reverse transition exists | Auto mode follows the same locked save | Kill-window fixture accepts either no final file or one valid v2 file; next locked startup removes stale owned temp files |
| T2 v1 run-state migration | Valid schema v1 file; project lock held | Generate UUID and atomically save schema v2 | One readable v2 file exists before any provider invocation | U2 | `tests/test_state.py::test_v1_migration_persists_before_provider` | v2 file and no provider call before save | `test_v1_migration_replace_failure_preserves_v1` patches `os.replace`; v1 stays byte-identical, no event or provider call occurs, and U2 cleans temp files | Resume reuses the stored UUID and performs no second migration | Restore the untouched v1 fixture; no production reverse migration exists | Non-interactive resume follows the same path | Kill before replace leaves readable v1; kill after replace leaves readable v2; the next resume handles either state |
| T3 call-event append | Valid v2 state and writable event directory | Finalize one routing attempt under the event-file lock | One complete content-free JSON line identifies the attempt | U2/U4 | `tests/test_call_events.py::test_process_safe_append_and_partial_tail_recovery` | Validator finds exactly one finalized row per invocation | The test kills an isolated writer after a bounded partial append; the next locked writer quarantines only the trailing partial bytes, preserves complete rows, and U2 owns cleanup | A new invocation appends a new event and never edits a complete row | Eval rejects the affected trial; quarantine evidence remains and a fresh receipt owns any external rerun | Auto modes emit the same fields without content | Kill before finalization leaves no complete event or a quarantined trailing fragment; the eval marks the trial incomplete |
| T4 holder lock lifecycle | Project path exists and no process holds its directory | Real holder entrypoint acquires lock and reaches the test-only planner barrier | Contender cannot mutate durable project files; release permits a real resumer | U6 | `tests/integration/test_live_run_lock.py` | Controlled holder failure releases lock and resumer enters planner | `test_resume_after_holder_release[forced_kill]` kills the isolated orchestrator after ready; OS releases lock, the tree stays valid, and U6 removes test controls | Repeating creates a fresh temp project and evidence root | The lock algorithm is unchanged; fixture process-group cleanup compensates | All three entrypoints are non-interactive and bounded | Pytest `finally` terminates owned orchestrator groups and records cleanup; detached-provider supervision is excluded as Cycle 5 work |
| T5 approval receipt consumption | Closed packet and unused current-session receipt with mode `0600` | Atomically reserve and consume the packet-bound receipt | Exactly one matching runner owns the receipt; replay is impossible | U1/V1 | `tests/test_dogfood_eval.py::test_receipt_is_packet_bound_and_one_shot` | One matching invocation reserves then marks consumed | The test covers stale, tampered, wrong-session, wrong-scope, replayed, and concurrent receipts; all fail before model execution and U1 removes reservations | No receipt rerun exists; a first-hand USER approval creates a new nonce | An unused reservation can be invalidated; a consumed external authorization is irreversible and remains evidence | Missing receipt exits 1 without prompting | Interruption after reservation invalidates that receipt; a fresh approval is required before any call |
| T6 live eval generation | U1–U6 committed; preflight closed; matching receipts available | Run approved pilot, full remainder, and separately approved grading | Complete manifests and sanitized comparison report exist | V1 | First-hand release-loop session | Every task/build pair is 3/3 and thresholds are evaluated | `tests/test_dogfood_eval.py::test_failed_live_trial_never_completes_generation` injects a failing command in an isolated root; partial state names the trial, no completion marker exists, and V1 owns cleanup | Completed trial IDs remain; changed commands or failed external trials require a new receipt | External calls are irreversible; preserve sanitized evidence and perform manual cost review | The runner rejects missing receipts and never prompts | SIGINT/SIGTERM records interruption and blocks retry; V1 audits owned PIDs and requires manual cleanup evidence before a fresh receipt |

# Carry-forward trigger audit

| Tracker row | Trigger class | What fired it | Disposition |
|---|---|---|---|
| `teslamint/ai-orchestration#3` | event-based | Cycle 1 planning started for the exact routing, locking, and dogfooding scope | Fold into U1 through U6 and V1 because every acceptance condition belongs to this cycle. |

Issues `#4`, `#5`, `#6`, and `teslamint/compound-loop#28` are event-based dependency rows. Their triggers are the completion of their declared prerequisite bundles. None has fired at planning time. U1 may satisfy part of `#4`, `#5`, and `#28`; U2 may satisfy part of `#5` and `#6`; U6 may satisfy part of `#6`. The release ledger must repeat this audit after U1, U2, U6, and before Ship. Partial dependency satisfaction does not start another cycle.

Audited `https://github.com/teslamint/ai-orchestration/issues` and `https://github.com/teslamint/compound-loop/issues/28` at the GitHub states observed on 2026-08-28: 5 open rows, 1 fired, 0 unobservable.

# Deferred to Follow-Up Work

- `teslamint/compound-loop#28`: Cycle 2 owns host Git-signing isolation and test-integrity enforcement.
- `teslamint/ai-orchestration#4`: Cycle 3 owns embedded compound-loop contracts and gated phases.
- `teslamint/ai-orchestration#5`: Cycle 4 owns prompt payload measurement and stage-message unification beyond the content-free metrics required here.
- `teslamint/ai-orchestration#6`: Cycle 5 owns concurrent whole-run supervision. Cycle 1 preserves same-project exclusion.
- General vendor SDK redesign and new vendor models remain outside this release.

# Open unknowns

## Planning-time

None. The plan fixes token estimation, event enums, catalog-collision behavior, normal lock-release fixture behavior, and the number of external calls.

## Implementation-time

- Fake vendor exception classes must follow the installed optional SDK versions. U3 resolves exact attribute inspection without importing an unselected SDK.
- macOS and Ubuntu process scheduling may require different bounded timeout values. U6 may raise a timeout only when CI evidence shows the current bound is insufficient.
- Cost remains `not_measurable` unless both paired builds expose usage and the approved packet names a pinned price snapshot.

# Release-loop pre-merge verification V1: Run the approved dogfood corpus

Matrix rows: T5, T6

Owner: the first-hand release-loop orchestrating session after Review is clean and before Shipping reaches the merge gate.

State: `.release-loop/runs/stabilize-routing-locking/evidence/V1/live-eval-state.json` stores preflight status, baseline and candidate commit IDs, packet and receipt digests, task/trial status, owned process IDs, cleanup evidence, usage, latency, rubric scores, and the final manifest digest. Durable state, packets, receipts, and reservations use mode `0600` and store no credential, prompt content, response content, or unbounded output. One bounded `0600` payload file may hold exact outbound bytes while its live process waits. The runner deletes it immediately after authorization, rejection, timeout, or normal cancellation. Preflight removes an orphaned payload file only after proving its recorded owner process is absent.

Precondition: U1 through U6 are committed. The full deterministic gate passes. `uv run python scripts/run_dogfood_eval.py preflight --contract evals/dogfood/v1 --baseline-manifest .release-loop/runs/stabilize-routing-locking/evidence/U2/baseline-manifest.json --candidate-manifest .release-loop/runs/stabilize-routing-locking/evidence/U6/candidate-manifest.json` returns `adapter_eligibility: closed`, verifies both full commit IDs, proves output-root isolation, and reports every model destination and missing credential without calling a model.

Action:

1. Run `uv run python scripts/run_dogfood_eval.py approval-packet --scope pilot --contract evals/dogfood/v1 --baseline-manifest .release-loop/runs/stabilize-routing-locking/evidence/U2/baseline-manifest.json --candidate-manifest .release-loop/runs/stabilize-routing-locking/evidence/U6/candidate-manifest.json --output .release-loop/runs/stabilize-routing-locking/evidence/V1/pilot-packet.json`.
2. Present every destination and model, maximum routing invocations, timeout, retry limit, token cap, cost cap or unavailable status, environment variable names, output root, and command digest. After first-hand USER approval, write a packet-bound current-session receipt and run `uv run python scripts/run_dogfood_eval.py pilot --packet .release-loop/runs/stabilize-routing-locking/evidence/V1/pilot-packet.json --receipt .release-loop/runs/stabilize-routing-locking/evidence/V1/pilot-receipt.json --state .release-loop/runs/stabilize-routing-locking/evidence/V1/live-eval-state.json`.
3. Before each routing-layer provider invocation, the same live process suspends at its authorization socket and emits the content-free path `.release-loop/runs/stabilize-routing-locking/evidence/V1/next-invocation-packet.json` plus a bounded payload-file path. Read and present the exact outbound payload bytes, destination, model, timeout, retry, token, and cost bounds. After first-hand approval, run `uv run python scripts/run_dogfood_eval.py authorize-invocation --packet .release-loop/runs/stabilize-routing-locking/evidence/V1/next-invocation-packet.json --receipt .release-loop/runs/stabilize-routing-locking/evidence/V1/next-invocation-receipt.json --state .release-loop/runs/stabilize-routing-locking/evidence/V1/live-eval-state.json`. The suspended process verifies the receipt and payload digest, deletes the payload file, and makes exactly that call without restarting the provider or stage. The runner uses a minimal child environment allowlist and rejects symlinked roots or payload bytes matching any loaded credential value or secret pattern before egress.
4. Run `uv run python scripts/run_dogfood_eval.py approval-packet --scope full --contract evals/dogfood/v1 --baseline-manifest .release-loop/runs/stabilize-routing-locking/evidence/U2/baseline-manifest.json --candidate-manifest .release-loop/runs/stabilize-routing-locking/evidence/U6/candidate-manifest.json --output .release-loop/runs/stabilize-routing-locking/evidence/V1/full-packet.json`. Present four remaining orchestrator trials: baseline and candidate `C1-E-02` and `C1-E-03`. The packet also lists all local `C1-R` and `C1-L` commands and retains both approved `C1-E-01` results.
5. After separate first-hand USER approval, write the full receipt and run `uv run python scripts/run_dogfood_eval.py full --packet .release-loop/runs/stabilize-routing-locking/evidence/V1/full-packet.json --receipt .release-loop/runs/stabilize-routing-locking/evidence/V1/full-receipt.json --state .release-loop/runs/stabilize-routing-locking/evidence/V1/live-eval-state.json`. Apply step 3 to every full-run provider invocation.
6. Write blinded artifact bytes and digests to `.release-loop/runs/stabilize-routing-locking/evidence/V1/blinded-artifacts.json`. Run `uv run python scripts/run_dogfood_eval.py grading-packet --state .release-loop/runs/stabilize-routing-locking/evidence/V1/live-eval-state.json --artifact-manifest .release-loop/runs/stabilize-routing-locking/evidence/V1/blinded-artifacts.json --output .release-loop/runs/stabilize-routing-locking/evidence/V1/grading-packet.json`. Present the exact outbound grading payload bytes, destination, model, and caps. After separate approval, run `uv run python scripts/run_dogfood_eval.py grading --packet .release-loop/runs/stabilize-routing-locking/evidence/V1/grading-packet.json --receipt .release-loop/runs/stabilize-routing-locking/evidence/V1/grading-receipt.json --state .release-loop/runs/stabilize-routing-locking/evidence/V1/live-eval-state.json`. Ask the USER to adjudicate any dimension difference greater than one.
7. Finalize only when every task/build pair is 3/3, every owned process exited, every call event validates, every receipt was consumed once, and the aggregate report passes or contains a USER-approved latency exception.

Resume: retain completed local and external trial IDs. Never reuse a receipt or reservation. Any payload, destination, model, cap, command, retry, environment allowlist, artifact digest, or build commit change requires a new packet and first-hand approval. An interrupted external trial requires PID cleanup evidence and a fresh receipt before retry.

Acceptance: `.release-loop/runs/stabilize-routing-locking/evidence/V1/complete.json` exists. Its manifest proves 9/9 candidate stability, at least 8/9 usable candidate artifacts, no zero rubric dimension, valid content-free call events, no secret disclosure, paired median latency within 25 percent or a USER-approved exception, cost measured or explicitly `not_measurable`, and complete process cleanup. Shipping remains blocked without this file and its recorded SHA-256.

Failure behavior: infrastructure failure, product failure, quality failure, and missing comparability remain distinct. Any failure leaves V1 incomplete and blocks Ship. No incomplete or unapproved generation can satisfy this gate.
