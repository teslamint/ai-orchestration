# Suggested Commands
- Install deps (uv): `uv sync`
- Install editable: `python -m pip install -e .`
- Run CLI (uv): `uv run python orchestrator_cli.py "<prompt>"`
- Run CLI (alt): `python orchestrator_cli.py "<goal>" --workspace ./workspace`
- Run context demo: `python orchestration_context.py`
- Run tests: `uv run pytest -v`
- Lint check: `uv run ruff check .`
- Format code: `uv run ruff format .`
- Pre-commit: `uv run pre-commit run --all-files`

# Notes
- External tools expected on PATH: `gemini`, `codex`, `claude`.
- Default workspace is `./workspace`; keep generated files inside it unless requested otherwise.