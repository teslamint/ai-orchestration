"""Loop tests: executor self-healing, main review-fix loop, Ralph Wiggum loop (U4).

Covers §Testing scenarios: executor stops at its attempt cap; the main
Stage 5->6 loop iterates on requires_fixes plus user selection (cap
--max-fix-iterations, default 1); Ralph Wiggum accepts by decision or
threshold (default 3 max-iterations, 0.8 threshold), the state file is
created and cleaned up, and its top-three fixes carry forward.
"""

from pathlib import Path

from ai_orchestration.models.context import (
    ActionType,
    CodeReviewItem,
    CodeReviewResult,
    OrchestrationContext,
    RalphWiggumFeedback,
    ReviewDecision,
    ReviewItemType,
    ReviewSeverity,
    Task,
)


def _context(tmp_path, **overrides):
    defaults = dict(
        project_name="p",
        user_goal="build a thing",
        workspace_path=tmp_path,
    )
    defaults.update(overrides)
    return OrchestrationContext(**defaults)


# --- Executor self-healing ---------------------------------------------------


def test_executor_self_healing_retries_syntax_error_and_succeeds_after_fix(tmp_path):
    from ai_orchestration.engine.loops import run_executor_self_healing

    task = Task(
        step_id=1,
        file_path=Path("bad.py"),
        action_type=ActionType.CREATE_FILE,
        instruction="write a function",
    )
    outputs = iter(["def f(:\n  pass", "def f():\n    pass"])

    def fake_complete(prompt: str) -> str:
        return next(outputs)

    context = _context(tmp_path)
    result = run_executor_self_healing(
        context, task, complete=fake_complete, max_retries=3
    )
    assert result.success is True
    assert (tmp_path / "bad.py").read_text() == "def f():\n    pass"


def test_executor_self_healing_stops_at_attempt_cap_and_records_failure(tmp_path):
    from ai_orchestration.engine.loops import run_executor_self_healing

    task = Task(
        step_id=1,
        file_path=Path("always_bad.py"),
        action_type=ActionType.CREATE_FILE,
        instruction="write a function",
    )

    def always_broken(prompt: str) -> str:
        return "def f(:\n  still broken"

    context = _context(tmp_path)
    result = run_executor_self_healing(
        context, task, complete=always_broken, max_retries=3
    )
    assert result.success is False
    assert not (tmp_path / "always_bad.py").exists()


def test_executor_self_healing_captures_diff_on_edit(tmp_path):
    from ai_orchestration.engine.loops import run_executor_self_healing

    target = tmp_path / "existing.py"
    target.write_text("x = 1\n")
    task = Task(
        step_id=2,
        file_path=Path("existing.py"),
        action_type=ActionType.EDIT_FILE,
        instruction="change x to 2",
    )
    context = _context(tmp_path)
    result = run_executor_self_healing(
        context, task, complete=lambda p: "x = 2\n", max_retries=1
    )
    assert result.success is True
    assert "existing.py" in context.generated_diffs
    assert "+x = 2" in context.generated_diffs["existing.py"]


def test_executor_self_healing_non_python_file_skips_syntax_check(tmp_path):
    from ai_orchestration.engine.loops import run_executor_self_healing

    task = Task(
        step_id=3,
        file_path=Path("notes.txt"),
        action_type=ActionType.CREATE_FILE,
        instruction="write notes",
    )
    context = _context(tmp_path)
    result = run_executor_self_healing(
        context, task, complete=lambda p: "this is not python (: syntax", max_retries=0
    )
    assert result.success is True


# --- Main Stage 5->6 review-fix loop -----------------------------------------


def test_main_loop_stops_when_no_fixes_required(tmp_path):
    from ai_orchestration.engine.loops import run_main_review_fix_loop

    context = _context(tmp_path)
    review_calls = []

    def run_review(ctx):
        review_calls.append(1)
        ctx.code_review_result = CodeReviewResult(
            reviewed_at="t",
            total_files_reviewed=1,
            items=[],
            overall_assessment="clean",
            requires_fixes=False,
        )

    fix_calls = []
    run_main_review_fix_loop(
        context,
        run_review=run_review,
        run_fix=lambda ctx, item: fix_calls.append(item),
        max_fix_iterations=1,
        auto_fix=True,
    )
    assert len(review_calls) == 1
    assert fix_calls == []


def test_main_loop_applies_selected_items_then_stops_at_iteration_cap(tmp_path):
    from ai_orchestration.engine.loops import run_main_review_fix_loop

    context = _context(tmp_path)
    review_count = {"n": 0}

    def run_review(ctx):
        review_count["n"] += 1
        item = CodeReviewItem(
            item_id=1,
            file_path=Path("a.py"),
            review_type=ReviewItemType.BUG,
            severity=ReviewSeverity.HIGH,
            description="bug",
            suggestion="fix it",
        )
        ctx.code_review_result = CodeReviewResult(
            reviewed_at="t",
            total_files_reviewed=1,
            items=[item],
            overall_assessment="needs work",
            requires_fixes=True,
        )

    fix_calls = []
    run_main_review_fix_loop(
        context,
        run_review=run_review,
        run_fix=lambda ctx, item: fix_calls.append(item.item_id),
        max_fix_iterations=1,  # default cap
        auto_fix=True,
    )
    # default cap is 1: exactly one review pass, one fix pass, no re-review.
    assert review_count["n"] == 1
    assert fix_calls == [1]


