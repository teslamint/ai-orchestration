"""Typed error hierarchy for ai_orchestration.

Every stage failure and startup failure raises one of these so callers can
distinguish configuration mistakes, provider/transport faults, routing
rejections, approval-gate refusals, and run-state corruption without string
matching.
"""


class OrchestrationError(Exception):
    """Base class for every error raised by ai_orchestration."""


class ConfigError(OrchestrationError):
    """A configuration file or CLI value is malformed or incomplete."""


class RoutingError(OrchestrationError):
    """A resolved model or binary slot failed validation.

    Raised for an unknown proxy model id on a reachable catalog, a missing
    binary on PATH, or an unknown stage name. Never raised for an
    unreachable catalog, which is skipped rather than failed (S5).
    """


class ProviderError(OrchestrationError):
    """A provider call failed after every configured fallback was applied."""


class TaskExecutionError(OrchestrationError):
    """An executor-stage task (e.g. a `run_command` step) failed.

    Raised when the implementation plan's own build/test/migration command
    exits nonzero after retries; a failing task must not be reported as
    pipeline success.
    """


class GateError(OrchestrationError):
    """A non-interactive approval gate was reached without its authorizing flag."""


class StateError(OrchestrationError):
    """Persisted run state is missing, corrupt, or inconsistent with a resume request."""
