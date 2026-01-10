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

- Python 3.9+
- `gemini`, `codex`, `claude` CLI가 PATH에 있어야 합니다
- (권장) `uv` 사용 환경

## 설치

```bash
git clone https://github.com/teslamint/ai-orchestration.git
cd ai-orchestration
uv sync
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
├── workspace/                 # 프로젝트별 샌드박스
│   ├── my_project/            # --project-name my_project
│   └── build_a_rest_api/      # 자동 생성된 프로젝트명
├── execution_logs/            # run_command 실행 로그(JSON)
├── orchestrator_debug_logs/   # 디버그 로그 출력
└── tests/
```

## 테스트

```bash
uv run pytest
```

## 문제 해결

- **명령어를 찾을 수 없습니다**: `gemini`, `codex`, `claude` CLI가 PATH에 있는지 확인하세요.
- **출력이 비어 있음**: `--debug` 옵션으로 실행해 단계별 출력 로그를 확인하세요.
- **코드 리뷰 파싱 실패**: Codex 출력이 JSON 형식인지 확인하세요.

## 라이선스

MIT License. 자세한 내용은 `LICENSE`를 참고하세요.
