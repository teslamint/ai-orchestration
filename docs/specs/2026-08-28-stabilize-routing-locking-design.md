---
title: Stabilize Legacy API Routing and Live Run Locking
status: approved
date: 2026-08-28
schema: spec/v1
---

# Stabilize Legacy API Routing and Live Run Locking Design

_Created 2026-08-28._

## Overview

This cycle resolves one routing ambiguity and closes one concurrency evidence gap. It also creates the shared dogfooding eval contract for five planned release cycles.

The exact values `gemini_api`, `openai_api`, and `anthropic_api` select legacy direct-vendor providers. They never select proxy models. The process lock keeps its current behavior, but a real two-process test must prove it.

## User Scenarios

### S1: Select a legacy direct-vendor stage

An operator runs `ai-orchestration "goal" --planner openai_api`. The planner uses the legacy OpenAI adapter. The CLIProxyAPI catalog does not validate or reinterpret this reserved value.

### S2: Fail safely when a legacy provider is unavailable

An operator selects `anthropic_api` without its optional dependency or API key. Startup fails with `ConfigError` that names the missing requirement. That slot bypasses proxy catalog validation and no direct-vendor request starts. Other proxy-valued stages may still require the one startup catalog probe.

### S3: Preserve proxy and CLI routing

An operator selects `gpt-5.5` or `codex`. A normal model identifier still selects CLIProxyAPI. An exact supported binary name still selects its subprocess provider.

### S4: Reject a simultaneous run for one project

One process holds the run lock for a project. A second live process targets the same project, exits 1, and prints the `RunLockedError` diagnostic. The second process does not mutate durable state.

### S5: Resume after lock release

The first process stops or finishes. A later process uses `--resume` for the same project. The process acquires the released lock and continues from the durable stage boundary.

### S6: Compare dogfooding quality before release

A maintainer runs the fixed dogfooding corpus against the baseline and candidate builds. The report compares stability, output quality, cost, and latency before Ship.

## Scope

### In

- R1: Reserve the three `*_api` literals as a distinct direct-vendor slot kind.
- R2: Route each reserved literal through a provider that satisfies the shared provider protocol.
- R3: Preserve current proxy model and exact CLI binary behavior.
- R4: Map missing credentials and optional dependencies to typed failures before a live request.
- R5: Apply existing fallback policy by fault class. Authentication failures never fall back.
- R6: Reject every reserved direct-vendor literal in `fallback_model`, regardless of primary slot kind.
- R7: Prove project locking with two live orchestrator processes.
- R8: Version the shared dogfooding dataset, graders, thresholds, and result schema.
- R9: Add per-call instrumentation before baseline capture.
- R10: Record an instrumentation-only baseline and candidate distribution before Ship.

### Out

- New vendor models or vendor SDK redesigns.
- Test-integrity review-lane changes in the compound-loop repository.
- Compound-loop contract embedding.
- Parallel execution of independent runs.
- Stage-message unification.
- Automatic branch or worktree mutation.

## Assumptions and Preconditions

| Claim | Command | Observed at | Observed result | Evidence source |
|---|---|---|---|---|
| The baseline package suite passes in an isolated Git environment. | `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q` | `2026-08-27T15:51:43Z` | 373 passed and 2 skipped. | Isolated feature worktree at `a39998dc5fe297c9f2a9f32db42bd0100c2233fd` |
| The default host Git configuration makes one fixture test depend on a Secretive signing key. | `UV_CACHE_DIR=.uv-cache uv run pytest -q` | `2026-08-27T15:52:33Z` | 372 passed, 2 skipped, and 1 fixture commit failed before test assertions. | Main checkout test output; retained as Cycle 2 input |
| All three CLI provider binaries exist on the current host. | `command -v agy; command -v codex; command -v claude` | `2026-08-27T15:45:00Z` | All commands resolved under `/opt/homebrew/bin`. | Isolated feature worktree shell |
| External-model dogfooding requires explicit egress approval. | `uv run ai-orchestration "Create an evaluation-first design package for stabilizing legacy API stage-slot routing and live process-level run locking in ai-orchestration. Produce DESIGN.md and EVAL_PLAN.md only. Define exact behavior, deterministic regression tests, a repeated live dogfooding dataset, graders, thresholds, cost and latency metrics, and failure evidence. Do not modify the source repository." --workspace /private/tmp/ai-orchestration-cycle1-dogfood --project-name cycle1_dogfood_baseline --brainstormer agy --reviewer codex --planner codex --executor claude --code-reviewer codex --fixer claude --auto-select --auto-run --auto-approve --auto-fix --debug --debug-log /private/tmp/ai-orchestration-cycle1-dogfood/baseline-debug-escalated` | `2026-08-27T15:46:57Z` | The sandbox attempt failed on the `agy` log path. The escalated attempt was rejected pending payload and destination approval. | Release-loop progress ledger |

