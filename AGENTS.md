# Repository Guidelines

## Project Structure & Module Organization
- Root scripts: `orchestrator_cli.py` (Typer CLI), `orchestration_context.py` (Pydantic models), and `agent_prompts.py` (LLM prompt templates).
- `workspace/` holds example/generated projects (e.g., `workspace/lotto/lotto.py`, `workspace/rock-paper-scissors/`). Treat it as a sandbox; avoid hardcoding paths outside this directory.
- `pyproject.toml` defines runtime dependencies and Python version.

## Build, Test, and Development Commands
- `uv run python orchestrator_cli.py "<goal>" --workspace ./workspace` runs the orchestration flow end-to-end.
- `uv run python orchestration_context.py` executes a local demo of the context models.
- `uv sync` installs dependencies in editable mode.
- `uv run pytest` runs the test suite.

## Coding Style & Naming Conventions
- Use 4-space indentation, PEP 8 naming (`snake_case` for functions/vars, `PascalCase` for classes).
- Prefer explicit type hints on public functions and models.
- Keep prompts in `agent_prompts.py` as plain strings; avoid side effects in that module.
- No formatter/linter is configured; keep diffs tidy and imports sorted.

## Testing Guidelines
- Tests are located under `tests/` with files named `test_*.py`.
- Favor small unit tests around parsing and command execution helpers.
- Run tests with: `uv run pytest`

## Commit & Pull Request Guidelines
- This repo has no Git history yet, so there are no established commit conventions.
- Use short, imperative commit subjects (e.g., "Add command validation") and include context in the body when behavior changes.
- PRs should describe the user-facing impact, list commands run, and link related issues if applicable.

## Configuration & Runtime Notes
- The CLI expects external tools on PATH: `gemini`, `codex`, and `claude`.
- Default workspace is `./workspace`; keep generated files inside that directory unless explicitly requested.
