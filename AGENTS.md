# Repository Guidelines

## Project Structure & Module Organization
- `src/ai_orchestration/`: package source.
  - `cli.py` (Typer CLI, `ai-orchestration` console script), `config.py` (stage/provider
    config, workspace anchor), `errors.py` (typed exceptions).
  - `models/` (Pydantic context models), `prompts/` (stage prompt templates),
    `utils/` (extraction, diff, slug helpers).
  - `providers/` (HTTP proxy, CLI subprocess, legacy API adapters, routing/fallback).
  - `engine/` (durable state, approval gates, review/fix and Ralph Wiggum loops,
    stage registry, `CommandExecutor`).
- `workspace/<project_name>/` holds generated projects in isolated directories.
- `pyproject.toml` defines runtime dependencies and Python version (3.10+).

## Build, Test, and Development Commands
- `ai-orchestration "<goal>"` - 기본 실행 (설치된 콘솔 스크립트)
- `uv run ai-orchestration "<goal>"` - `uv` 환경에서 실행
- `uv run python -m ai_orchestration.cli "<goal>"` - 모듈 직접 실행 (설치 없이)
- `ai-orchestration "<goal>" --project-name <name>` - 프로젝트명 지정
- `ai-orchestration "<goal>" --auto-select --auto-run --auto-approve --auto-fix` - 완전 자동화
- `ai-orchestration "<goal>" --skip-review` - 코드 리뷰 생략
- `ai-orchestration "<goal>" --brainstormer claude --planner claude --reviewer claude --executor claude --code-reviewer claude --fixer claude` - 모든 stage를 CLI 서브프로세스로 실행
- `ai-orchestration "<goal>" --tool-config ./my_tools.json` - 설정 파일 사용 (모델/fallback_model/fallback_binary 포함)
- `ai-orchestration "<goal>" --resume` - 중단된 실행 재개 (기본값은 항상 새로 시작)
- `uv run pytest -v` - 테스트 실행
- `uv sync` - 의존성 설치

## CLI Options Reference
| Option | Description | Default |
|--------|-------------|---------|
| `--workspace` | 작업 디렉터리 | `ORCHESTRATOR_WORKSPACE` 또는 `./workspace` |
| `--project-name` | 프로젝트 이름 (goal에서 자동 생성) | auto |
| `--auto-select` | 접근 방식 자동 선택 | False |
| `--auto-run` | 명령 자동 실행 | False |
| `--auto-approve` | 실행 확인 자동 승인 | False |
| `--auto-fix` | 리뷰 항목 자동 수정 | False |
| `--skip-review` | 코드 리뷰 단계 생략 | False |
| `--max-fix-iterations` | 리뷰-수정 반복 횟수 | 1 |
| `--debug` | 디버그 로그 출력 | False |
| `--brainstormer` | Stage 1 도구 (CLI 바이너리 또는 프록시 모델 id) | `gemini-3.1-pro-low` |
| `--reviewer` | Stage 2 도구 | `gpt-5.5` |
| `--planner` | Stage 3 도구 | `gpt-5.5` |
| `--executor` | Stage 4 도구 | `claude-sonnet-5` |
| `--code-reviewer` | Stage 5 도구 | `gpt-5.5` |
| `--fixer` | Stage 6 도구 | `claude-sonnet-5` |
| `--tool-config` | LLM 도구 설정 JSON 파일 | None |
| `--enable-ralph-wiggum` | Ralph Wiggum 피드백 루프 활성화 | False |
| `--ralph-wiggum-threshold` | 승인 임계값 (0.0-1.0) | 0.8 |
| `--ralph-wiggum-max-iterations` | 최대 반복 횟수 | 3 |
| `--completion-promise` | 완료 시 출력할 promise 텍스트 | None |
| `--ralph-wiggum-state-file/--no-ralph-wiggum-state-file` | 상태 파일 사용 여부 | True |
| `--resume` | 중단된 실행 재개 | False |

### Stage Routing Slots
각 단계 값은 정확한 CLI 바이너리 이름(`agy`/`codex`/`claude`)이면 그 서브프로세스로, 그 외는 모두
프록시 모델 id로 취급됩니다. 도구 설정 파일에서 단계별로 `fallback_model`(같은 엔드포인트의 대체
모델, 429/5xx 대응)과 `fallback_binary`(엔드포인트 자체 장애 시 서브프로세스 대체)를 각각 지정할
수 있습니다. 우선순위: 단계 플래그 > 설정 파일 > 내장 기본값.

### Available Tool Types
| Type | Description |
|------|-------------|
| `agy` | Antigravity CLI (requires `agy` in PATH) |
| `codex` | OpenAI Codex CLI (requires `codex` in PATH) |
| `claude` | Claude CLI (requires `claude` in PATH) |
| `gemini_api` | Google AI API (requires `GOOGLE_AI_API_KEY` env; legacy direct-vendor adapter) |
| `openai_api` | OpenAI API (requires `OPENAI_API_KEY` env; legacy direct-vendor adapter) |
| `anthropic_api` | Anthropic API (requires `ANTHROPIC_API_KEY` env; legacy direct-vendor adapter) |
| (그 외 문자열) | CLIProxyAPI가 노출하는 프록시 모델 id (예: `gpt-5.5`, `opus-5`) |

