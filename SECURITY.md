# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

보안 취약점을 발견하셨다면, 공개 이슈 대신 비공개로 보고해 주세요.

### 보고 방법

1. GitHub의 [Private vulnerability reporting](https://github.com/teslamint/ai-orchestration/security/advisories/new) 기능 사용
2. 또는 이메일로 직접 연락

### 보고 내용

- 취약점 설명
- 재현 단계
- 영향 범위
- 가능하다면 수정 제안

### 응답 시간

- 확인: 48시간 이내
- 초기 평가: 7일 이내
- 수정 계획: 심각도에 따라 결정

## Security Considerations

이 도구는 외부 LLM CLI (`gemini`, `codex`, `claude`)를 실행합니다:

- 생성된 코드를 실행하기 전에 검토하세요
- `--auto-run` 옵션 사용 시 주의가 필요합니다
- API 키나 민감한 정보를 goal에 포함하지 마세요
