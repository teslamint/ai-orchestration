# Repository Guidelines

## Project Structure & Module Organization
- Root scripts: `orchestrator_cli.py` (Typer CLI), `orchestration_context.py` (Pydantic models), and `agent_prompts.py` (LLM prompt templates).
- `workspace/<project_name>/` holds generated projects in isolated directories.
- `pyproject.toml` defines runtime dependencies and Python version (3.9+).

## Build, Test, and Development Commands
- `uv run python orchestrator_cli.py "<goal>"` - 기본 실행
- `uv run python orchestrator_cli.py "<goal>" --project-name <name>` - 프로젝트명 지정
- `uv run python orchestrator_cli.py "<goal>" --auto-select --auto-run --auto-approve --auto-fix` - 완전 자동화
- `uv run python orchestrator_cli.py "<goal>" --skip-review` - 코드 리뷰 생략
- `uv run pytest -v` - 테스트 실행
- `uv sync` - 의존성 설치

## CLI Options Reference
| Option | Description | Default |
|--------|-------------|---------|
| `--workspace` | 작업 디렉터리 | `./workspace` |
| `--project-name` | 프로젝트 이름 (goal에서 자동 생성) | auto |
| `--auto-select` | 접근 방식 자동 선택 | False |
| `--auto-run` | 명령 자동 실행 | False |
| `--auto-approve` | 실행 확인 자동 승인 | False |
| `--auto-fix` | 리뷰 항목 자동 수정 | False |
| `--skip-review` | 코드 리뷰 단계 생략 | False |
| `--max-fix-iterations` | 리뷰-수정 반복 횟수 | 1 |
| `--debug` | 디버그 로그 출력 | False |

## Coding Style & Naming Conventions
- Use 4-space indentation, PEP 8 naming (`snake_case` for functions/vars, `PascalCase` for classes).
- Prefer explicit type hints on public functions and models.
- Keep prompts in `agent_prompts.py` as plain strings; avoid side effects in that module.
- No formatter/linter is configured; keep diffs tidy and imports sorted.

## Testing Guidelines
- Tests under `tests/` with `test_*.py` naming
- pytest configured to exclude: `workspace`, `logs`, `.venv`
- Helper function tests: `_generate_project_name`, `_generate_diff`, etc.
- CLI tests: Use `typer.testing.CliRunner` for option validation
- Run: `uv run pytest -v`

## Project Work Patterns

### Self-Improvement Pattern
오케스트레이션 툴로 자체 버그 수정:
```bash
uv run python orchestrator_cli.py "Fix the <issue> in <file>" --project-name fix_<issue> --auto-select
```

### Full Workflow Testing
```bash
# 리뷰 포함 전체 테스트
uv run python orchestrator_cli.py "<goal>" --project-name test --auto-select --auto-fix --auto-run --auto-approve

# 리뷰 제외 빠른 테스트
uv run python orchestrator_cli.py "<goal>" --project-name test --skip-review --auto-select
```

## Known Issues & Solutions

### File Path Duplication
- **Issue**: LLM generates `workspace/` prefix in file paths
- **Solution**: `agent_prompts.py` rule #11 enforces project-relative paths

### Non-ASCII Project Names
- **Issue**: Korean/special characters in goal cause path issues
- **Solution**: `_generate_project_name()` creates ASCII-only slugs

## Commit & Pull Request Guidelines
- Use conventional commits: `feat:`, `fix:`, `docs:`, `test:`
- Use short, imperative commit subjects (e.g., "Add command validation")
- Include `Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>` for AI-assisted commits
- PRs should describe the user-facing impact, list commands run, and link related issues

## Configuration & Runtime Notes
- The CLI expects external tools on PATH: `gemini`, `codex`, and `claude`.
- Default workspace is `./workspace`; each project gets isolated subdirectory.
- Generated files stay inside `workspace/<project_name>/` unless explicitly requested.
