from dataclasses import dataclass

from asset_management.domain.enums import DataStatus, DecisionAction

from .reason_codes import ReasonCode


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    action: DecisionAction
    reasons: tuple[ReasonCode, ...]


class RiskGovernor:
    def evaluate(self, *, statuses: tuple[DataStatus, ...], reconciled: bool, limit_breached: bool) -> GovernanceDecision:
        reasons: list[ReasonCode] = []
        if any(status is not DataStatus.KNOWN for status in statuses):
            reasons.append(ReasonCode.DATA_NOT_KNOWN)
        if not reconciled:
            reasons.append(ReasonCode.RECONCILIATION_REQUIRED)
        if limit_breached:
            reasons.append(ReasonCode.LIMIT_BREACH)
        return GovernanceDecision(DecisionAction.BLOCK, tuple(reasons)) if reasons else GovernanceDecision(DecisionAction.ALLOW, (ReasonCode.PASSED,))
