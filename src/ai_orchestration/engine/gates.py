"""Approval gates: fail-closed non-interactive behavior, interactive fix selection.

Per §Non-interactive gates: when a gate is reached, stdin is not a TTY, and
the authorizing flag is absent, the run does not block waiting for input —
it raises `PausedRun` naming the exact flag that would have authorized it,
so the caller can persist resumable state and exit non-zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

from ai_orchestration.errors import GateError

T = TypeVar("T")


class PausedRun(GateError):
    """Raised when a non-interactive gate is reached without authorization."""

    def __init__(self, authorizing_flag: str, exit_code: int = 1):
        self.authorizing_flag = authorizing_flag
        self.exit_code = exit_code
        self.pause_reason = (
            f"approval gate requires {authorizing_flag} in a non-interactive run"
        )
        super().__init__(self.pause_reason)


@dataclass
class ApprovalGate:
    """A single approval gate: interactive prompt or fail-closed non-TTY behavior."""

    is_tty: bool
    ask: Callable[[str], bool]

    def request(self, prompt: str, *, authorizing_flag: str, authorized: bool) -> bool:
        """Request approval for `prompt`.

        If already `authorized` (e.g. --auto-run passed), proceeds without
        asking. Otherwise, if interactive (`is_tty`), asks via `self.ask`.
        If non-interactive and not authorized, raises `PausedRun` naming
        `authorizing_flag` rather than blocking.
        """
        if authorized:
            return True
        if not self.is_tty:
            raise PausedRun(authorizing_flag)
        return self.ask(prompt)


def select_fix_items(
    items: list[T],
    *,
    choice: Optional[str] = None,
    auto_fix: bool = False,
) -> list[T]:
    """Select which review items to fix, ported from `_prompt_fix_selection`.

    `auto_fix=True` applies every item without asking. Otherwise `choice`
    is parsed: "a" applies all, "n" skips all, comma-separated 1-indexed
    numbers select specific items, and anything unparseable skips all
    (matching the legacy invalid-selection fallback).
    """
    if not items:
        return []
    if auto_fix:
        return items
    if choice is None:
        return []
    normalized = choice.strip().lower()
    if normalized == "a":
        return items
    if normalized == "n":
        return []
    try:
        indices = [int(x.strip()) - 1 for x in choice.split(",")]
        return [items[i] for i in indices if 0 <= i < len(items)]
    except (ValueError, IndexError):
        return []