The API extras and vendor credentials are not available in the current shell. Tests must use deterministic adapters until a USER authorizes a live vendor probe.

## Routing Decision

### Approach A: First-class direct-vendor slots

The three reserved literals select direct-vendor providers through one explicit slot kind. This approach preserves the documented compatibility contract and prevents proxy reinterpretation.

### Approach B: Reject legacy literals in stage slots

The new stage configuration rejects the three values. The compatibility factory remains available only to old callers. This approach is smaller, but it breaks documented tool-config behavior.

### Approach C: Keep proxy reinterpretation

The three values remain arbitrary proxy model identifiers. Documentation removes them from the stage tool list. This approach preserves current runtime code but contradicts the retained legacy-provider contract.

**Recommendation:** Use Approach A. Existing provider enums, factories, tests, and documentation already identify these literals as direct-vendor adapters.

## Architecture

### Routing boundary

The configuration layer classifies a stage value as a CLI binary, proxy model, or legacy direct-vendor provider. The routing layer consumes that classification. It does not duplicate string checks.

Reserved direct-vendor values bypass CLIProxyAPI catalog membership validation. Startup performs at most one catalog probe when any resolved primary or fallback slot uses a proxy model. The probe is skipped when every resolved slot is a CLI binary or direct-vendor value.

`LegacyProviderAdapter` wraps one existing `APIBasedTool`. It implements `Provider.complete()` by forwarding `prompt` and `system` to `generate()`. It implements `complete_structured()` by sending the flattened schema instruction and passing the returned text through the existing common extraction and Pydantic validation path. It does not add a vendor-specific structured parser.

### Fault boundary

The direct-vendor provider validates its optional dependency and credential during startup, before provider construction or a network request. Missing configuration raises `ConfigError`.

The adapter maps runtime SDK failures by observable cause:

- Vendor 401 or 403 responses raise `AuthenticationError`.
- DNS, connection, and timeout failures raise `TransportError`.
- Vendor 429, 5xx, and unusable response failures raise `ModelFaultError`.
- Unknown SDK failures raise `ProviderError` and preserve the original exception as the cause.

- Authentication failures are terminal.
- Transport failures can use `fallback_binary`.
- Model failures are terminal for a legacy primary because `fallback_model` is invalid.
- Structured output uses the common extraction and schema-validation path.

### Call event boundary

The existing `Provider` return types remain unchanged. A concurrency-safe `CallRecorder` is injected into routing and provider factories. Routing opens and finalizes one attempt token for every primary or fallback call. Providers attach reported usage to that token when their transport exposes it. The legacy adapter copies `APIResponse.usage`; providers without usage attach `null`.

Each append-only JSON Lines event uses `call-event/v1`. It contains `run_id`; nullable `task_id`; nullable `executor_step_id`; stage; provider kind; model or binary; routing-attempt ordinal; prompt bytes; estimated input tokens; reported input and output tokens; nullable transport retry count; latency milliseconds; terminal outcome; and typed error kind. It never stores prompt or response content.

`task_id` is the dogfood dataset ID supplied by the eval harness and is `null` during normal product runs. `executor_step_id` is the persisted plan step ID for executor calls and is `null` for other stages. Routing-attempt ordinal starts at one for each stage-completion request and increments for each fallback model or binary invocation. An SDK-internal HTTP retry remains part of that one routing attempt; it does not create another event. `transport_retry_count` is `null` when the SDK does not expose it. The missing-event gate expects one finalized event per routing-layer provider invocation.

