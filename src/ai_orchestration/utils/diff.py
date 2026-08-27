"""Unified diff generation, ported verbatim from `_generate_diff` at `8ee3c4c`."""

from __future__ import annotations

import difflib


def generate_diff(old_content: str, new_content: str, file_path: str) -> str:
    """Generate a unified diff between two versions of a file's content."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    )
    return "".join(diff)
