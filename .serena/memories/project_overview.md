# Project Overview
- Purpose: CLI tool that orchestrates multi-stage LLM code generation/review/validation with a Rich UI output.
- Tech stack: Python 3.9+, Typer CLI, Pydantic models, Rich for UI; pytest for tests.
- Entrypoints: `orchestrator_cli.py` (main CLI), `orchestration_context.py` (context demo), `agent_prompts.py` (prompt templates).
- Structure: root scripts above; `tests/` for unit tests; `workspace/` for example/generated projects (sandbox).