Run-state schema v2 adds required UUID `run_id`. A fresh run generates and atomically saves v2 under the project lock before its first provider call. On `--resume`, loading v1 generates one UUID and atomically saves the migrated v2 under the same lock before emitting an event or calling a provider. If migration persistence fails, the run exits before provider execution. Every later resume reuses the persisted UUID.

Events are written under `<project>/.ai_orchestration/call_events.jsonl`. A recorder write failure emits a warning and does not change provider output or fallback behavior. The eval gate fails when an expected event is missing or malformed.

### Lock evidence boundary

Production locking remains on the project workspace directory. A POSIX-only subprocess integration test starts one holder and one contender. A test provider blocks the holder at the planner boundary after it writes a ready signal. The fixture records state SHA-256 and provider invocations, then starts the contender.

The contender must exit 1 and print `Error: another run already holds the lock for`. It must not invoke a provider or change the state digest. The fixture terminates the holder by normal release and forced kill in separate parameterized cases. A third process then acquires the lock with `--resume` and starts at the saved planner boundary. Every child has a bounded timeout and `finally` cleanup.

### Eval boundary

The repository stores `evals/dogfood/v1/dataset.json`, `grader.yaml`, `call-event.schema.json`, `result.schema.json`, and sanitized summaries. Raw model logs remain local unless they pass the repository secret-pattern scanner and a configured size limit. The eval contract fixes dataset version, grader version, trial IDs, and environment fingerprint.

Instrumentation lands before baseline capture. Each model call records run ID, task ID, stage, provider, model, attempt, prompt bytes, estimated input tokens, reported usage when available, latency, and terminal outcome. Cost is available only when a pinned price snapshot supports the model and both builds report comparable usage.

The first eval version contains three fixed tasks:

1. `C1-R` observes classification and factory selection for every reserved legacy API literal and one ordinary proxy model without making a network request.
2. `C1-L` exercises barrier-controlled contention, killed-holder release, and resume.
3. `C1-E` produces a bounded artifact for end-to-end quality scoring.

Each build runs three trials per task. `C1-R` expects `proxy_model` for the instrumentation-only baseline and `direct_vendor` for the candidate. `C1-L` expects lock exclusion in both builds. `C1-E` expects successful artifact generation in both builds. Every trial produces a report artifact, including an expected baseline mismatch with the candidate contract.

The report records baseline and candidate Git commit IDs, provider configuration digest, terminal state, artifact manifest, test result, rubric score, elapsed time, and available usage data. Quality includes every completed report artifact. Latency compares paired task/trial IDs only when both runs reach their declared terminal state. Cost compares only paired `C1-E` trials with reported usage and pinned pricing. A blinded reviewer from a different model family scores artifacts when available. A USER adjudicates score disagreements greater than one point. The cost gate reports `not_measurable` when usage or pinned pricing is unavailable; that state cannot pass or fail the release by cost alone.

## Interface

The existing stage flags and tool-config fields remain unchanged. Their accepted value semantics become explicit:

- `agy`, `codex`, and `claude` select CLI providers.
- `gemini_api`, `openai_api`, and `anthropic_api` select direct-vendor providers.
- Every other non-empty string selects a CLIProxyAPI model.

The direct-vendor values are reserved in every stage field. A proxy catalog entry or `fallback_model` with the same identifier cannot override them. This is an intentional compatibility correction: configurations that used one of these literals as a proxy model must rename that proxy catalog ID before upgrading. Startup prints a typed migration diagnostic when a reachable proxy catalog contains a conflicting reserved ID.

## Data Model

The slot-kind vocabulary gains one direct-vendor value. Persisted run configuration continues to store the original stage string. Resume reclassifies it under the same reserved-literal contract.

The eval result format has one version field. It stores task ID, trial ID, build identity, expected and observed terminal status, comparability status, stability checks, rubric dimensions, latency, usage, cost status, and artifact checks. `cost_status` is `measured` or `not_measurable`. Run-state schema v1 remains readable only through the one-way v1-to-v2 migration; writers emit v2 only.

## Testing

### Deterministic TDD suite

