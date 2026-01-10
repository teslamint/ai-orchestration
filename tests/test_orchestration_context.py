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
    OrchestrationContext,
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
