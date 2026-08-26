"""Shared subprocess-diagnostic helpers.

`truncate_stderr` was duplicated verbatim in `engine/stages.py` and
`providers/cli.py`; this is the single source both import from.
"""

from __future__ import annotations


def truncate_stderr(stderr: str, max_lines: int = 5, max_chars: int = 200) -> str:
    """Truncate stderr to a short excerpt for diagnostics."""
    if not stderr:
        return "(no stderr)"
    lines = stderr.strip().splitlines()[:max_lines]
    excerpt = "\n".join(lines)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "..."
    return excerpt
