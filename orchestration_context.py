from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActionType(str, Enum):
    """
    Task가 수행해야 할 작업의 종류를 정의하는 Enum입니다.
    """

    CREATE_FILE = "create_file"
    EDIT_FILE = "edit_file"
    RUN_COMMAND = "run_command"
    OTHER = "other"


class ReviewItemType(str, Enum):
    """
    코드 리뷰 항목의 유형을 정의하는 Enum입니다.
    """

    BUG = "bug"
    IMPROVEMENT = "improvement"
    STYLE = "style"
    SECURITY = "security"
    PERFORMANCE = "performance"
    DOCUMENTATION = "documentation"


class ReviewSeverity(str, Enum):
    """
    코드 리뷰 항목의 심각도를 정의하는 Enum입니다.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ReviewDecision(str, Enum):
    """Ralph Wiggum 피드백 루프의 리뷰 결정 유형입니다."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"
    PENDING = "pending"


class Task(BaseModel):
    """
    Stage 2 (Planner)에서 생성되는 개별 실행 계획(Task)을 정의합니다.
    """

    step_id: int = Field(..., description="실행 계획의 순서 또는 고유 식별자")
    file_path: Path = Field(
        ..., description="생성하거나 수정할 파일의 전체 또는 상대 경로"
    )
    action_type: ActionType = Field(
        ..., description="수행할 작업의 종류 (e.g., 'create_file')"
    )
    instruction: str = Field(
        ..., description="Stage 3 (Executor)에게 전달될 구체적인 지시사항"
    )


class ExecutionLog(BaseModel):
    """
    Stage 3 (Executor)의 각 Task 실행 결과를 기록합니다.
    """

    step_id: int = Field(..., description="실행된 Task의 step_id")
    success: bool = Field(..., description="해당 Task의 실행 성공 여부")
    message: Optional[str] = Field(
        default=None, description="실행 결과에 대한 요약 메시지 (성공, 실패 원인 등)"
    )
    output: Optional[str] = Field(
        default=None, description="명령어 실행 시 발생한 표준 출력 또는 에러 내용"
    )


class CodeReviewItem(BaseModel):
    """
    Stage 5 (Code Reviewer)에서 생성되는 개별 리뷰 항목입니다.
    """

    item_id: int = Field(..., description="리뷰 항목의 고유 식별자")
    file_path: Path = Field(..., description="리뷰 대상 파일 경로")
    line_start: Optional[int] = Field(default=None, description="문제 시작 라인")
    line_end: Optional[int] = Field(default=None, description="문제 끝 라인")
    review_type: ReviewItemType = Field(..., description="리뷰 항목 유형")
    severity: ReviewSeverity = Field(..., description="심각도")
    description: str = Field(..., description="문제 설명")
    suggestion: str = Field(..., description="수정 제안")
    code_snippet: Optional[str] = Field(default=None, description="관련 코드 조각")


class CodeReviewResult(BaseModel):
    """
    Stage 5 (Code Reviewer)의 전체 리뷰 결과입니다.
    """

    reviewed_at: str = Field(..., description="리뷰 수행 시간 (ISO format)")
    total_files_reviewed: int = Field(..., description="리뷰된 파일 수")
    items: List[CodeReviewItem] = Field(
        default_factory=list, description="리뷰 항목 목록"
    )
    overall_assessment: str = Field(..., description="전체 평가 요약")
    requires_fixes: bool = Field(..., description="수정이 필요한지 여부")


class RalphWiggumFeedback(BaseModel):
    """Ralph Wiggum 피드백 루프에서 리뷰어 피드백 모델입니다."""

    reviewer_id: str = Field(default="ralph_wiggum", description="리뷰어 식별자")
    decision: ReviewDecision = Field(
        default=ReviewDecision.PENDING, description="리뷰 결정"
    )
    comments: List[str] = Field(default_factory=list, description="리뷰 코멘트")
    suggestions: List[str] = Field(default_factory=list, description="개선 제안")
    confidence_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="신뢰도 점수 (0.0-1.0)"
    )
    reviewed_at: Optional[str] = Field(
        default=None, description="리뷰 시간 (ISO format)"
    )


