import pytest

from asset_management.domain.errors import InvariantViolation, NoTrade
from asset_management.orchestration.pipelines import (
    InvestmentPipeline,
    PipelineStage,
    StageEvidence,
)


def evidence(stage: PipelineStage) -> StageEvidence:
    return StageEvidence(stage, f"evidence-{stage.name}", f"hash-{stage.name}")


def test_pipeline_cannot_skip_policy_or_reverse_stage_order():
    pipeline = InvestmentPipeline("run-1")
    with pytest.raises(InvariantViolation):
        pipeline.complete(evidence(PipelineStage.ACCOUNT_TRUTH))
    pipeline = pipeline.complete(evidence(PipelineStage.INVESTMENT_POLICY))
    with pytest.raises(InvariantViolation):
        pipeline.complete(evidence(PipelineStage.INVESTMENT_POLICY))
    assert pipeline.next_stage is PipelineStage.ACCOUNT_TRUTH


def test_order_is_blocked_until_risk_control_has_evidence():
    pipeline = InvestmentPipeline("run-1")
    for stage in (
        PipelineStage.INVESTMENT_POLICY,
        PipelineStage.ACCOUNT_TRUTH,
        PipelineStage.TIME_TRUTH,
        PipelineStage.DATA_TRUTH,
        PipelineStage.FINANCIAL_CALCULATION,
        PipelineStage.TARGET_PORTFOLIO,
    ):
        pipeline = pipeline.complete(evidence(stage))
    with pytest.raises(NoTrade):
        pipeline.require_order_authorized()
    pipeline = pipeline.complete(evidence(PipelineStage.RISK_CONTROL))
    pipeline.require_order_authorized()
    assert pipeline.next_stage is PipelineStage.ORDER


def test_pipeline_rejects_preassembled_out_of_order_evidence():
    with pytest.raises(InvariantViolation):
        InvestmentPipeline(
            "run-1",
            (
                evidence(PipelineStage.INVESTMENT_POLICY),
                evidence(PipelineStage.TIME_TRUTH),
            ),
        )
