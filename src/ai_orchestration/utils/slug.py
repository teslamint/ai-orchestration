"""Project and command slug helpers, ported verbatim from `8ee3c4c`.

`generate_project_name` mirrors `_generate_project_name`: ASCII-only, so
non-English goals fall back to `"project"`. `generate_command_slug` mirrors
`_generate_command_slug`, which keeps word characters and falls back to
`"cmd"`.
"""

from __future__ import annotations

import re


def generate_command_slug(command: str, max_length: int = 30) -> str:
    """Generate a short slug from a command for use in log filenames."""
    slug = re.sub(r"[^\w\s-]", "", command.lower())
    slug = re.sub(r"[\s_-]+", "_", slug)
    slug = slug.strip("_")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("_")
    return slug if slug else "cmd"


def generate_project_name(goal: str, max_length: int = 30) -> str:
    """Generate an ASCII-only project name slug from a goal string."""
    slug = re.sub(r"[^a-z0-9\s-]", "", goal.lower())
    slug = re.sub(r"[\s_-]+", "_", slug)
    slug = slug.strip("_")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("_")
    return slug if slug else "project"
