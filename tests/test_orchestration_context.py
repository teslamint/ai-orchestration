"""Tests for orchestration_context models."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestration_context import (
    ActionType,
    CodeReviewItem,
    CodeReviewResult,
    ExecutionLog,
    IterationMetadata,
    OrchestrationContext,
    RalphWiggumFeedback,
    ReviewDecision,
    ReviewItemType,
    ReviewSeverity,
    Task,
)


def test_review_item_type_values():
    assert ReviewItemType.BUG.value == "bug"
    assert ReviewItemType.SECURITY.value == "security"
    assert ReviewItemType.PERFORMANCE.value == "performance"


def test_review_severity_values():
    assert ReviewSeverity.CRITICAL.value == "critical"
    assert ReviewSeverity.HIGH.value == "high"
    assert ReviewSeverity.INFO.value == "info"


def test_code_review_item_creation():
    item = CodeReviewItem(
        item_id=1,
        file_path=Path("main.py"),
        line_start=10,
        line_end=15,
        review_type=ReviewItemType.BUG,
        severity=ReviewSeverity.HIGH,
        description="Potential null pointer",
        suggestion="Add null check",
    )
    assert item.item_id == 1
    assert item.severity == ReviewSeverity.HIGH
    assert item.review_type == ReviewItemType.BUG
    assert str(item.file_path) == "main.py"


def test_code_review_item_optional_fields():
    item = CodeReviewItem(
        item_id=2,
        file_path=Path("utils.py"),
        review_type=ReviewItemType.STYLE,
        severity=ReviewSeverity.LOW,
        description="Naming convention issue",
        suggestion="Use snake_case",
    )
    assert item.line_start is None
    assert item.line_end is None
    assert item.code_snippet is None


def test_code_review_result_creation():
    items = [
        CodeReviewItem(
            item_id=1,
            file_path=Path("main.py"),
            review_type=ReviewItemType.BUG,
            severity=ReviewSeverity.CRITICAL,
            description="Critical bug",
            suggestion="Fix it",
        )
    ]
    result = CodeReviewResult(
        reviewed_at="2024-01-01T00:00:00",
        total_files_reviewed=3,
        items=items,
        overall_assessment="Issues found",
        requires_fixes=True,
    )
    assert result.requires_fixes is True
    assert len(result.items) == 1
    assert result.total_files_reviewed == 3


def test_code_review_result_empty_items():
    result = CodeReviewResult(
        reviewed_at="2024-01-01T00:00:00",
        total_files_reviewed=2,
        items=[],
        overall_assessment="All good",
        requires_fixes=False,
    )
    assert result.requires_fixes is False
    assert len(result.items) == 0


def test_orchestration_context_new_fields(tmp_path):
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
    )

    # Default values for new fields
    assert context.refined_brainstorming is None
    assert context.brainstorming_review_notes is None
    assert context.generated_diffs == {}
    assert context.code_review_result is None
    assert context.fix_execution_logs == []
    assert context.fix_iteration_count == 0


def test_orchestration_context_set_new_fields(tmp_path):
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
    )

    # Set Stage 2 data
    context.refined_brainstorming = "Refined approach 1"
    context.brainstorming_review_notes = "Recommended for efficiency"

    # Set Stage 4 diff data
    context.generated_diffs = {"main.py": "+new line\n-old line"}

    # Set Stage 5 data
    context.code_review_result = CodeReviewResult(
        reviewed_at="2024-01-01T00:00:00",
        total_files_reviewed=1,
        items=[],
        overall_assessment="Clean code",
        requires_fixes=False,
    )

    # Set Stage 6 data
    context.fix_execution_logs = [
        ExecutionLog(step_id=1, success=True, message="Fixed")
    ]
    context.fix_iteration_count = 1

    # Verify
    assert context.refined_brainstorming == "Refined approach 1"
    assert "main.py" in context.generated_diffs
    assert context.code_review_result.requires_fixes is False
    assert len(context.fix_execution_logs) == 1
    assert context.fix_iteration_count == 1


def test_task_creation():
    task = Task(
        step_id=1,
        file_path=Path("test.py"),
        action_type=ActionType.CREATE_FILE,
        instruction="Create a test file",
    )
    assert task.step_id == 1
    assert task.action_type == ActionType.CREATE_FILE


def test_execution_log_creation():
    log = ExecutionLog(
        step_id=1,
        success=True,
        message="File created successfully",
        output="Created test.py",
    )
    assert log.success is True
    assert log.message == "File created successfully"


# === Ralph Wiggum Feedback Loop Tests ===


def test_review_decision_values():
    """Test ReviewDecision enum values."""
    assert ReviewDecision.ACCEPTED.value == "accepted"
    assert ReviewDecision.REJECTED.value == "rejected"
    assert ReviewDecision.NEEDS_REVISION.value == "needs_revision"
    assert ReviewDecision.PENDING.value == "pending"


def test_ralph_wiggum_feedback_creation():
    """Test RalphWiggumFeedback model creation."""
    feedback = RalphWiggumFeedback(
        reviewer_id="test_reviewer",
        decision=ReviewDecision.ACCEPTED,
        comments=["Good job", "Clean code"],
        suggestions=["Consider adding tests"],
        confidence_score=0.9,
        reviewed_at="2024-01-01T00:00:00",
    )
    assert feedback.reviewer_id == "test_reviewer"
    assert feedback.decision == ReviewDecision.ACCEPTED
    assert len(feedback.comments) == 2
    assert len(feedback.suggestions) == 1
    assert feedback.confidence_score == 0.9


def test_ralph_wiggum_feedback_defaults():
    """Test RalphWiggumFeedback default values."""
    feedback = RalphWiggumFeedback()
    assert feedback.reviewer_id == "ralph_wiggum"
    assert feedback.decision == ReviewDecision.PENDING
    assert feedback.comments == []
    assert feedback.suggestions == []
    assert feedback.confidence_score == 0.0
    assert feedback.reviewed_at is None


def test_iteration_metadata_creation():
    """Test IterationMetadata model creation."""
    metadata = IterationMetadata(
        review_attempt=2,
        max_attempts=5,
        review_notes=["Note 1"],
    )
    assert metadata.review_attempt == 2
    assert metadata.max_attempts == 5
    assert len(metadata.review_notes) == 1


def test_iteration_metadata_defaults():
    """Test IterationMetadata default values."""
    metadata = IterationMetadata()
    assert metadata.review_attempt == 1
    assert metadata.max_attempts == 3
    assert metadata.review_notes == []
    assert metadata.iteration_history == []
    assert metadata.file_snapshots == []
    assert metadata.previous_outputs == []


def test_iteration_metadata_increment_attempt():
    """Test IterationMetadata.increment_attempt method."""
    metadata = IterationMetadata(review_attempt=1, max_attempts=3)
    assert metadata.increment_attempt() is True
    assert metadata.review_attempt == 2
    assert metadata.increment_attempt() is True
    assert metadata.review_attempt == 3
    assert metadata.increment_attempt() is False
    assert metadata.review_attempt == 3


def test_iteration_metadata_add_note():
    """Test IterationMetadata.add_note method."""
    metadata = IterationMetadata()
    metadata.add_note("First note")
    metadata.add_note("Second note")
    assert len(metadata.review_notes) == 2
    assert metadata.review_notes[0] == "First note"


def test_iteration_metadata_add_history_entry():
    """Test IterationMetadata.add_history_entry method."""
    metadata = IterationMetadata()
    metadata.add_history_entry({"action": "test", "result": "pass"})
    assert len(metadata.iteration_history) == 1
    assert metadata.iteration_history[0]["action"] == "test"


def test_orchestration_context_ralph_wiggum_defaults(tmp_path):
    """Test OrchestrationContext Ralph Wiggum default fields."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
    )
    assert context.ralph_wiggum_enabled is False
    assert context.ralph_wiggum_feedback is None
    assert context.ralph_wiggum_threshold == 0.8
    assert context.ralph_wiggum_original_prompt is None
    assert context.ralph_wiggum_completion_promise is None
    assert context.ralph_wiggum_state_file is None