## Coding Style & Naming Conventions
- Use 4-space indentation, PEP 8 naming (`snake_case` for functions/vars, `PascalCase` for classes).
- Prefer explicit type hints on public functions and models.
- Keep prompts in `src/ai_orchestration/prompts/stages.py` as plain strings; avoid side effects in that module.
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
- Helper function tests: `generate_project_name`, `generate_diff`, etc. (`src/ai_orchestration/utils/`)
- CLI tests: Use `typer.testing.CliRunner` for option validation (`tests/test_cli.py`)
- Installed-entrypoint smoke tests: `tests/test_smoke.py`
- Run: `uv run pytest -v`

## Project Work Patterns

### Self-Improvement Pattern
오케스트레이션 툴로 자체 버그 수정:
```bash
ai-orchestration "Fix the <issue> in <file>" --project-name fix_<issue> --auto-select
```

### Full Workflow Testing
```bash
# 리뷰 포함 전체 테스트
ai-orchestration "<goal>" --project-name test --auto-select --auto-fix --auto-run --auto-approve

# 리뷰 제외 빠른 테스트
ai-orchestration "<goal>" --project-name test --skip-review --auto-select
```

### Ralph Wiggum Feedback Loop
자동 반복 리뷰/수정 사이클을 활성화합니다:
```bash
# 기본 피드백 루프
ai-orchestration "<goal>" --enable-ralph-wiggum

# 높은 임계값과 더 많은 반복
ai-orchestration "<goal>" \
  --enable-ralph-wiggum \
  --ralph-wiggum-threshold 0.9 \
  --ralph-wiggum-max-iterations 5 \
  --completion-promise "DONE"
```

## Known Issues & Solutions

### File Path Duplication
- **Issue**: LLM generates `workspace/` prefix in file paths
- **Solution**: `src/ai_orchestration/prompts/stages.py`'s planner template rule #11 enforces project-relative paths

### Non-ASCII Project Names
- **Issue**: Korean/special characters in goal cause path issues
- **Solution**: `generate_project_name()` in `src/ai_orchestration/utils/slug.py` creates ASCII-only slugs

## Commit & Pull Request Guidelines
- Use conventional commits: `feat:`, `fix:`, `docs:`, `test:`
- Use short, imperative commit subjects (e.g., "Add command validation")
- Include `Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>` for AI-assisted commits
- PRs should describe the user-facing impact, list commands run, and link related issues

## Configuration & Runtime Notes
- CLI-based providers use binaries on PATH as `fallback_binary`: `agy`, `codex`, `claude`.
- Proxy-routed stages need `CLIPROXYAPI_KEY` (or a tool-config `provider.api_key`).
- Legacy direct-vendor API tools require environment variables: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_AI_API_KEY`.
- Workspace anchor precedence: `--workspace` flag > `ORCHESTRATOR_WORKSPACE` env var > current directory's `workspace/`. Absolute paths are used as-is.
- Generated files stay inside `<workspace-anchor>/<project_name>/` unless explicitly requested.
- Re-running a project name always starts fresh unless `--resume` is passed.

## Devcontainer (Security Sandbox)
Use Devcontainer for isolated execution with network restrictions.

### Setup
```bash
# VS Code: "Dev Containers: Reopen in Container"
# Or use devcontainer CLI:
devcontainer up --workspace-folder .
```

### Features
- Python 3.13 + uv + Claude CLI pre-installed
- Network firewall (only allowed domains: GitHub, PyPI, API endpoints)
- zsh with powerline10k theme
- API keys passed via `remoteEnv` in devcontainer.json

### Environment Variables (Host)
Set these on your host machine before starting the container:
```bash
export CLIPROXYAPI_KEY=...
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
export GOOGLE_AI_API_KEY=AI...
```

### API Dependencies
```bash
# Install legacy direct-vendor API client libraries
uv sync --extra api
```

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
ai-orchestration "<goal>" --debug --debug-log ./logs/

# 빠른 테스트 (리뷰 생략, 자동 선택)
ai-orchestration "<goal>" --skip-review --auto-select
```

### CI Test Locally
```bash
uv run pytest -v
```

### Cleanup Artifacts
```bash
rm -rf *_debug_logs/ workspace/
```

## Claude Code Skills

### Recommended Skills
| Skill | Use Case |
|-------|----------|
| `/commit` | Git 커밋 생성 (conventional commit 형식) |
| `/test` | 테스트 실행 및 결과 분석 |
| `/review` | 코드 리뷰 수행 |
| `/format` | ruff format으로 코드 포맷팅 |
| `/fix-imports` | import 정리 및 수정 |
| `/docs` | 문서 업데이트 |

### Useful Skill Combinations
```bash
# 코드 변경 후 전체 검증
/format → /test → /review → /commit

# 새 기능 추가 시
/implement → /test → /format → /commit

# 문서 업데이트
/docs → /commit
```

## Development Checklist

### Before Commit
- [ ] `uv run ruff check .` - 린트 검사 통과
- [ ] `uv run ruff format --check .` - 포맷팅 검사 통과
- [ ] `uv run pytest -v` - 테스트 통과
- [ ] CLI 동작 확인: `uv run ai-orchestration "<prompt>"`

### Code Changes
- [ ] PEP 8 naming 준수 (`snake_case` / `PascalCase`)
- [ ] 공개 함수에 type hints 추가
- [ ] `workspace/` 경로 하드코딩 금지
- [ ] `tests/` 에 관련 테스트 추가/수정

### PR Submission
- [ ] conventional commit 형식 사용
- [ ] Co-Authored-By 헤더 포함
- [ ] 변경 사항 설명 작성
