"""Read-only compound-loop contract adapter."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ai_orchestration.compound_loop.ledger import (
    LedgerError,
    ReleaseLoopState,
    TransitionResult,
    load_ledger,
    next_transition,
    save_ledger,
)
from ai_orchestration.compound_loop.plan import PlanValidation, validate_plan
from ai_orchestration.compound_loop.source import (
    ContractError,
    ContractManifest,
    ContractSource,
    load_contract_source,
)


@dataclass
class CompoundLoopAdapter:
    """Compose validated contracts with the local pipeline's phase ledger."""

    source: ContractSource
    plan: Optional[PlanValidation]
    ledger_path: Path
    state: ReleaseLoopState

    @classmethod
    def prepare(
        cls,
        source_root: Path,
        manifest_path: Path,
        ledger_path: Path,
        *,
        plan_path: Optional[Path] = None,
        resume: bool = False,
    ) -> "CompoundLoopAdapter":
        source = load_contract_source(source_root, manifest_path)
        plan = validate_plan(plan_path) if plan_path is not None else None
        current = load_ledger(ledger_path)
        if current is not None and not resume:
            raise LedgerError("existing release-loop ledger requires --resume")
        if current is None:
            current = ReleaseLoopState("implement", "in-progress")
            save_ledger(current, ledger_path)
        elif current.phase not in {"implement", "review"}:
            raise LedgerError("release-loop ledger is outside the local pipeline")
        return cls(
            source=source, plan=plan, ledger_path=Path(ledger_path), state=current
        )

    @property
    def next_stage(self) -> Optional[str]:
        return self.state.next_stage

    def _save_transition(self, event: str) -> None:
        result = next_transition(self.state, event)
        self.state = result.state
        save_ledger(self.state, self.ledger_path)

    def begin_stage(self, stage: str) -> bool:
        """Start a pending model stage, or resume the same interrupted stage."""

        if stage in self.state.completed_stages:
            return False
        if self.state.current_stage == stage:
            return True
        self._save_transition(f"stage-start:{stage}")
        return True

    def complete_stage(self, stage: str) -> None:
        """Persist one completed model-stage marker."""

        if stage in self.state.completed_stages:
            return
        self._save_transition(f"stage-complete:{stage}")

    def complete_optional_stage(self, stage: str) -> None:
        """Record a stage that was intentionally a no-op in the local loop."""

        if stage in self.state.completed_stages:
            return
        if not self.begin_stage(stage):
            return
        self.complete_stage(stage)

    def complete_implementation(self) -> None:
        """Move the release phase to review after all model stages finish."""

        if self.state.phase == "review":
            return
        if self.state.next_stage is not None:
            raise LedgerError("implementation cannot complete with pending stages")
        self._save_transition("implementation-complete")


__all__ = [
    "ContractError",
    "ContractManifest",
    "ContractSource",
    "CompoundLoopAdapter",
    "LedgerError",
    "PlanValidation",
    "ReleaseLoopState",
    "TransitionResult",
    "load_ledger",
    "load_contract_source",
    "next_transition",
    "save_ledger",
    "validate_plan",
]
