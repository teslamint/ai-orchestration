# Contributing

AI Orchestration 프로젝트에 기여해 주셔서 감사합니다!

## 개발 환경 설정

```bash
git clone https://github.com/teslamint/ai-orchestration.git
cd ai-orchestration
uv sync --dev
```

## 코드 스타일

- Python 3.9+ 호환 코드 작성
- 타입 힌트 사용 권장
- 함수/클래스에 docstring 작성

## 테스트

변경 사항을 제출하기 전에 테스트를 실행해주세요:

```bash
uv run pytest -v
```

## Pull Request 가이드

1. 이슈를 먼저 생성하거나 기존 이슈를 확인
2. `master` 브랜치에서 새 브랜치 생성
3. 변경 사항 커밋 (커밋 메시지는 [Conventional Commits](https://www.conventionalcommits.org/) 형식 권장)
4. PR 생성 시 관련 이슈 링크

### 커밋 메시지 예시

```
feat: 새 기능 추가
fix: 버그 수정
docs: 문서 업데이트
test: 테스트 추가/수정
chore: 기타 변경사항
```

## 이슈 리포트

버그를 발견하거나 기능을 제안하고 싶다면 [이슈 템플릿](https://github.com/teslamint/ai-orchestration/issues/new/choose)을 사용해주세요.