- RED tests prove all three legacy values currently classify as proxy models.
- Routing tests prove each reserved value selects the correct direct-vendor adapter.
- Negative tests prove missing extras and credentials fail before a network call.
- Adapter matrix tests cover all three vendors across text and structured paths, including system-prompt preservation.
- Fault tests cover missing dependency, missing credential, 401/403, timeout, 429/5xx, direct-vendor `fallback_binary`, and prove authentication never falls back.
- Validation tests reject every reserved literal in `fallback_model` for proxy, CLI, and direct-vendor primaries.
- Proxy and CLI regression tests preserve CLI flag, bare config, object config, and resume classification.
- State tests prove fresh v2 persistence, atomic v1 migration before provider execution, stable `run_id` across resume, and typed failure when migration cannot persist.
- Call-event tests prove nullable task fields, routing-attempt ordinals across both fallback paths, SDK-internal retry treatment, and fail-open recorder behavior with a fail-closed eval result.
- Spies prove reserved slots bypass catalog membership and select the legacy provider factory.
- A live subprocess test proves lock exclusion, byte-stable state, provider non-invocation, release, and resume.
- The process test runs on macOS and Linux CI targets with `fcntl.flock`; unsupported platforms skip it with the POSIX deviation identifier.

### EDD suite

The eval specification, JSON schemas, deterministic smoke expectations, graders, thresholds, and instrumentation land before production routing and locking changes. The instrumentation-only commit supplies the baseline. CI runs deterministic smoke evals. Release verification runs the repeated live corpus after explicit egress approval.

The artifact rubric has five dimensions. Each dimension scores zero, one, or two:

1. Requirement coverage.
2. Executable correctness.
3. Test quality.
4. Maintainability.
5. Evidence clarity.

A usable artifact scores at least 8 of 10. No dimension can score zero.

## Risks and Mitigations

- A proxy may use a reserved identifier. The reserved-literal rule is explicit and tested.
- Vendor adapters expose different SDK errors. The shared provider taxonomy contains the difference.
- Optional API extras may be absent. The adapter fails before import-time side effects or a network call.
- A real-process lock test can hang. Every child process has a bounded timeout and forced cleanup.
- Live evals can leak repository data. The USER approves the exact payload and model destinations before execution.
- Stochastic evals can become expensive. The release corpus is fixed at nine live runs per build, with blind run IDs and a pinned grader version.
- Host Git settings can corrupt fixture evidence. Cycle 2 owns a general test-integrity fix.

## Success Criteria

1. The three legacy API literals select their direct-vendor adapters.
   - **Measured by**: `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_config.py tests/test_routing.py tests/test_legacy_api.py`.
2. Proxy model identifiers and exact CLI binary identifiers retain their existing behavior.
   - **Measured by**: the same focused command plus `tests/test_providers.py`.
3. Missing direct-vendor credentials or extras cause typed failures before a network call.
   - **Measured by**: focused negative tests with socket creation denied.
4. A second live process cannot mutate one project's held run state.
   - **Measured by**: the new process-level integration test and a before/after SHA-256 comparison of `run_state.json`.
5. A later process resumes after the first process releases the lock.
   - **Measured by**: the same integration test observes the expected next stage and exit status.
6. The full deterministic quality gate stays green.
   - **Measured by**: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q && UV_CACHE_DIR=/private/tmp/uv-cache uv run ruff check . && UV_CACHE_DIR=/private/tmp/uv-cache uv run ruff format --check .`.
7. Dogfooding has no stability failure across the repeated release corpus.
   - **Measured by**: 9 of 9 candidate runs have no uncaught exception, secret disclosure, or corrupt state.
8. Dogfooding output quality remains usable.
   - **Measured by**: at least 8 of 9 candidate artifacts score at least 8 of 10, with no zero dimension.
9. Cost and latency remain bounded.
   - **Measured by**: candidate median latency stays within 25 percent of comparable baseline pairs, or a USER approves the recorded exception. Cost uses the same gate only for paired `C1-E` trials with reported usage and a pinned price snapshot; otherwise `cost_status` is `not_measurable`.

## Decisions and Remaining Approval

- The USER approved Approach A at the Design gate on 2026-08-28.
- The USER must approve the exact external-model payload and destinations before live dogfooding resumes.
- Cycle 1 is tracked by `teslamint/ai-orchestration#3`. Cycles 2 through 5 are tracked by `teslamint/compound-loop#28` and `teslamint/ai-orchestration#4` through `#6`.
