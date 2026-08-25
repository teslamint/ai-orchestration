"""Approval gate tests: fail-closed non-TTY behavior, authorized proceed (U4).

Covers S2 (command approval) and §Non-interactive gates: a gate reached with
stdin not a TTY and its authorizing flag absent must not block, must persist
resumable state, exit nonzero, and name the specific flag that would have
authorized it.
"""

import pytest

from ai_orchestration.engine.gates import ApprovalGate, PausedRun


def test_gate_proceeds_when_authorized_flag_is_true():
    gate = ApprovalGate(is_tty=True, ask=lambda _prompt: True)
    assert (
        gate.request(
            "run this command?", authorizing_flag="--auto-run", authorized=True
        )
        is True
    )


def test_gate_asks_interactively_when_tty_and_not_pre_authorized():
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return True

    gate = ApprovalGate(is_tty=True, ask=ask)
    result = gate.request(
        "run this command?", authorizing_flag="--auto-run", authorized=False
    )
    assert result is True
    assert asked == ["run this command?"]


def test_gate_respects_interactive_decline():
    gate = ApprovalGate(is_tty=True, ask=lambda _prompt: False)
    result = gate.request(
        "run this command?", authorizing_flag="--auto-run", authorized=False
    )
    assert result is False


def test_gate_fails_closed_when_non_tty_and_not_authorized():
    gate = ApprovalGate(is_tty=False, ask=lambda _prompt: True)
    with pytest.raises(PausedRun) as excinfo:
        gate.request(
            "run this command?", authorizing_flag="--auto-run", authorized=False
        )
    assert "--auto-run" in str(excinfo.value)


def test_gate_non_tty_authorized_proceeds_without_asking():
    def ask(_prompt):
        raise AssertionError("must not prompt when already authorized")

    gate = ApprovalGate(is_tty=False, ask=ask)
    result = gate.request(
        "run this command?", authorizing_flag="--auto-run", authorized=True
    )
    assert result is True


def test_paused_run_names_the_exact_authorizing_flag():
    gate = ApprovalGate(is_tty=False, ask=lambda _p: True)
    with pytest.raises(PausedRun) as excinfo:
        gate.request("apply fixes?", authorizing_flag="--auto-fix", authorized=False)
    assert excinfo.value.authorizing_flag == "--auto-fix"


def test_paused_run_carries_a_resumable_pause_reason():
    gate = ApprovalGate(is_tty=False, ask=lambda _p: True)
    with pytest.raises(PausedRun) as excinfo:
        gate.request(
            "execute command?", authorizing_flag="--auto-run", authorized=False
        )
    assert excinfo.value.pause_reason
    assert "--auto-run" in excinfo.value.pause_reason


def test_gate_exit_code_is_nonzero():
    gate = ApprovalGate(is_tty=False, ask=lambda _p: True)
    with pytest.raises(PausedRun) as excinfo:
        gate.request("x?", authorizing_flag="--auto-approve", authorized=False)
    assert excinfo.value.exit_code != 0


# --- Fix-item selection gate (interactive) ----------------------------------


def test_select_fix_items_apply_all():
    from ai_orchestration.engine.gates import select_fix_items

    items = ["a", "b", "c"]
    result = select_fix_items(items, choice="a")
    assert result == items


def test_select_fix_items_skip_all():
    from ai_orchestration.engine.gates import select_fix_items

    result = select_fix_items(["a", "b"], choice="n")
    assert result == []


def test_select_fix_items_specific_indices():
    from ai_orchestration.engine.gates import select_fix_items

    result = select_fix_items(["a", "b", "c"], choice="1,3")
    assert result == ["a", "c"]


def test_select_fix_items_invalid_choice_skips_all():
    from ai_orchestration.engine.gates import select_fix_items

    result = select_fix_items(["a", "b"], choice="not-a-valid-input")
    assert result == []


def test_select_fix_items_auto_fix_applies_all_without_asking():
    from ai_orchestration.engine.gates import select_fix_items

    items = ["a", "b"]
    result = select_fix_items(items, choice=None, auto_fix=True)
    assert result == items