def test_orchestration_context_ralph_wiggum_enabled(tmp_path):
    """Test OrchestrationContext with Ralph Wiggum enabled."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_threshold=0.9,
        ralph_wiggum_original_prompt="Original prompt",
        ralph_wiggum_completion_promise="DONE",
    )
    assert context.ralph_wiggum_enabled is True
    assert context.ralph_wiggum_threshold == 0.9
    assert context.ralph_wiggum_original_prompt == "Original prompt"
    assert context.ralph_wiggum_completion_promise == "DONE"


def test_submit_ralph_wiggum_feedback(tmp_path):
    """Test OrchestrationContext.submit_ralph_wiggum_feedback method."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
    )
    feedback = RalphWiggumFeedback(
        decision=ReviewDecision.ACCEPTED,
        confidence_score=0.95,
    )
    context.submit_ralph_wiggum_feedback(feedback)

    assert context.ralph_wiggum_feedback is not None
    assert context.ralph_wiggum_feedback.decision == ReviewDecision.ACCEPTED
    assert len(context.ralph_wiggum_iteration.iteration_history) == 1
    assert (
        context.ralph_wiggum_iteration.iteration_history[0]["action"]
        == "feedback_submitted"
    )


def test_is_ralph_wiggum_accepted_by_decision(tmp_path):
    """Test OrchestrationContext.is_ralph_wiggum_accepted by decision."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_threshold=0.8,
    )
    feedback = RalphWiggumFeedback(
        decision=ReviewDecision.ACCEPTED,
        confidence_score=0.5,
    )
    context.ralph_wiggum_feedback = feedback
    assert context.is_ralph_wiggum_accepted() is True


def test_is_ralph_wiggum_accepted_by_threshold(tmp_path):
    """Test OrchestrationContext.is_ralph_wiggum_accepted by threshold."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_threshold=0.8,
    )
    feedback = RalphWiggumFeedback(
        decision=ReviewDecision.NEEDS_REVISION,
        confidence_score=0.85,
    )
    context.ralph_wiggum_feedback = feedback
    assert context.is_ralph_wiggum_accepted() is True


