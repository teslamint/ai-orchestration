# AI Orchestration

[![Orchestration Tests](https://github.com/teslamint/ai-orchestration/actions/workflows/orchestrator-tests.yml/badge.svg)](https://github.com/teslamint/ai-orchestration/actions/workflows/orchestrator-tests.yml)
[![GitHub release](https://img.shields.io/github/v/release/teslamint/ai-orchestration?include_prereleases)](https://github.com/teslamint/ai-orchestration/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

Gemini → Codex → Claude 6단계 워크플로우로, 사용자의 목표를 브레인스토밍부터 코드 리뷰/수정까지 자동화하는 로컬 CLI 오케스트레이터입니다.

## 주요 특징

- **6-Stage Orchestration**: 브레인스토밍 → 리뷰/정리 → 계획 → 구현 → 코드 리뷰 → 수정
- **코드 리뷰 자동화**: Codex가 버그, 보안, 성능 이슈를 검토하고 Claude가 수정
- **유연한 수정 옵션**: 항목별 선택, 자동 수정, 반복 리뷰-수정 지원
- **Configurable LLM Provider**: 각 단계별로 다른 LLM 도구 지정 가능
- **API 직접 호출 지원**: CLI 없이 OpenAI/Anthropic/Google AI API 직접 사용
- **Ralph Wiggum 피드백 루프**: 자동 반복 리뷰/수정 사이클 (자체 참조 컨텍스트 포함)
- **안전한 실행**: `run_command` 단계는 기본적으로 실행 전 확인
- **디버그 모드**: 단계별 출력 스트림 및 전체 로그 저장

## 6단계 워크플로우

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Stage 1: Gemini        브레인스토밍 - 여러 접근 방식 제안              │
│     ↓                                                                    │
│  Stage 2: Codex         브레인스토밍 리뷰/정리 - 제안 개선 및 추천       │
│     ↓                                                                    │
│  [사용자 선택]          접근 방식 선택 또는 직접 입력                    │
│     ↓                                                                    │
│  Stage 3: Codex         계획 수립 - JSON Task 리스트 생성                │
│     ↓                                                                    │
│  Stage 4: Claude        구현 - 파일 생성/수정 및 명령 실행               │
│     ↓                                                                    │
│  Stage 5: Codex         코드 리뷰 - 버그/보안/성능 검토                  │
│     ↓                                                                    │
│  [사용자 선택]          수정할 항목 선택 (또는 자동 수정)                │
│     ↓                                                                    │
│  Stage 6: Claude        수정 - 리뷰 피드백 반영                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## 요구 사항

- `agy`, `codex`, `claude` CLI가 PATH에 있어야 합니다 (CLI 도구 사용 시; 과거 `gemini`는 `agy`로
  대체되었습니다)
- (권장) `uv` 사용 환경
- (선택) API 직접 호출 시 환경변수 설정:
  - `GOOGLE_AI_API_KEY`: Google AI API 키 (gemini_api)
  - `OPENAI_API_KEY`: OpenAI API 키 (openai_api)
  - `ANTHROPIC_API_KEY`: Anthropic API 키 (anthropic_api)

## 설치

```bash
git clone https://github.com/teslamint/ai-orchestration.git
cd ai-orchestration
uv sync

# API 도구 사용 시 추가 의존성 설치
uv sync --extra api
```

## 사용 예시

### 기본 사용법

```bash
uv run python orchestrator_cli.py "Create a Python CLI tool that converts CSV to JSON"
```

### 코드 리뷰 건너뛰기

```bash
uv run python orchestrator_cli.py "Build a simple REST API" --skip-review
```

### 자동 수정 모드

```bash
uv run python orchestrator_cli.py "Create a web scraper" --auto-fix
```

### 반복 리뷰-수정 (최대 3회)

```bash
uv run python orchestrator_cli.py "Build a database migration tool" --max-fix-iterations 3
```

### 완전 자동화 모드

```bash
uv run python orchestrator_cli.py "Create unit tests for my project" \
  --auto-select --auto-run --auto-approve --auto-fix
```

### LLM 도구 커스터마이징

```bash
# 특정 단계에 다른 LLM 도구 사용
uv run python orchestrator_cli.py "Build a REST API" \
  --brainstormer gemini_api --executor anthropic_api

# 설정 파일로 모든 단계 지정
uv run python orchestrator_cli.py "Build a REST API" --tool-config ./llm_config.json
```

### API 직접 호출 (CLI 없이)

CLI 도구 대신 API를 직접 호출할 수 있습니다:
- `gemini_api`: Google AI API (Gemini)
- `openai_api`: OpenAI API (GPT-4o)
- `anthropic_api`: Anthropic API (Claude Sonnet)

```bash
# 환경 변수 설정
export GOOGLE_AI_API_KEY="your-api-key"
export OPENAI_API_KEY="your-api-key"
export ANTHROPIC_API_KEY="your-api-key"

# API 도구 사용
uv run python orchestrator_cli.py "Create a web scraper" \
  --brainstormer gemini_api --executor anthropic_api
```

### Ralph Wiggum 피드백 루프

자동 반복 리뷰/수정 사이클을 활성화합니다:

```bash
uv run python orchestrator_cli.py "Build a calculator" \
  --enable-ralph-wiggum \
  --ralph-wiggum-threshold 0.9 \
  --ralph-wiggum-max-iterations 5 \
  --completion-promise "DONE"
```

### 프로젝트 이름 지정

```bash
# 명시적 프로젝트 이름 지정 -> workspace/my-api/ 에 생성
uv run python orchestrator_cli.py "Build a REST API" --project-name my-api

# 자동 생성 -> workspace/build_a_rest_api/ 에 생성
uv run python orchestrator_cli.py "Build a REST API"
```

### 디버그 모드

```bash
uv run python orchestrator_cli.py "Refactor the authentication module" \
  --debug --debug-log ./logs
```

## CLI 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--workspace` | 작업 파일이 생성될 폴더 경로 | `./workspace` |
| `--debug` | 단계별 출력 스트림과 진단 로그 출력 | `False` |
| `--debug-log` | 디버그 로그 저장 경로 | `./orchestrator_debug_logs` |
| `--auto-run` | `run_command` 태스크 자동 실행 | `False` |
| `--auto-approve` | `run_command` 확인 프롬프트 자동 승인 | `False` |
| `--skip-review` | 코드 리뷰 단계(Stage 5-6) 건너뛰기 | `False` |
| `--max-fix-iterations` | 최대 리뷰-수정 반복 횟수 | `1` |
| `--auto-fix` | 리뷰 항목 자동 수정 (확인 없이) | `False` |
| `--auto-select` | 접근 방식 자동 선택 (기본값 또는 추천) | `False` |
| `--project-name` | 프로젝트 이름 (생략 시 goal에서 자동 생성) | 자동 생성 |
| `--brainstormer` | Stage 1 브레인스토밍 도구 (agy/codex/claude/gemini_api/openai_api/anthropic_api) | `agy` |
| `--reviewer` | Stage 2 브레인스토밍 리뷰 도구 | `codex` |
| `--planner` | Stage 3 계획 수립 도구 | `codex` |
| `--executor` | Stage 4 코드 실행 도구 | `claude` |
| `--code-reviewer` | Stage 5 코드 리뷰 도구 | `codex` |
| `--fixer` | Stage 6 코드 수정 도구 | `claude` |
| `--tool-config` | LLM 도구 설정 파일 경로 (JSON) | 없음 |
| `--enable-ralph-wiggum` | Ralph Wiggum 피드백 루프 활성화 | `False` |
| `--ralph-wiggum-threshold` | Ralph Wiggum 승인 임계값 (0.0-1.0) | `0.8` |
| `--ralph-wiggum-max-iterations` | Ralph Wiggum 최대 반복 횟수 | `3` |
| `--completion-promise` | 완료 시 출력할 promise 텍스트 | 없음 |
| `--ralph-wiggum-state-file/--no-ralph-wiggum-state-file` | 자체 참조용 상태 파일 사용 여부 | `True` |

## 실행 흐름 예시

```
$ uv run python orchestrator_cli.py "Create a fibonacci calculator"

╭────────────────────────── 🚀 Orchestrator Started ───────────────────────────╮
│ Goal: Create a fibonacci calculator                                          │
│ Project: create_a_fibonacci_calculator                                        │
│ Workspace: /path/to/workspace/create_a_fibonacci_calculator                   │
╰──────────────────────────────────────────────────────────────────────────────╯

Stage 1: Gemini Brainstorming
┌──────────────────────────────────────────────────────────────────┐
│ - Approach 1: Recursive implementation                           │
│ - Approach 2: Iterative with memoization                         │
│ - Approach 3: Matrix exponentiation                              │
└──────────────────────────────────────────────────────────────────┘

Stage 2: Codex Brainstorming Review
┌──────────────────────────────────────────────────────────────────┐
│ ## Refined Approaches                                            │
│ ### Approach 1: Iterative (Recommended)                          │
│ ...                                                              │
└──────────────────────────────────────────────────────────────────┘

Please select an approach:
  1: ### Approach 1: Iterative (Recommended)
  2: ### Approach 2: Recursive with memoization
  3: Custom (enter your own)
Enter the number of your choice [1]: 1

Stage 3: Codex Planning
┌──────────────────────────────────────────────────────────────────┐
│ [{"step_id": 1, "file_path": "fibonacci.py", ...}]               │
└──────────────────────────────────────────────────────────────────┘

Stage 4: Claude Implementation
Saved: workspace/create_a_fibonacci_calculator/fibonacci.py

Stage 5: Codex Code Review
┌──────────────────────────────────────────────────────────────────┐
│ Overall: Good implementation with minor improvements needed      │
│ Files Reviewed: 1                                                │
│ Issues Found: 2                                                  │
└──────────────────────────────────────────────────────────────────┘
  [MEDIUM] improvement: fibonacci.py - Add input validation...
  [LOW] documentation: fibonacci.py - Add docstring...

                    Code Review Items
┌───┬──────────┬───────────────┬──────────────┬─────────────────┐
│ # │ Severity │ Type          │ File         │ Description     │
├───┼──────────┼───────────────┼──────────────┼─────────────────┤
│ 1 │ MEDIUM   │ improvement   │ fibonacci.py │ Add input val...│
│ 2 │ LOW      │ documentation │ fibonacci.py │ Add docstring...│
└───┴──────────┴───────────────┴──────────────┴─────────────────┘

Options:
  a - Apply all fixes
  n - Skip all fixes
  1,2,3 - Select specific items
  c - Critical and High only
Enter your choice [a]: a

Stage 6: Claude Fixes
Fixed: workspace/create_a_fibonacci_calculator/fibonacci.py

┌──────────────────────────────────────────────────────────────────┐
│ All Done!                                                        │
└──────────────────────────────────────────────────────────────────┘
```

## 디렉터리 구조

```
.
├── orchestrator_cli.py        # CLI 엔트리포인트
├── orchestration_context.py   # Pydantic 모델 정의
├── agent_prompts.py           # LLM 프롬프트 템플릿
├── llm_tools.py               # LLM 도구 추상화 및 설정
├── api_tools.py               # API 기반 LLM 도구 구현
├── .devcontainer/             # Devcontainer 설정 (보안 샌드박스)
├── workspace/                 # 프로젝트별 샌드박스
│   ├── my_project/            # --project-name my_project
│   └── build_a_rest_api/      # 자동 생성된 프로젝트명
├── execution_logs/            # run_command 실행 로그(JSON)
├── orchestrator_debug_logs/   # 디버그 로그 출력
└── tests/
```

## LLM 도구 설정 파일 예시

`llm_config.json` 파일을 생성하여 각 단계별 도구를 설정할 수 있습니다:

```json
{
  "brainstormer": "gemini_api",
  "reviewer": "codex",
  "planner": "codex",
  "executor": "anthropic_api",
  "code_reviewer": "openai_api",
  "fixer": "claude"
}
```

지원 도구 목록:
- CLI 도구: `agy`, `codex`, `claude`
- API 도구: `gemini_api`, `openai_api`, `anthropic_api`

## Devcontainer (보안 샌드박스)

격리된 환경에서 네트워크 제한과 함께 실행할 수 있습니다.

```bash
# VS Code: "Dev Containers: Reopen in Container" 사용
# 또는 devcontainer CLI:
devcontainer up --workspace-folder .
```

**주요 기능:**
- Python 3.13 + uv + Claude CLI 사전 설치
- 네트워크 방화벽 (GitHub, PyPI, API 엔드포인트만 허용)
- API 키는 `remoteEnv`를 통해 호스트에서 전달

## 테스트

```bash
uv run pytest
```

## 문제 해결

- **명령어를 찾을 수 없습니다**: `gemini`, `codex`, `claude` CLI가 PATH에 있는지 확인하세요.
- **출력이 비어 있음**: `--debug` 옵션으로 실행해 단계별 출력 로그를 확인하세요.
- **코드 리뷰 파싱 실패**: Codex 출력이 JSON 형식인지 확인하세요.
- **API 호출 실패**: 환경변수가 올바르게 설정되었는지 확인하고, `uv sync --extra api`로 의존성을 설치하세요.
- **Ralph Wiggum 루프 무한 반복**: `--ralph-wiggum-max-iterations`로 최대 반복 횟수를 제한하거나 `--completion-promise`로 완료 조건을 설정하세요.

## 기여

기여를 환영합니다! 자세한 내용은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.

## 라이선스

MIT License. 자세한 내용은 [LICENSE](LICENSE)를 참고하세요.