class IterationMetadata(BaseModel):
    """Ralph Wiggum 피드백 루프의 반복 메타데이터입니다."""

    review_attempt: int = Field(
        default=1, ge=1, description="현재 리뷰 시도 횟수 (1-indexed)"
    )
    max_attempts: int = Field(default=3, ge=1, description="최대 시도 횟수")
    review_notes: List[str] = Field(default_factory=list, description="리뷰 노트")
    iteration_history: List[Dict[str, Any]] = Field(
        default_factory=list, description="반복 히스토리"
    )
    file_snapshots: List[Dict[str, str]] = Field(
        default_factory=list, description="각 반복 시작 시 파일 상태 스냅샷"
    )
    previous_outputs: List[str] = Field(
        default_factory=list, description="이전 반복들의 출력 요약"
    )

    def increment_attempt(self) -> bool:
        """시도 횟수를 증가시킵니다. 최대 도달 시 False 반환."""
        if self.review_attempt >= self.max_attempts:
            return False
        self.review_attempt += 1
        return True

    def add_note(self, note: str) -> None:
        """리뷰 노트를 추가합니다."""
        self.review_notes.append(note)

    def add_history_entry(self, entry: Dict[str, Any]) -> None:
        """히스토리 항목을 추가합니다."""
        self.iteration_history.append(entry)


