#!/usr/bin/env python3
"""Run a versioned batch of independent orchestration commands."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from ai_orchestration.supervisor import (
    RunSpec,
    SupervisionPersistenceError,
    SupervisionPlan,
    SupervisionValidationError,
    run_supervised,
)

_PLAN_KEYS = {
    "version",
    "runs",
    "max_concurrency",
    "rate_limit_retries",
    "rate_limit_delay",
}
_RUN_KEYS = {"run_id", "command", "workspace"}


def _read_plan(path: Path) -> SupervisionPlan:
    if path.is_symlink() or not path.is_file():
        raise SupervisionValidationError("plan must be a regular non-symlink file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupervisionValidationError("plan is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or set(payload) - _PLAN_KEYS:
        raise SupervisionValidationError("plan has an invalid shape")
    if payload.get("version") != "supervision-plan/v1":
        raise SupervisionValidationError("plan has an unsupported version")
    raw_runs = payload.get("runs")
    if not isinstance(raw_runs, list):
        raise SupervisionValidationError("plan runs must be a list")
    runs = []
    for raw_run in raw_runs:
        if not isinstance(raw_run, dict) or set(raw_run) != _RUN_KEYS:
            raise SupervisionValidationError("plan run has an invalid shape")
        command = raw_run["command"]
        if not isinstance(command, list) or not all(
            isinstance(part, str) for part in command
        ):
            raise SupervisionValidationError("plan command must be a string list")
        try:
            runs.append(
                RunSpec(
                    raw_run["run_id"],
                    tuple(command),
                    Path(raw_run["workspace"]),
                )
            )
        except (TypeError, ValueError) as exc:
            raise SupervisionValidationError(
                "plan run fields have invalid types"
            ) from exc
    try:
        return SupervisionPlan(
            tuple(runs),
            payload.get("max_concurrency", min(4, len(runs))),
            payload.get("rate_limit_retries", 0),
            payload.get("rate_limit_delay", 0.0),
        )
    except (TypeError, ValueError) as exc:
        raise SupervisionValidationError("plan options have invalid types") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--rate-limit-retries", type=int)
    parser.add_argument("--rate-limit-delay", type=float)
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = _read_plan(args.plan)
        if args.max_concurrency is not None:
            plan = SupervisionPlan(
                plan.runs,
                args.max_concurrency,
                plan.rate_limit_retries,
                plan.rate_limit_delay,
            )
        if args.rate_limit_retries is not None:
            plan = SupervisionPlan(
                plan.runs,
                plan.max_concurrency,
                args.rate_limit_retries,
                plan.rate_limit_delay,
            )
        if args.rate_limit_delay is not None:
            plan = SupervisionPlan(
                plan.runs,
                plan.max_concurrency,
                plan.rate_limit_retries,
                args.rate_limit_delay,
            )
    except SupervisionValidationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    cancellation = threading.Event()

    def request_cancellation(signum: int, _frame: object) -> None:
        del signum
        cancellation.set()

    previous = {
        signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        for signum in previous:
            signal.signal(signum, request_cancellation)
        try:
            manifest = run_supervised(
                plan,
                args.output_root,
                resume=args.resume,
                cancel_event=cancellation,
            )
        except SupervisionPersistenceError as exc:
            print(f"supervision error: {exc}", file=sys.stderr)
            return 1
        except (OSError, SupervisionValidationError) as exc:
            print(f"configuration error: {exc}", file=sys.stderr)
            return 2
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    print(json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True))
    return manifest.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