def test_is_ralph_wiggum_accepted_false(tmp_path):
    """Test OrchestrationContext.is_ralph_wiggum_accepted returns False."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_threshold=0.8,
    )
    feedback = RalphWiggumFeedback(
        decision=ReviewDecision.NEEDS_REVISION,
        confidence_score=0.5,
    )
    context.ralph_wiggum_feedback = feedback
    assert context.is_ralph_wiggum_accepted() is False


def test_is_ralph_wiggum_accepted_no_feedback(tmp_path):
    """Test OrchestrationContext.is_ralph_wiggum_accepted with no feedback."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
    )
    assert context.is_ralph_wiggum_accepted() is False


def test_can_ralph_wiggum_retry(tmp_path):
    """Test OrchestrationContext.can_ralph_wiggum_retry method."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_iteration=IterationMetadata(review_attempt=1, max_attempts=3),
    )
    assert context.can_ralph_wiggum_retry() is True

    context.ralph_wiggum_iteration.review_attempt = 3
    assert context.can_ralph_wiggum_retry() is False


def test_prepare_ralph_wiggum_retry(tmp_path):
    """Test OrchestrationContext.prepare_ralph_wiggum_retry method."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_iteration=IterationMetadata(review_attempt=1, max_attempts=3),
    )
    context.ralph_wiggum_feedback = RalphWiggumFeedback(
        decision=ReviewDecision.NEEDS_REVISION
    )

    assert context.prepare_ralph_wiggum_retry() is True
    assert context.ralph_wiggum_iteration.review_attempt == 2
    assert context.ralph_wiggum_feedback is None


def test_prepare_ralph_wiggum_retry_at_max(tmp_path):
    """Test OrchestrationContext.prepare_ralph_wiggum_retry at max attempts."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_iteration=IterationMetadata(review_attempt=3, max_attempts=3),
    )
    assert context.prepare_ralph_wiggum_retry() is False


def test_check_promise_completion(tmp_path):
    """Test OrchestrationContext.check_promise_completion method."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_completion_promise="DONE",
    )
    assert (
        context.check_promise_completion("Task complete <promise>DONE</promise>")
        is True
    )
    assert (
        context.check_promise_completion("Task complete <promise>NOT_DONE</promise>")
        is False
    )
    assert context.check_promise_completion("No promise here") is False


def test_check_promise_completion_no_promise(tmp_path):
    """Test OrchestrationContext.check_promise_completion without promise set."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
    )
    assert context.check_promise_completion("<promise>DONE</promise>") is False


def test_save_iteration_snapshot(tmp_path):
    """Test OrchestrationContext.save_iteration_snapshot method."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
    )
    snapshot = {"main.py": "print('hello')", "utils.py": "def helper(): pass"}
    context.save_iteration_snapshot(snapshot)

    assert len(context.ralph_wiggum_iteration.file_snapshots) == 1
    assert "main.py" in context.ralph_wiggum_iteration.file_snapshots[0]


def test_add_previous_output(tmp_path):
    """Test OrchestrationContext.add_previous_output method."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
    )
    context.add_previous_output("Iteration 1: Score 0.6")
    context.add_previous_output("Iteration 2: Score 0.7")

    assert len(context.ralph_wiggum_iteration.previous_outputs) == 2


def test_get_self_reference_context(tmp_path):
    """Test OrchestrationContext.get_self_reference_context method."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
    )
    context.add_previous_output("Iteration 1: Score 0.6")
    context.add_previous_output("Iteration 2: Score 0.7")

    self_ref = context.get_self_reference_context()
    assert "## Previous Iteration Outputs" in self_ref
    assert "### Iteration 1" in self_ref
    assert "### Iteration 2" in self_ref


def test_get_self_reference_context_empty(tmp_path):
    """Test OrchestrationContext.get_self_reference_context with no outputs."""
    context = OrchestrationContext(
        project_name="TestProject",
        user_goal="Build a test app",
        workspace_path=tmp_path,
        ralph_wiggum_enabled=True,
    )
    assert context.get_self_reference_context() == ""
