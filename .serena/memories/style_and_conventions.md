# Style & Conventions
- Indentation: 4 spaces, PEP 8 naming (`snake_case` functions/vars, `PascalCase` classes).
- Type hints: prefer explicit type hints on public functions and models.
- Prompts: keep prompt strings as plain strings in `agent_prompts.py`; avoid side effects there.
- Imports: keep tidy and sorted.
- Linter/Formatter: ruff (`uv run ruff check .`, `uv run ruff format .`)
- Pre-commit hooks: `uv run pre-commit run --all-files`