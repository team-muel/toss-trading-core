"""Irreversible runtime gates for the investment-decision pipeline."""

from dataclasses import dataclass
from enum import IntEnum

from asset_management.domain.errors import InvariantViolation, NoTrade


class PipelineStage(IntEnum):
    INVESTMENT_POLICY = 1
    ACCOUNT_TRUTH = 2
    TIME_TRUTH = 3
    DATA_TRUTH = 4
    FINANCIAL_CALCULATION = 5
    TARGET_PORTFOLIO = 6
    RISK_CONTROL = 7
    ORDER = 8


STAGE_ORDER = tuple(PipelineStage)


@dataclass(frozen=True, slots=True)
class StageEvidence:
    stage: PipelineStage
    evidence_id: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.content_hash.strip():
            raise InvariantViolation("stage evidence requires an id and content hash")


@dataclass(frozen=True, slots=True)
class InvestmentPipeline:
    """A run can only complete the next stage; skipping and reversal are forbidden."""

    runtime_run_id: str
    completed: tuple[StageEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.runtime_run_id.strip():
            raise InvariantViolation("runtime_run_id cannot be blank")
        stages = tuple(item.stage for item in self.completed)
        if stages != STAGE_ORDER[: len(stages)]:
            raise InvariantViolation("pipeline evidence is missing, reordered, or duplicated")

    @property
    def next_stage(self) -> PipelineStage | None:
        return STAGE_ORDER[len(self.completed)] if len(self.completed) < len(STAGE_ORDER) else None

    def complete(self, evidence: StageEvidence) -> "InvestmentPipeline":
        if evidence.stage is not self.next_stage:
            expected = self.next_stage.name if self.next_stage else "NONE"
            raise InvariantViolation(
                f"cannot complete {evidence.stage.name}; required next stage is {expected}"
            )
        return InvestmentPipeline(self.runtime_run_id, self.completed + (evidence,))

    def require_order_authorized(self) -> None:
        required = STAGE_ORDER.index(PipelineStage.RISK_CONTROL) + 1
        if len(self.completed) < required:
            raise NoTrade("order is blocked until every stage through RISK_CONTROL is evidenced")
