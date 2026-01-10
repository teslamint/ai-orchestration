# AI Orchestration

Gemini(브레인스토머) → Codex(플래너) → Claude(실행자) 3단계 흐름으로, 사용자의 목표를 실행 가능한 계획과 코드로 만들어주는 로컬 CLI 오케스트레이터입니다. 각 단계의 결과를 Rich UI로 확인하고, 필요한 경우 사용자가 접근 방식을 선택할 수 있습니다.

## 주요 특징

- 3-Stage Orchestration: 아이디어 제안 → 실행 계획(JSON) → 코드 생성/수정
- 작업 타입 분리: 파일 생성/수정, 명령 실행을 명확히 분리
- 안전한 실행: `run_command` 단계는 기본적으로 실행 전 확인
- 자동 재시도 + 구조화 로그: 명령 실행 시 재시도 및 JSON 로그 기록
- 디버그 모드: 단계별 출력 스트림 및 전체 로그 저장

## 동작 구조

1. **Gemini (Brainstormer)**: 요청을 바탕으로 여러 접근 방식을 제안
2. **Codex (Planner)**: 선택된 접근 방식을 JSON Task 리스트로 변환
3. **Claude (Executor)**: Task에 따라 파일 생성/수정 또는 명령 실행

## 요구 사항

- Python 3.9+
- `gemini`, `codex`, `claude` CLI가 PATH에 있어야 합니다
- (권장) `uv` 사용 환경

## 설치

```bash
# 저장소 클론

git clone <repository-url>
cd ai-orchestration

# 의존성 설치
uv sync
```

> `uv` 사용이 어렵다면 `python -m pip install -e .` 방식으로도 설치할 수 있습니다.

## 빠른 시작

```bash
uv run python orchestrator_cli.py "Create a Python function to calculate fibonacci numbers"
```

실행 중 Gemini가 여러 접근 방식을 제안하면, 숫자로 선택할 수 있습니다.

## CLI 사용법

```bash
uv run python orchestrator_cli.py "<goal>" \
  --workspace ./workspace \
  --debug \
  --debug-log ./orchestrator_debug_logs \
  --auto-run \
  --auto-approve
```

### 옵션 설명

- `--workspace`: 작업 파일이 생성될 폴더 경로 (기본값: `./workspace`)
- `--debug`: 단계별 출력 스트림과 진단 로그를 출력
- `--debug-log`: 디버그 로그 저장 경로(파일 또는 디렉터리)
- `--auto-run`: `run_command` 태스크를 자동 실행
- `--auto-approve`: `run_command` 확인 프롬프트를 자동 승인

## 예시 출력

```
┌─ Stage 1: Gemini ─────────────────────────┐
│ ✓ Code generated successfully             │
└───────────────────────────────────────────┘

┌─ Stage 2: Codex ──────────────────────────┐
│ ✓ Plan created (JSON)                     │
└───────────────────────────────────────────┘

┌─ Stage 3: Claude ─────────────────────────┐
│ ✓ Files written / commands executed       │
└───────────────────────────────────────────┘
```

## 디렉터리 구조

```
.
├── orchestrator_cli.py        # CLI 엔트리포인트
├── orchestration_context.py   # Pydantic 모델 정의
├── agent_prompts.py           # LLM 프롬프트 템플릿
├── workspace/                 # 생성/수정 파일이 저장되는 샌드박스
├── execution_logs/            # run_command 실행 로그(JSON)
├── orchestrator_debug_logs/   # 디버그 로그 출력
└── tests/
```

## 로깅

- `execution_logs/`: 명령 실행 시도(재시도 포함) 및 결과를 JSON으로 기록
- `orchestrator_debug_logs/`: 디버그 모드에서 전체 출력 스트림 로그 저장

## 테스트

```bash
uv run pytest
```

## 문제 해결

- **명령어를 찾을 수 없습니다**: `gemini`, `codex`, `claude` CLI가 PATH에 있는지 확인하세요.
- **출력이 비어 있음**: `--debug` 옵션으로 실행해 단계별 출력 로그를 확인하세요.
- **파일이 다른 위치에 생성됨**: `--workspace` 경로가 기대와 같은지 확인하세요.

## 기여 방법

1. 이슈 또는 개선 요청을 남겨주세요.
2. 기능 제안/버그 수정 PR을 환영합니다.
3. 큰 변경은 먼저 이슈로 논의해주세요.

## 라이선스

MIT License. 자세한 내용은 `LICENSE`를 참고하세요.