class OrchestrationContext(BaseModel):
    """
    AI Orchestration Tool의 전체 워크플로우를 관통하며 데이터를 관리하는 메인 컨텍스트 객체입니다.
    """

    # === Global Context ===
    project_name: str = Field(..., description="AI가 생성할 프로젝트의 이름")
    user_goal: str = Field(
        ..., description="사용자가 처음에 입력한 최종 목표 또는 요구사항"
    )
    workspace_path: Path = Field(
        ..., description="파일 생성, 수정 등 작업이 이루어질 로컬 시스템의 기본 경로"
    )

    # === Stage 1 Data (Gemini - Brainstormer) ===
    brainstorming_ideas: Union[str, List[str]] = Field(
        default_factory=list,
        description="Gemini가 제안한 아이디어나 기술적 접근 방식 목록",
    )

    # === Stage 2 Data (Codex - Brainstorming Reviewer) ===
    refined_brainstorming: Optional[str] = Field(
        default=None, description="Codex가 정리/리뷰한 브레인스토밍 결과"
    )
    brainstorming_review_notes: Optional[str] = Field(
        default=None, description="브레인스토밍 리뷰 시 Codex의 추가 노트"
    )
    selected_approach: Optional[str] = Field(
        default=None, description="사용자가 선택했거나 최종적으로 결정된 구현 접근 방식"
    )

    # === Stage 3 Data (Codex - Planner) ===
    implementation_plan: List[Task] = Field(
        default_factory=list, description="Codex가 생성한 구체적인 실행 계획 목록"
    )

    # === Stage 4 Data (Claude - Executor) ===
    execution_logs: List[ExecutionLog] = Field(
        default_factory=list, description="Claude가 각 Task를 실행하고 남긴 결과 기록"
    )
    generated_diffs: Dict[str, str] = Field(
        default_factory=dict,
        description="Stage 4에서 생성된 파일별 diff (파일경로 -> diff 문자열)",
    )

    # === Stage 5 Data (Codex - Code Reviewer) ===
    code_review_result: Optional[CodeReviewResult] = Field(
        default=None, description="Codex 코드 리뷰 결과"
    )

    # === Stage 6 Data (Claude - Fixer) ===
    fix_execution_logs: List[ExecutionLog] = Field(
        default_factory=list, description="Stage 6에서 수정 적용 후 실행 로그"
    )
    fix_iteration_count: int = Field(default=0, description="수정 반복 횟수")

    # === Ralph Wiggum Feedback Loop Data ===
    ralph_wiggum_enabled: bool = Field(
        default=False, description="Ralph Wiggum 피드백 루프 활성화 여부"
    )
    ralph_wiggum_feedback: Optional[RalphWiggumFeedback] = Field(
        default=None, description="Ralph Wiggum 리뷰 피드백"
    )
    ralph_wiggum_iteration: IterationMetadata = Field(
        default_factory=IterationMetadata, description="피드백 루프 반복 메타데이터"
    )
    ralph_wiggum_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, description="승인 임계값 (0.0-1.0)"
    )
    ralph_wiggum_original_prompt: Optional[str] = Field(
        default=None, description="Ralph Wiggum 루프의 불변 원본 프롬프트"
    )
    ralph_wiggum_completion_promise: Optional[str] = Field(
        default=None, description="완료 시 출력해야 할 promise 텍스트"
    )
    ralph_wiggum_state_file: Optional[Path] = Field(
        default=None,
        description="Ralph Wiggum 상태 파일 경로 (.claude/ralph-loop.local.md)",
    )

    @field_validator("workspace_path")
    def resolve_workspace_path(cls, v: Path) -> Path:
        """workspace_path를 절대 경로로 변환하고, 해당 디렉토리가 없으면 생성합니다."""
        resolved_path = v.resolve()
        resolved_path.mkdir(parents=True, exist_ok=True)
        return resolved_path

    # === Ralph Wiggum Feedback Loop Methods ===
    def submit_ralph_wiggum_feedback(self, feedback: RalphWiggumFeedback) -> None:
        """Ralph Wiggum 피드백을 제출합니다."""
        self.ralph_wiggum_feedback = feedback
        self.ralph_wiggum_iteration.add_history_entry(
            {
                "action": "feedback_submitted",
                "decision": feedback.decision.value
                if hasattr(feedback.decision, "value")
                else feedback.decision,
                "attempt": self.ralph_wiggum_iteration.review_attempt,
            }
        )

    def is_ralph_wiggum_accepted(self) -> bool:
        """피드백이 승인되었는지 확인합니다."""
        if not self.ralph_wiggum_feedback:
            return False
        feedback = self.ralph_wiggum_feedback
        decision = feedback.decision
        if hasattr(decision, "value"):
            is_accepted = decision == ReviewDecision.ACCEPTED
        else:
            is_accepted = decision == "accepted"
        return is_accepted or feedback.confidence_score >= self.ralph_wiggum_threshold

    def can_ralph_wiggum_retry(self) -> bool:
        """추가 시도가 가능한지 확인합니다."""
        return (
            self.ralph_wiggum_iteration.review_attempt
            < self.ralph_wiggum_iteration.max_attempts
        )

    def prepare_ralph_wiggum_retry(self) -> bool:
        """다음 시도를 준비합니다. 성공 시 True 반환."""
        if not self.can_ralph_wiggum_retry():
            return False
        self.ralph_wiggum_iteration.increment_attempt()
        self.ralph_wiggum_feedback = None
        return True

    def check_promise_completion(self, output: str) -> bool:
        """출력에서 <promise>TAG</promise> 패턴을 확인합니다."""
        if not self.ralph_wiggum_completion_promise:
            return False
        import re

        pattern = r"<promise>(.*?)</promise>"
        match = re.search(pattern, output, re.DOTALL)
        if match:
            promise_text = match.group(1).strip()
            return promise_text == self.ralph_wiggum_completion_promise
        return False

    def save_iteration_snapshot(self, files: Dict[str, str]) -> None:
        """현재 파일 상태를 스냅샷으로 저장합니다."""
        self.ralph_wiggum_iteration.file_snapshots.append(files)

    def add_previous_output(self, output_summary: str) -> None:
        """이전 반복 출력을 저장합니다."""
        self.ralph_wiggum_iteration.previous_outputs.append(output_summary)

    def get_self_reference_context(self) -> str:
        """자체 참조 컨텍스트를 생성합니다."""
        ctx_parts = []
        if self.ralph_wiggum_iteration.previous_outputs:
            ctx_parts.append("## Previous Iteration Outputs")
            for i, output in enumerate(self.ralph_wiggum_iteration.previous_outputs, 1):
                ctx_parts.append(f"### Iteration {i}\n{output}")
        return "\n\n".join(ctx_parts)

    model_config = ConfigDict(
        use_enum_values=True,
        arbitrary_types_allowed=True,
    )


