# Completion Checklist
- Keep changes within project structure; avoid hardcoding paths outside `workspace/`.
- Update or add tests under `tests/` when behavior changes; run `uv run pytest`.
- Ensure CLI usage still works: `uv run python orchestrator_cli.py "<prompt>"`.
- Keep diffs tidy; follow PEP 8 naming and explicit type hints for public APIs.