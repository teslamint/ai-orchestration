#!/usr/bin/env python3
"""Publish a deterministic report for one content-free call-event stream."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai_orchestration.evals.prompt_measurement import write_measurement_report
from ai_orchestration.providers.call_events import CallEventError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(description=__doc__)
    parser.add_argument("--call-events", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        report = write_measurement_report(Path(args.call_events), Path(args.output))
    except (CallEventError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
