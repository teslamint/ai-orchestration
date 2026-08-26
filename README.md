# AI Orchestration

[![Orchestration Tests](https://github.com/teslamint/ai-orchestration/actions/workflows/orchestrator-tests.yml/badge.svg)](https://github.com/teslamint/ai-orchestration/actions/workflows/orchestrator-tests.yml)
[![GitHub release](https://img.shields.io/github/v/release/teslamint/ai-orchestration?include_prereleases)](https://github.com/teslamint/ai-orchestration/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

6단계 워크플로우로 사용자의 목표를 브레인스토밍부터 코드 리뷰/수정까지 자동화하는 로컬 CLI 오케스트레이터입니다.
모델은 프록시(CLIProxyAPI) 우선으로 라우팅되며, 엔드포인트가 닿지 않을 때는 `agy`/`codex`/`claude` CLI
서브프로세스로 자동 전환합니다.

## 주요 특징

- **6-Stage Orchestration**: 브레인스토밍 → 리뷰/정리 → 계획 → 구현 → 코드 리뷰 → 수정
- **프록시 우선, CLI 자동 폴백**: 각 단계는 프록시 모델 id를 기본으로 사용하고, 엔드포인트가 끊기면
  `agy`/`codex`/`claude` 서브프로세스로 자동 다운그레이드합니다
- **단계별 이중 폴백 슬롯**: 같은 엔드포인트의 대체 모델(`fallback_model`)과 서브프로세스
  대체(`fallback_binary`)를 분리해서 설정할 수 있습니다
- **코드 리뷰 자동화**: 코드 리뷰어 단계가 버그, 보안, 성능 이슈를 검토하고 픽서 단계가 수정
- **유연한 수정 옵션**: 항목별 선택, 자동 수정, 반복 리뷰-수정 지원
- **레거시 API 직접 호출 호환**: 기존 `gemini_api`/`openai_api`/`anthropic_api` 값도 그대로 동작
- **Ralph Wiggum 피드백 루프**: 자동 반복 리뷰/수정 사이클 (자체 참조 컨텍스트 포함)
- **안전한 실행**: `run_command` 단계는 기본적으로 실행 전 확인, 비대화형 환경에서는 승인 플래그
  없이 절대 진행하지 않습니다 (fail-closed)
- **디버그 모드**: 단계별 출력 스트림 및 전체 로그 저장

## 6단계 워크플로우

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Stage 1: Brainstormer   브레인스토밍 - 여러 접근 방식 제안              │
│     ↓                                                                    │
│  Stage 2: Reviewer       브레인스토밍 리뷰/정리 - 제안 개선 및 추천       │
│     ↓                                                                    │
│  [사용자 선택]           접근 방식 선택 또는 직접 입력                    │
│     ↓                                                                    │
│  Stage 3: Planner        계획 수립 - JSON Task 리스트 생성                │
│     ↓                                                                    │
│  Stage 4: Executor       구현 - 파일 생성/수정 및 명령 실행               │
│     ↓                                                                    │
│  Stage 5: Code Reviewer  코드 리뷰 - 버그/보안/성능 검토                  │
│     ↓                                                                    │
│  [사용자 선택]           수정할 항목 선택 (또는 자동 수정)                │
│     ↓                                                                    │
│  Stage 6: Fixer          수정 - 리뷰 피드백 반영                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## 요구 사항

- Python 3.10+
- `agy`, `codex`, `claude` CLI가 PATH에 있으면 각 단계의 기본 `fallback_binary`로 자동 사용됩니다
  (과거 `gemini`는 `agy`로 대체되었습니다). 없어도 프록시 모델만으로 실행할 수 있습니다.
- (권장) `uv` 사용 환경
- 프록시 경유 시 `CLIPROXYAPI_KEY` 환경변수(또는 도구 설정 파일의 `provider.api_key`)
- (선택) 레거시 API 직접 호출 시 환경변수 설정:
  - `GOOGLE_AI_API_KEY`: Google AI API 키 (`gemini_api`)
  - `OPENAI_API_KEY`: OpenAI API 키 (`openai_api`)
  - `ANTHROPIC_API_KEY`: Anthropic API 키 (`anthropic_api`)
- 이 릴리스는 POSIX 호스트를 지원합니다. durable run lock에는 `fcntl`을, 시간 초과된 CLI 서브프로세스 정리에는 process group `killpg`를 사용하므로 Windows는 지원하지 않습니다.

## 설치

```bash
git clone https://github.com/teslamint/ai-orchestration.git
cd ai-orchestration
uv sync

# 레거시 API 직접 호출 도구 사용 시 추가 의존성 설치
uv sync --extra api
```

## 사용 예시

설치된 콘솔 스크립트 `ai-orchestration`을 바로 사용합니다. (`uv run ai-orchestration ...`도 동일하게
동작합니다.)

### 기본 사용법

```bash
ai-orchestration "Create a Python CLI tool that converts CSV to JSON"
```

### 코드 리뷰 건너뛰기

```bash
ai-orchestration "Build a simple REST API" --skip-review
```

### 자동 수정 모드

```bash
ai-orchestration "Create a web scraper" --auto-fix
```

### 반복 리뷰-수정 (최대 3회)

```bash
ai-orchestration "Build a database migration tool" --max-fix-iterations 3
```

### 완전 자동화 모드

```bash
ai-orchestration "Create unit tests for my project" \
  --auto-select --auto-run --auto-approve --auto-fix
```

### 단계별 모델/도구 지정

각 단계 플래그는 정확한 CLI 바이너리 이름(`agy`/`codex`/`claude`)이거나 프록시가 노출하는 임의의
모델 id를 받습니다. 바이너리 이름과 일치하면 그 서브프로세스로, 그 외는 프록시 모델로 라우팅됩니다.

```bash
# 특정 단계에 다른 프록시 모델/CLI 지정
ai-orchestration "Build a REST API" \
  --planner opus-5 --executor claude

# 설정 파일로 모든 단계 지정 (fallback_model/fallback_binary 포함)
ai-orchestration "Build a REST API" --tool-config ./llm_config.json
```

### 레거시 API 직접 호출 (CLI 없이)

CLI/프록시 대신 벤더 API를 직접 호출하는 값도 그대로 동작합니다:
- `gemini_api`: Google AI API (Gemini)
- `openai_api`: OpenAI API (GPT-4o)
- `anthropic_api`: Anthropic API (Claude Sonnet)

```bash
# 환경 변수 설정
export GOOGLE_AI_API_KEY="your-api-key"
export OPENAI_API_KEY="your-api-key"
export ANTHROPIC_API_KEY="your-api-key"

# 레거시 API 도구 사용
ai-orchestration "Create a web scraper" \
  --brainstormer gemini_api --executor anthropic_api
```

### Ralph Wiggum 피드백 루프

자동 반복 리뷰/수정 사이클을 활성화합니다:

```bash
ai-orchestration "Build a calculator" \
  --enable-ralph-wiggum \
  --ralph-wiggum-threshold 0.9 \
  --ralph-wiggum-max-iterations 5 \
  --completion-promise "DONE"
```

### 프로젝트 이름 지정

```bash
# 명시적 프로젝트 이름 지정 -> workspace/my-api/ 에 생성
ai-orchestration "Build a REST API" --project-name my-api

# 자동 생성 -> workspace/build_a_rest_api/ 에 생성
ai-orchestration "Build a REST API"
```

### 워크스페이스 위치 지정

작업 디렉터리 anchor는 우선순위대로 결정됩니다: `--workspace` 플래그 > `ORCHESTRATOR_WORKSPACE`
환경변수 > 현재 디렉터리의 `workspace/`. 절대 경로는 그대로 사용됩니다.

```bash
# 플래그로 명시
ai-orchestration "Build a REST API" --workspace /tmp/my-workspace

# 환경변수로 명시 (플래그 미지정 시 사용)
export ORCHESTRATOR_WORKSPACE=/tmp/my-workspace
ai-orchestration "Build a REST API"
```

### 재개 (Resume)

기본적으로 같은 프로젝트명으로 다시 실행하면 **항상 새로 시작**합니다. 중단된 실행을 이어가려면
`--resume`을 명시해야 합니다.

```bash
# 새로 시작 (기본값) - 이전 상태를 무시하고 처음부터 실행
ai-orchestration "Build a REST API" --project-name my-api

# 재개 - 완료된 단계는 건너뛰고 중단 지점부터 이어서 실행
ai-orchestration "Build a REST API" --project-name my-api --resume
```

### 디버그 모드

```bash
ai-orchestration "Refactor the authentication module" \
  --debug --debug-log ./logs
```

## CLI 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--workspace` | 작업 파일이 생성될 폴더 경로 | `ORCHESTRATOR_WORKSPACE` 또는 `./workspace` |
| `--debug` | 단계별 출력 스트림과 진단 로그 출력 | `False` |
| `--debug-log` | 디버그 로그 저장 경로 | `./orchestrator_debug_logs` |
| `--auto-run` | `run_command` 태스크 자동 실행 | `False` |
| `--auto-approve` | `run_command` 확인 프롬프트 자동 승인 | `False` |
| `--skip-review` | 코드 리뷰 단계(Stage 5-6) 건너뛰기 | `False` |
| `--max-fix-iterations` | 최대 리뷰-수정 반복 횟수 | `1` |
| `--auto-fix` | 리뷰 항목 자동 수정 (확인 없이) | `False` |
| `--auto-select` | 접근 방식 자동 선택 (기본값 또는 추천) | `False` |
| `--project-name` | 프로젝트 이름 (생략 시 goal에서 자동 생성) | 자동 생성 |
| `--brainstormer` | Stage 1 도구 (CLI 바이너리 이름 또는 프록시 모델 id) | `gemini-3.1-pro-low` |
| `--reviewer` | Stage 2 도구 | `gpt-5.5` |
| `--planner` | Stage 3 도구 | `gpt-5.5` |
| `--executor` | Stage 4 도구 | `claude-sonnet-5` |
| `--code-reviewer` | Stage 5 도구 | `gpt-5.5` |
| `--fixer` | Stage 6 도구 | `claude-sonnet-5` |
| `--tool-config` | LLM 도구 설정 파일 경로 (JSON) | 없음 |
| `--enable-ralph-wiggum` | Ralph Wiggum 피드백 루프 활성화 | `False` |
| `--ralph-wiggum-threshold` | Ralph Wiggum 승인 임계값 (0.0-1.0) | `0.8` |
| `--ralph-wiggum-max-iterations` | Ralph Wiggum 최대 반복 횟수 | `3` |
| `--completion-promise` | 완료 시 출력할 promise 텍스트 | 없음 |
| `--ralph-wiggum-state-file/--no-ralph-wiggum-state-file` | 자체 참조용 상태 파일 사용 여부 | `True` |
| `--resume` | 이전에 중단된 실행을 이어서 재개 (기본값: 새로 시작) | `False` |

## 단계별 모델 라우팅과 폴백

각 단계 플래그가 받는 값은 정확한 CLI 바이너리 이름(`agy`, `codex`, `claude`)이면 그 서브프로세스를,
그 외의 어떤 값이든 프록시 모델 id로 취급합니다. 정확도를 위해 추측하지 않습니다.

각 단계는 세 개의 독립적인 슬롯을 갖습니다:

| 슬롯 | 용도 | 검증 방식 |
|---|---|---|
| `model` | 1차 목표: 프록시 모델 id 또는 CLI 바이너리 이름 | 모델이면 `/v1/models`, 바이너리면 PATH |
| `fallback_model` | 같은 엔드포인트에서의 2차 시도 (모델 자체 장애, 예: 429) | 항상 `/v1/models` |
| `fallback_binary` | 엔드포인트 자체가 닿지 않을 때의 서브프로세스 대체 | 항상 PATH |

엔드포인트가 완전히 닿지 않으면(연결 거부/타임아웃) `fallback_model`은 시도하지 않고
`fallback_binary`로 바로 전환합니다 — 같은 죽은 전송 경로를 다시 시도하는 것은 의미가 없기
때문입니다. 반대로 모델이 429/5xx를 반환하거나 파싱 불가능한 출력을 낼 때는 `fallback_model`만
시도하고, CLI 서브프로세스로는 절대 내려가지 않습니다 (엔드포인트는 정상이므로).

우선순위(높은 순): 단계 플래그 > 도구 설정 파일의 값 > 내장 기본값.

## 실행 흐름 예시

```
$ ai-orchestration "Create a fibonacci calculator"

╭────────────────────────── 🚀 Orchestrator Started ───────────────────────────╮
│ Goal: Create a fibonacci calculator                                          │
│ Project: create_a_fibonacci_calculator                                        │
│ Workspace: /path/to/workspace/create_a_fibonacci_calculator                   │
╰──────────────────────────────────────────────────────────────────────────────╯

Stage 1: Brainstorming
┌──────────────────────────────────────────────────────────────────┐
│ - Approach 1: Recursive implementation                           │
│ - Approach 2: Iterative with memoization                         │
│ - Approach 3: Matrix exponentiation                              │
└──────────────────────────────────────────────────────────────────┘

Stage 2: Brainstorming Review
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

Stage 3: Planning
┌──────────────────────────────────────────────────────────────────┐
│ [{"step_id": 1, "file_path": "fibonacci.py", ...}]               │
└──────────────────────────────────────────────────────────────────┘

Stage 4: Implementation
Saved: workspace/create_a_fibonacci_calculator/fibonacci.py

Stage 5: Code Review
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

Stage 6: Fixes
Fixed: workspace/create_a_fibonacci_calculator/fibonacci.py

┌──────────────────────────────────────────────────────────────────┐
│ All Done!                                                        │
└──────────────────────────────────────────────────────────────────┘
```

## 디렉터리 구조

```
.
├── src/ai_orchestration/
│   ├── cli.py                 # Typer CLI 엔트리포인트 (ai-orchestration 콘솔 스크립트)
│   ├── config.py              # 설정 계약: 단계 라우팅, 프록시 엔드포인트, 워크스페이스 anchor
│   ├── errors.py              # 타입화된 예외 계층
│   ├── models/                # Pydantic 컨텍스트 모델
│   ├── prompts/                # 단계별 프롬프트 템플릿
│   ├── providers/             # HTTP(프록시)/CLI/레거시 API 프로바이더, 라우팅
│   ├── engine/                # 상태, 게이트, 루프, 스테이지 레지스트리
│   └── utils/                  # 추출, diff, slug 헬퍼
├── .devcontainer/              # Devcontainer 설정 (보안 샌드박스)
├── workspace/                   # 프로젝트별 샌드박스
│   ├── my_project/             # --project-name my_project
│   └── build_a_rest_api/       # 자동 생성된 프로젝트명
├── execution_logs/             # run_command 실행 로그(JSON)
├── orchestrator_debug_logs/    # 디버그 로그 출력
└── tests/
```

## LLM 도구 설정 파일 예시

`llm_config.json` 파일을 생성하여 각 단계별 모델과 폴백 슬롯을 설정할 수 있습니다:

```json
{
  "provider": {
    "base_url": "https://cliproxyapi.tailnet-0a4d.ts.net:8317/v1",
    "api_key": "..."
  },
  "planner": {"model": "opus-5", "fallback_model": "gpt-5.5", "fallback_binary": "codex"},
  "reviewer": "codex",
  "executor": "claude",
  "code_reviewer": "gemini_api",
  "fixer": "anthropic_api"
}
```

바레 문자열(`"reviewer": "codex"`)은 `{"model": "codex"}`의 축약형이며 기존 설정 파일도 그대로
동작합니다.

지원 도구 목록:
- CLI 도구: `agy`, `codex`, `claude`
- 레거시 API 직접 호출: `gemini_api`, `openai_api`, `anthropic_api`
- 그 외 문자열: 프록시가 노출하는 모델 id (`/v1/models`로 시작 시 검증)

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

## 검증

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
uv run ai-orchestration --help
```

## 문제 해결

- **명령어를 찾을 수 없습니다**: `agy`, `codex`, `claude` CLI가 PATH에 있는지 확인하세요. CLI가
  없어도 프록시 모델만으로는 실행할 수 있습니다.
- **알 수 없는 모델/바이너리 오류**: 시작 시 모든 단계의 `model`/`fallback_model`/`fallback_binary`가
  검증됩니다. 오류 메시지에 단계 이름과 문제가 된 값이 그대로 표시됩니다.
- **출력이 비어 있음**: `--debug` 옵션으로 실행해 단계별 출력 로그를 확인하세요.
- **코드 리뷰 파싱 실패**: 코드 리뷰어 출력이 JSON 형식인지 확인하세요.
- **레거시 API 호출 실패**: 환경변수가 올바르게 설정되었는지 확인하고, `uv sync --extra api`로
  의존성을 설치하세요.
- **비대화형 환경에서 멈춘 것처럼 보임**: 승인 게이트는 비대화형(non-TTY) 환경에서 자동으로
  실패 처리되며 필요한 플래그(`--auto-run`, `--auto-approve`, `--auto-fix`)를 출력에 명시합니다.
  절대 무한 대기하지 않습니다.
- **Ralph Wiggum 루프 무한 반복**: `--ralph-wiggum-max-iterations`로 최대 반복 횟수를 제한하거나
  `--completion-promise`로 완료 조건을 설정하세요.

## 기여

기여를 환영합니다! 자세한 내용은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.

## 라이선스

MIT License. 자세한 내용은 [LICENSE](LICENSE)를 참고하세요.