# ======== 사용 예시 ========
if __name__ == "__main__":
    # 1. 초기 컨텍스트 생성 (사용자 입력 기반)
    initial_context = OrchestrationContext(
        project_name="MyAwesomeApp",
        user_goal="파이썬으로 웹 스크래핑을 해서 CSV 파일로 저장하는 CLI 도구 만들어줘.",
        workspace_path="./workspace/my_awesome_app",
    )
    print("--- 1. Initial Context ---")
    print(initial_context.model_dump_json(indent=2, exclude_none=True))

    # 2. Stage 1 (Gemini) 실행 후 데이터 추가
    initial_context.brainstorming_ideas = [
        "Requests와 BeautifulSoup 라이브러리 사용",
        "Scrapy 프레임워크 사용",
        "Playwright를 이용한 동적 페이지 스크래핑",
    ]
    initial_context.selected_approach = "Requests와 BeautifulSoup 라이브러리 사용"
    print("\n--- 2. After Stage 1 (Brainstorming) ---")
    print(initial_context.model_dump_json(indent=2, exclude_none=True))

    # 3. Stage 2 (ChatGPT) 실행 후 데이터 추가
    plan = [
        Task(
            step_id=1,
            file_path=Path("main.py"),
            action_type=ActionType.CREATE_FILE,
            instruction="requests와 beautifulsoup4, pandas를 import하고, 특정 URL에서 데이터를 스크래핑하여 DataFrame으로 만드는 기본 코드를 작성해줘.",
        ),
        Task(
            step_id=2,
            file_path=Path("main.py"),
            action_type=ActionType.EDIT_FILE,
            instruction="스크래핑한 DataFrame을 'output.csv' 파일로 저장하는 기능을 추가해줘.",
        ),
        Task(
            step_id=3,
            file_path=Path("requirements.txt"),
            action_type=ActionType.CREATE_FILE,
            instruction="프로젝트에 필요한 라이브러리 (requests, beautifulsoup4, pandas)를 requirements.txt 파일에 기록해줘.",
        ),
        Task(
            step_id=4,
            file_path=Path("."),
            action_type=ActionType.RUN_COMMAND,
            instruction="pip install -r requirements.txt",
        ),
    ]
    initial_context.implementation_plan = plan
    print("\n--- 3. After Stage 2 (Planning) ---")
    print(initial_context.model_dump_json(indent=2, exclude_none=True))

    # 4. Stage 3 (Claude) 실행 후 데이터 추가
    logs = [
        ExecutionLog(step_id=1, success=True, message="main.py 파일 생성 완료"),
        ExecutionLog(step_id=2, success=True, message="CSV 저장 기능 추가 완료"),
        ExecutionLog(
            step_id=3, success=False, message="파일 생성 실패: 권한 문제 발생"
        ),
        ExecutionLog(
            step_id=4,
            success=True,
            message="명령 실행 완료",
            output="Successfully installed requests beautifulsoup4 pandas",
        ),
    ]
    initial_context.execution_logs = logs
    print("\n--- 4. After Stage 3 (Execution) ---")
    print(initial_context.model_dump_json(indent=2, exclude_none=True))

    # 최종 결과 확인
    print("\n--- Final Orchestration Context Object ---")
    print(initial_context)
