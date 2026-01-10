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
- Code is formatted and linted with ruff (see Linting section below).

## Linting & Formatting
- **Linter/Formatter**: ruff
- **Config**: `pyproject.toml` `[tool.ruff]` section
- **Rules**: `E` (errors), `F` (pyflakes), `I` (isort)
- **Ignored**: `E501` (line too long), `E722` (bare except), `E402` in tests

### Commands
```bash
# Check for issues
uv run ruff check .

# Auto-fix issues
uv run ruff check . --fix

# Format code
uv run ruff format .

# Check formatting without changes
uv run ruff format --check .
```

### Pre-commit Hooks
```bash
# Install hooks (one-time)
uv run pre-commit install

# Run on all files
uv run pre-commit run --all-files
```

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

## CI/CD & GitHub Configuration

### GitHub Actions
- Workflow: `.github/workflows/orchestrator-tests.yml`
- Triggers: `push`, `pull_request`
- Uses `astral-sh/setup-uv@v5` for uv installation
- Python version from `.python-version` file

### GitHub Templates
- Issue templates: `.github/ISSUE_TEMPLATE/`
  - `bug_report.yml`: 버그 신고
  - `feature_request.yml`: 기능 제안
  - `config.yml`: 템플릿 설정
- PR template: `.github/pull_request_template.md`

## Bug Fix Patterns

### Planner Example Contamination (341db36)
- **Issue**: Codex planner copied example file names (`requirements.txt`, `main.py`) literally
- **Root Cause**: Prompt example used concrete file names
- **Fix**: Changed to placeholders (`<path/to/target_file>`) + warning text

### Approach Selection Parsing (257a279)
- **Issue**: Template placeholders and duplicates in approach list
- **Fix**: Filter invalid entries before display

### ANSI Code in Tests (b363c3e)
- **Issue**: Rich formatting breaks string assertions in CI
- **Fix**: Strip ANSI codes with `re.sub(r'\x1b\[[0-9;]*m', '', output)`

## Development Workflow

### Debug & Test Orchestrator
```bash
# 디버그 로그와 함께 실행
uv run python orchestrator_cli.py "<goal>" --debug --debug-log ./logs/

# 빠른 테스트 (리뷰 생략, 자동 선택)
uv run python orchestrator_cli.py "<goal>" --skip-review --auto-select
```

### CI Test Locally
```bash
uv run pytest -v
```

### Cleanup Artifacts
```bash
rm -rf *_debug_logs/ workspace/
```
