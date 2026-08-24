---
schema: design/v1
title: LangChain-based Multi-model Agent Orchestration Rewrite
type: feat
status: draft
date: 2026-08-24
---

# LangChain-based Multi-model Agent Orchestration Rewrite

## Goal

Rewrite `ai-orchestration` from a 6-stage linear CLI orchestrator (Gemini → Codex → Claude → Codex review → Claude fix → Codex final review) into a LangChain-based multi-model agent orchestration tool that:
1. Uses LangChain as the runtime for LLM provider abstraction, tool calling, and agent chains
2. Embeds `compound-loop` as the release-loop/framework layer for lifecycle management
3. Supports multi-model agent workflows with parallel dispatch, review gates, and feedback loops
4. Preserves existing CLI ergonomics (`typer`-based) while modernizing internals

## Architecture

### High-level structure

```
ai-orchestration/
├── pyproject.toml                    # Updated deps: langchain-core, langchain-community, compound-loop(path)
├── src/ai_orchestration/
│   ├── __init__.py
│   ├── cli.py                        # Typer CLI entrypoint (preserves existing options)
│   ├── config.py                     # LangChain-compatible configuration (replaces OrchestratorConfig)
│   ├── models/                        # Pydantic data models (replaces orchestration_context.py)
│   │   ├── __init__.py
│   │   ├── task.py                   # Task, ExecutionLog
│   │   ├── review.py                 # CodeReviewItem, CodeReviewResult
│   │   ├── feedback.py               # RalphWiggumFeedback, IterationMetadata
│   │   └── context.py                # OrchestrationContext (composite)
│   ├── providers/                     # LangChain LLM provider wrappers
│   │   ├── __init__.py
│   │   ├── base.py                   # BaseProvider abstraction using LangChain
│   │   ├── gemini.py                 # Gemini provider (Google AI / CLI)
│   │   ├── openai.py                 # OpenAI provider (Codex CLI / API)
│   │   └── anthropic.py              # Anthropic provider (Claude CLI / API)
│   ├── agents/                        # LangChain agents for each role
│   │   ├── __init__.py
│   │   ├── brainstormer.py           # Stage 1: creative technical lead
│   │   ├── planner.py               # Stage 2: planning agent
│   │   ├── executor.py             # Stage 3: code executor
│   │   ├── reviewer.py             # Stage 4: code review
│   │   └── fixer.py                # Stage 5: fix application
│   ├── chains/                        # LangChain chains composing agent workflows
│   │   ├── __init__.py
│   │   ├── orchestration.py        # Main orchestration chain
│   │   ├── ralph_wiggum.py         # Feedback loop chain
│   │   └── ralph_loop.py           # Text review/fix loop chain
│   ├── tools/                         # LangChain tools (file ops, shell, etc.)
│   │   ├── __init__.py
│   │   ├── file_ops.py             # Read/write/edit file tools
│   │   ├── shell_ops.py            # Command execution tools
│   │   └── diff_ops.py             # Diff generation tools
│   ├── prompts/                       # LangChain prompt templates (replaces agent_prompts.py)
│   │   ├── __init__.py
│   │   └── templates.py            # All prompt templates as LangChain ChatPromptTemplate
│   ├── utils/                         # Utility functions preserved from original
│   │   ├── __init__.py
│   │   ├── slug.py                 # _generate_project_name, _generate_command_slug
│   │   ├── extract.py              # _extract_json_list, _extract_code_content, etc.
│   │   └── diff.py                 # _generate_diff
│   └── compound_loop/                 # compound-loop integration bridge
│       ├── __init__.py
│       ├── release_loop.py          # ReleaseLoopBridge class
│       ├── progress.py             # .release-loop/progress.md reader/writer
│       └── plan_validator.py        # Plan v1 contract validator
├── tests/
│   ├── __init__.py
│   ├── test_cli.py                  # CLI option tests (preserved + new)
│   ├── test_models.py              # Model tests (preserved + new)
│   ├── test_utils.py              # Utility function tests (preserved)
│   ├── test_chains.py            # New: chain composition tests
│   └── test_release_loop.py     # New: release-loop integration tests
└── workspace/                      # Existing workspace (preserved)
```

### Key design decisions

1. **LangChain as runtime**: Providers implement `BaseProvider` using LangChain's `BaseChatModel` interface. CLI tools (gemini, codex, claude) are wrapped as custom LangChain runnables. API-based tools use LangChain's built-in integrations where available.

2. **Preserved helper functions**: All utility functions (`_generate_project_name`, `_extract_json_list`, `_generate_diff`, etc.) move to `utils/` with identical signatures so existing tests pass.

3. **composite OrchestrationContext**: The existing `OrchestrationContext` pydantic model is decomposed into focused models in `models/` but reassembled as the same composite type for CLI compatibility.

4. **compound-loop embedding**: The `compound_loop/` package provides:
   - `ReleaseLoopBridge`: manages `.release-loop/progress.md` lifecycle, feature branch management, worktree isolation
   - `PlanValidator`: validates `plan/v1` schema compliance before execution
   - Integration points for each phase (Design → USER gate, Plan → planning, Implement → implementing, Review → reviewing, Ship → shipping, Retro → retrospective)

5. **CLI preservation**: The `main` and `ralph_loop` Typer commands retain all existing options and behavior. Internally they delegate to LangChain chains.

### Data models (preserved from existing)

All pydantic models from `orchestration_context.py` are preserved with identical field names, types, and validators:
- `ActionType`, `ReviewItemType`, `ReviewSeverity`, `ReviewDecision` (enums)
- `Task`, `ExecutionLog`, `CodeReviewItem`, `CodeReviewResult`, `RalphWiggumFeedback`, `IterationMetadata`, `OrchestrationContext`

### Provider abstraction

```python
class BaseProvider(ABC):
    """Abstract provider using LangChain models/runnables."""
    def get_model(self) -> BaseChatModel: ...
    def is_available(self) -> bool: ...
    def get_config(self) -> dict: ...

class GeminiProvider(BaseProvider): ...  # CLI or Google AI API
class OpenAIProvider(BaseProvider): ...  # CLI or OpenAI API
class AnthropicProvider(BaseProvider): ...  # CLI or Anthropic API
```

### Agent composition

Each stage agent is a LangChain `Agent` or `Runnable` that accepts a structured input (from `OrchestrationContext`) and produces structured output. Chains compose these agents with the dispatch-degradation ladder (parallel → sequential → fallback).

### Release-loop integration

The `ReleaseLoopBridge` class:
- Creates feature branches via `worktree-isolation` pattern
- Writes `.release-loop/progress.md` per `schemas/progress-schema.md`
- Validates plans against `plan/v1` schema
- Invokes compound-loop skills as subprocesses or direct module calls

## Risks

1. **LangChain dependency conflicts**: LangChain requires specific pydantic 2.x versions. Must verify compatibility with `pydantic>=2.12.5`.
2. **compound-loop path dependency**: Using `compound-loop` as a path dependency requires careful pyproject.toml configuration.
3. **Test preservation**: Existing 259-line test file tests imported symbols (`_extract_json_list`, etc.) — must keep these at top-level or re-export.
4. **Backward CLI compatibility**: Existing users expect `uv run python orchestrator_cli.py "<goal>"` to work.

## Verification

- `uv run ruff check .` — lint passes
- `uv run ruff format --check .` — formatting passes  
- `uv run pytest -v` — all existing + new tests pass
- `uv run python -m ai_orchestration.cli "create a hello world script"` — CLI runs end-to-end