def test_main_loop_respects_user_selection_when_not_auto_fix(tmp_path):
    from ai_orchestration.engine.loops import run_main_review_fix_loop

    context = _context(tmp_path)

    def run_review(ctx):
        items = [
            CodeReviewItem(
                item_id=i,
                file_path=Path(f"f{i}.py"),
                review_type=ReviewItemType.BUG,
                severity=ReviewSeverity.LOW,
                description="x",
                suggestion="y",
            )
            for i in (1, 2)
        ]
        ctx.code_review_result = CodeReviewResult(
            reviewed_at="t",
            total_files_reviewed=2,
            items=items,
            overall_assessment="x",
            requires_fixes=True,
        )

    fix_calls = []
    run_main_review_fix_loop(
        context,
        run_review=run_review,
        run_fix=lambda ctx, item: fix_calls.append(item.item_id),
        max_fix_iterations=1,
        auto_fix=False,
        select_items=lambda items: [items[0]],  # only apply the first
    )
    assert fix_calls == [1]


# --- Ralph Wiggum loop --------------------------------------------------------


def test_ralph_wiggum_loop_accepts_by_decision(tmp_path):
    from ai_orchestration.engine.loops import run_ralph_wiggum_loop

    context = _context(tmp_path, ralph_wiggum_enabled=True, ralph_wiggum_threshold=0.8)

    def run_review(ctx):
        ctx.ralph_wiggum_feedback = RalphWiggumFeedback(
            decision=ReviewDecision.ACCEPTED, confidence_score=0.5
        )

    run_ralph_wiggum_loop(context, run_review=run_review, write_state_file=True)
    assert context.is_ralph_wiggum_accepted() is True
    assert (
        context.ralph_wiggum_state_file is None
        or not context.ralph_wiggum_state_file.exists()
    )


def test_ralph_wiggum_loop_accepts_by_threshold(tmp_path):
    from ai_orchestration.engine.loops import run_ralph_wiggum_loop

    context = _context(tmp_path, ralph_wiggum_enabled=True, ralph_wiggum_threshold=0.8)

    def run_review(ctx):
        ctx.ralph_wiggum_feedback = RalphWiggumFeedback(
            decision=ReviewDecision.NEEDS_REVISION, confidence_score=0.9
        )

    run_ralph_wiggum_loop(context, run_review=run_review, write_state_file=True)
    assert context.is_ralph_wiggum_accepted() is True


def test_ralph_wiggum_loop_accepts_on_promise_tag_even_below_threshold(tmp_path):
    # Mutation-guarded: finding #19 — before the fix, check_promise_completion
    # was never called, so --completion-promise had no effect on the loop.
    # A low confidence score that would normally continue iterating must
    # still stop here because the promise tag is present in comments.
    from ai_orchestration.engine.loops import run_ralph_wiggum_loop
    from ai_orchestration.models.context import IterationMetadata

    context = _context(
        tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_threshold=0.99,
        ralph_wiggum_completion_promise="DONE",
        ralph_wiggum_iteration=IterationMetadata(review_attempt=1, max_attempts=5),
    )
    call_count = {"n": 0}

    def run_review(ctx):
        call_count["n"] += 1
        ctx.ralph_wiggum_feedback = RalphWiggumFeedback(
            decision=ReviewDecision.NEEDS_REVISION,
            confidence_score=0.1,
            comments=["all set <promise>DONE</promise>"],
        )

    run_ralph_wiggum_loop(context, run_review=run_review, write_state_file=False)
    assert call_count["n"] == 1  # stopped immediately on the promise tag


def test_ralph_wiggum_loop_ignores_mismatched_promise_tag(tmp_path):
    from ai_orchestration.engine.loops import run_ralph_wiggum_loop
    from ai_orchestration.models.context import IterationMetadata

    context = _context(
        tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_threshold=0.99,
        ralph_wiggum_completion_promise="DONE",
        ralph_wiggum_iteration=IterationMetadata(review_attempt=1, max_attempts=2),
    )
    call_count = {"n": 0}

    def run_review(ctx):
        call_count["n"] += 1
        ctx.ralph_wiggum_feedback = RalphWiggumFeedback(
            decision=ReviewDecision.NEEDS_REVISION,
            confidence_score=0.1,
            comments=["still working <promise>WRONG_TAG</promise>"],
        )

    run_ralph_wiggum_loop(context, run_review=run_review, write_state_file=False)
    assert call_count["n"] == 2  # ran to max_attempts; promise never matched


