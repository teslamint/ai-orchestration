"""Deterministic measurement of content-free provider call events."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ai_orchestration.providers.call_events import CallEvent, read_call_events

_MIGRATION_GATE = "requires an approved migration spec and a repeated before-after eval"


@dataclass(frozen=True)
class PromptMeasurementReport:
    source_sha256: str
    event_count: int
    prompt_bytes_total: int
    estimated_input_tokens_total: int
    usage: dict[str, int]
    calls: tuple[dict[str, object], ...]
    ranked_groups: tuple[dict[str, object], ...]
    decision: str = "no-migration"
    migration_gate: str = _MIGRATION_GATE

    def to_dict(self) -> dict[str, object]:
        return {
            "version": "prompt-measurement/v1",
            "source_sha256": self.source_sha256,
            "event_count": self.event_count,
            "prompt_bytes_total": self.prompt_bytes_total,
            "estimated_input_tokens_total": self.estimated_input_tokens_total,
            "usage": dict(self.usage),
            "calls": [dict(call) for call in self.calls],
            "ranked_groups": [dict(group) for group in self.ranked_groups],
            "decision": self.decision,
            "migration_gate": self.migration_gate,
        }


def measure_prompt_payloads(path: Path) -> PromptMeasurementReport:
    """Measure one validated event stream without reading prompt content."""
    source = Path(path)
    if source.is_symlink():
        raise ValueError(f"call-event path must not be a symlink: {source}")
    try:
        source_bytes = source.read_bytes()
    except OSError as exc:
        raise ValueError(f"call-event file is unreadable: {source}") from exc
    events = read_call_events(source)
    groups: defaultdict[tuple[str, str], list[CallEvent]] = defaultdict(list)
    calls: list[dict[str, object]] = []
    for ordinal, event in enumerate(events, 1):
        groups[(event.stage, event.provider_id)].append(event)
        calls.append(
            {
                "ordinal": ordinal,
                "run_id": event.run_id,
                "task_id": event.task_id,
                "stage": event.stage,
                "provider_kind": event.provider_kind,
                "provider_id": event.provider_id,
                "attempt": event.attempt,
                "prompt_bytes": event.prompt_bytes,
                "estimated_input_tokens": event.estimated_input_tokens,
                "reported_input_tokens": event.reported_input_tokens,
                "reported_output_tokens": event.reported_output_tokens,
                "transport_retry_count": event.transport_retry_count,
                "latency_ms": event.latency_ms,
                "outcome": event.outcome,
                "error_kind": event.error_kind,
            }
        )
    ranked = []
    for (stage, provider_id), grouped in groups.items():
        available = sum(
            event.reported_input_tokens is not None
            or event.reported_output_tokens is not None
            for event in grouped
        )
        ranked.append(
            {
                "stage": stage,
                "provider_id": provider_id,
                "call_count": len(grouped),
                "prompt_bytes_total": sum(event.prompt_bytes for event in grouped),
                "prompt_bytes_max": max(event.prompt_bytes for event in grouped),
                "estimated_input_tokens_total": sum(
                    event.estimated_input_tokens for event in grouped
                ),
                "latency_ms_total": sum(event.latency_ms for event in grouped),
                "usage_available_events": available,
            }
        )
    ranked.sort(
        key=lambda group: (
            -int(group["prompt_bytes_total"]),
            -int(group["prompt_bytes_max"]),
            str(group["stage"]),
            str(group["provider_id"]),
        )
    )
    available_events = sum(
        event.reported_input_tokens is not None
        or event.reported_output_tokens is not None
        for event in events
    )
    return PromptMeasurementReport(
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        event_count=len(events),
        prompt_bytes_total=sum(event.prompt_bytes for event in events),
        estimated_input_tokens_total=sum(
            event.estimated_input_tokens for event in events
        ),
        usage={
            "available_events": available_events,
            "unavailable_events": len(events) - available_events,
        },
        calls=tuple(calls),
        ranked_groups=tuple(ranked),
    )


def write_measurement_report(
    input_path: Path, output_path: Path
) -> PromptMeasurementReport:
    """Write one deterministic private report using an atomic replacement."""
    output = Path(output_path).absolute()
    _reject_symlink_components(output.parent)
    if output.is_symlink():
        raise ValueError(f"measurement output must not be a symlink: {output}")
    report = measure_prompt_payloads(Path(input_path))
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                report.to_dict(),
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return report


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(
                f"measurement output parent must not be a symlink: {current}"
            )
