"""JSON-list, JSON-object, and fenced-code extraction helpers.

`extract_json_list` and `extract_code_content` are ported verbatim from
`orchestrator_cli.py`'s `_extract_json_list` and `_extract_code_content` at
`8ee3c4c`: identical extraction precedence and identical output for the same
input. `extract_json_object` generalizes the inline `re.search(r"\\{[\\s\\S]*\\}",
output)` + `json.loads()` pattern used at the Stage 5 and Ralph Wiggum
review-parsing call sites (`:1177-1179`, `:1317-1318`) into a reusable
helper with the same "no match or invalid JSON returns None" behavior.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional


def extract_json_list(text: str) -> list[dict[str, Any]]:
    """Extract a JSON list of objects ([...]) from text.

    Tries a direct parse first. On failure, scans for every top-level `[`
    and keeps the last candidate that parses as a list of dicts. Returns an
    empty list when nothing qualifies (the legacy empty result).
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    else:
        if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
            return parsed
    decoder = json.JSONDecoder()
    candidates: list[list[dict[str, Any]]] = []
    idx = 0
    while True:
        idx = text.find("[", idx)
        if idx == -1:
            break
        try:
            parsed, end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            idx += 1
            continue
        if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
            candidates.append(parsed)
        idx = idx + end
    if candidates:
        return candidates[-1]

    return []


def extract_json_object(text: str) -> Optional[dict[str, Any]]:
    """Extract a single JSON object ({...}) from text.

    Finds the first `{...}` span (greedy, matching the legacy inline
    `re.search(r"\\{[\\s\\S]*\\}", output)` pattern) and parses it. Returns
    None when no span is found or the span does not parse as valid JSON;
    callers raise at the provider boundary when they cannot recover.
    """
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def extract_code_content(text: str) -> str:
    """Extract markdown fenced-code-block content from executor output."""
    start_match = re.search(r"```(?:[\w\+\-\.]+)?\s*\n", text)
    if not start_match:
        return text.strip()
    start_index = start_match.end()
    end_index = text.rfind("\n```")
    if end_index != -1 and end_index > start_index:
        return text[start_index:end_index].strip()
    return text[start_index:].strip()