def test_ralph_wiggum_loop_stops_at_max_iterations(tmp_path):
    from ai_orchestration.engine.loops import run_ralph_wiggum_loop
    from ai_orchestration.models.context import IterationMetadata

    context = _context(
        tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_threshold=0.9,
        ralph_wiggum_iteration=IterationMetadata(review_attempt=1, max_attempts=3),
    )
    call_count = {"n": 0}

    def run_review(ctx):
        call_count["n"] += 1
        ctx.ralph_wiggum_feedback = RalphWiggumFeedback(
            decision=ReviewDecision.NEEDS_REVISION, confidence_score=0.1
        )

    run_ralph_wiggum_loop(context, run_review=run_review, write_state_file=False)
    assert call_count["n"] == 3  # max_attempts
    assert context.is_ralph_wiggum_accepted() is False


def test_ralph_wiggum_loop_writes_and_cleans_up_state_file(tmp_path):
    from ai_orchestration.engine.loops import run_ralph_wiggum_loop

    context = _context(tmp_path, ralph_wiggum_enabled=True, ralph_wiggum_threshold=0.5)
    state_files_seen = []

    def run_review(ctx):
        if ctx.ralph_wiggum_state_file is not None:
            state_files_seen.append(ctx.ralph_wiggum_state_file.exists())
        ctx.ralph_wiggum_feedback = RalphWiggumFeedback(
            decision=ReviewDecision.ACCEPTED, confidence_score=0.9
        )

    run_ralph_wiggum_loop(context, run_review=run_review, write_state_file=True)
    assert state_files_seen == [True]  # existed during the loop
    assert context.ralph_wiggum_state_file is not None
    assert not context.ralph_wiggum_state_file.exists()  # cleaned up after


def test_ralph_wiggum_loop_carries_forward_top_three_fixes(tmp_path):
    from ai_orchestration.engine.loops import run_ralph_wiggum_loop

    context = _context(tmp_path, ralph_wiggum_enabled=True, ralph_wiggum_threshold=0.9)
    items = [
        CodeReviewItem(
            item_id=i,
            file_path=Path(f"f{i}.py"),
            review_type=ReviewItemType.BUG,
            severity=ReviewSeverity.LOW,
            description="x",
            suggestion="y",
        )
        for i in range(1, 6)  # 5 items; only top 3 should carry forward
    ]
    context.code_review_result = CodeReviewResult(
        reviewed_at="t",
        total_files_reviewed=5,
        items=items,
        overall_assessment="x",
        requires_fixes=True,
    )
    fix_calls = []

    def run_review(ctx):
        ctx.ralph_wiggum_feedback = RalphWiggumFeedback(
            decision=ReviewDecision.ACCEPTED,
            confidence_score=0.95,
            suggestions=["fix it"],
        )

    run_ralph_wiggum_loop(
        context,
        run_review=run_review,
        write_state_file=False,
        run_fix=lambda ctx, item: fix_calls.append(item.item_id),
        run_code_review=lambda ctx: None,
    )
    # Accepted on first pass: suggestions-driven fix-forward never fires
    # here because acceptance short-circuits before the fix-forward step.
    assert fix_calls == []


def test_ralph_wiggum_loop_applies_top_three_fixes_when_not_yet_accepted(tmp_path):
    from ai_orchestration.engine.loops import run_ralph_wiggum_loop
    from ai_orchestration.models.context import IterationMetadata

    context = _context(
        tmp_path,
        ralph_wiggum_enabled=True,
        ralph_wiggum_threshold=0.99,
        ralph_wiggum_iteration=IterationMetadata(review_attempt=1, max_attempts=2),
    )
    items = [
        CodeReviewItem(
            item_id=i,
            file_path=Path(f"f{i}.py"),
            review_type=ReviewItemType.BUG,
            severity=ReviewSeverity.LOW,
            description="x",
            suggestion="y",
        )
        for i in range(1, 6)
    ]
    context.code_review_result = CodeReviewResult(
        reviewed_at="t",
        total_files_reviewed=5,
        items=items,
        overall_assessment="x",
        requires_fixes=True,
    )
    fix_calls = []
    review_reruns = {"n": 0}

    def run_review(ctx):
        ctx.ralph_wiggum_feedback = RalphWiggumFeedback(
            decision=ReviewDecision.NEEDS_REVISION,
            confidence_score=0.1,
            suggestions=["fix it"],
        )

    def run_code_review(ctx):
        review_reruns["n"] += 1

    run_ralph_wiggum_loop(
        context,
        run_review=run_review,
        write_state_file=False,
        run_fix=lambda ctx, item: fix_calls.append(item.item_id),
        run_code_review=run_code_review,
    )
    # Not accepted on either pass (max_attempts=2). Fix-forward (rerun code
    # review, apply top 3 items) only fires on a non-terminal iteration
    # (attempt 1 of 2); the final attempt breaks on max-iterations without
    # another fix-forward pass.
    assert fix_calls == [1, 2, 3]
    assert review_reruns["n"] == 1
