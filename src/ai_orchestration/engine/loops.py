"""Threshold and retry loops: executor self-healing, main review-fix loop,
and the Ralph Wiggum feedback loop.

Per decision 1, these are plain stdlib control flow, not a graph runtime.
Each loop is ported from its inline block in the legacy `main()`, factored
into a pure function taking injectable callables so it is testable without
a real provider or terminal.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ai_orchestration.models.context import (
    ActionType,
    CodeReviewItem,
    ExecutionLog,
    OrchestrationContext,
    Task,
)
from ai_orchestration.utils.diff import generate_diff
from ai_orchestration.utils.extract import extract_code_content

_RALPH_STATE_RELATIVE_PATH = Path(".claude") / "ralph-loop.local.md"


@dataclass
class ExecutorResult:
    """Outcome of one executor self-healing attempt sequence."""

    success: bool
    task: Task


def run_executor_self_healing(
    context: OrchestrationContext,
    task: Task,
    *,
    complete: Callable[[str], str],
    max_retries: int = 3,
) -> ExecutorResult:
    """Run the executor with syntax-error self-healing (behavior inventory row).

    Ported from `run_claude_executor`'s self-healing loop: on a Python
    `SyntaxError`, retries with an error-correction prompt up to
    `max_retries` additional attempts before giving up. Non-Python files
    skip the syntax check entirely.
    """
    target_path = context.resolve_workspace_file(task.file_path)
    existing_code = ""
    if target_path.exists():
        try:
            existing_code = target_path.read_text(encoding="utf-8")
        except Exception:
            pass

    prompt = task.instruction
    for attempt in range(max_retries + 1):
        raw_output = complete(prompt)
        code_content = extract_code_content(raw_output)

        if str(task.file_path).endswith(".py") and code_content:
            try:
                ast.parse(code_content)
            except SyntaxError as exc:
                if attempt < max_retries:
                    prompt = (
                        f"The code has a SyntaxError: {exc.msg} at line "
                        f"{exc.lineno}.\nYour code:\n```python\n{code_content}\n"
                        "```\nPlease FIX it and return ONLY the corrected code."
                    )
                    continue
                context.execution_logs.append(
                    ExecutionLog(
                        step_id=task.step_id,
                        success=False,
                        message="최대 재시도 초과 또는 오류",
                    )
                )
                return ExecutorResult(success=False, task=task)

        if task.action_type in (ActionType.CREATE_FILE, ActionType.EDIT_FILE):
            full_path = context.resolve_workspace_file(task.file_path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(code_content, encoding="utf-8")
            diff = generate_diff(existing_code, code_content, str(task.file_path))
            context.generated_diffs[str(task.file_path)] = diff
            context.execution_logs.append(
                ExecutionLog(
                    step_id=task.step_id,
                    success=True,
                    message=f"파일 작성 완료: {task.file_path}",
                )
            )
            return ExecutorResult(success=True, task=task)

        return ExecutorResult(success=True, task=task)

    context.execution_logs.append(
        ExecutionLog(
            step_id=task.step_id, success=False, message="최대 재시도 초과 또는 오류"
        )
    )
    return ExecutorResult(success=False, task=task)


_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


def run_main_review_fix_loop(
    context: OrchestrationContext,
    *,
    run_review: Callable[[OrchestrationContext], None],
    run_fix: Callable[[OrchestrationContext, CodeReviewItem], None],
    max_fix_iterations: int = 1,
    auto_fix: bool = False,
    select_items: Optional[
        Callable[[list[CodeReviewItem]], list[CodeReviewItem]]
    ] = None,
) -> None:
    """Run the main Stage 5->6 review/fix loop (S4, default cap 1).

    Iterates on `requires_fixes` plus user selection. Terminates when the
    review reports no fixes required, no items are selected, or
    `max_fix_iterations` is reached.
    """
    fix_iteration = 0
    while fix_iteration < max_fix_iterations:
        fix_iteration += 1
        context.fix_iteration_count = fix_iteration
        run_review(context)

        if not context.code_review_result:
            return
        if not (
            context.code_review_result.requires_fixes
            and context.code_review_result.items
        ):
            return

        if auto_fix:
            items_to_fix = context.code_review_result.items
        elif select_items is not None:
            items_to_fix = select_items(context.code_review_result.items)
        else:
            items_to_fix = []

        if not items_to_fix:
            return

        sorted_items = sorted(
            items_to_fix, key=lambda item: _SEVERITY_ORDER.index(item.severity.value)
        )
        for item in sorted_items:
            run_fix(context, item)

        if fix_iteration >= max_fix_iterations:
            return


def _write_ralph_state_file(context: OrchestrationContext) -> None:
    """Write the Ralph Wiggum self-reference state file, ported verbatim."""
    state_dir = context.workspace_path / ".claude"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "ralph-loop.local.md"
    content = f"""---
active: true
iteration: {context.ralph_wiggum_iteration.review_attempt}
max_iterations: {context.ralph_wiggum_iteration.max_attempts}
completion_promise: "{context.ralph_wiggum_completion_promise or "null"}"
started_at: "{datetime.now().isoformat()}"
---

{context.ralph_wiggum_original_prompt or context.user_goal}
"""
    state_file.write_text(content, encoding="utf-8")
    context.ralph_wiggum_state_file = state_file


def _cleanup_ralph_state_file(context: OrchestrationContext) -> None:
    if context.ralph_wiggum_state_file and context.ralph_wiggum_state_file.exists():
        context.ralph_wiggum_state_file.unlink()


def run_ralph_wiggum_loop(
    context: OrchestrationContext,
    *,
    run_review: Callable[[OrchestrationContext], None],
    write_state_file: bool = True,
    run_fix: Optional[Callable[[OrchestrationContext, CodeReviewItem], None]] = None,
    run_code_review: Optional[Callable[[OrchestrationContext], None]] = None,
) -> None:
    """Run the Ralph Wiggum feedback loop (opt-in, default threshold 0.8, max 3).

    Accepts when `decision == ACCEPTED` or `confidence_score >= threshold`.
    On a non-terminal iteration with suggestions present, reruns
    `run_code_review` and applies the top three fix items
    (`items[:3]`, matching the legacy `iteration_history` carry-forward).
    Writes and cleans up the self-reference state file when
    `write_state_file` is True.
    """
    if write_state_file:
        _write_ralph_state_file(context)

    try:
        while True:
            run_review(context)
            if not context.ralph_wiggum_feedback:
                return

            feedback = context.ralph_wiggum_feedback
            if context.ralph_wiggum_completion_promise:
                last_output = " ".join(feedback.comments)
                if context.check_promise_completion(last_output):
                    return

            if context.is_ralph_wiggum_accepted():
                return

            if not context.can_ralph_wiggum_retry():
                return

            context.prepare_ralph_wiggum_retry()
            if write_state_file:
                _write_ralph_state_file(context)

            has_suggestions = bool(getattr(feedback, "suggestions", None))
            if (
                has_suggestions
                and run_fix is not None
                and run_code_review is not None
                and context.code_review_result
                and context.code_review_result.requires_fixes
                and context.code_review_result.items
            ):
                run_code_review(context)
                if (
                    context.code_review_result
                    and context.code_review_result.requires_fixes
                    and context.code_review_result.items
                ):
                    for item in context.code_review_result.items[:3]:
                        run_fix(context, item)
    finally:
        if write_state_file:
            _cleanup_ralph_state_file(context)
