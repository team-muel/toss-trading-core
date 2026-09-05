from dataclasses import dataclass
from decimal import Decimal

from asset_management.domain.errors import InvariantViolation
from asset_management.domain.decimal import exact_decimal
from asset_management.decisions.governor import ApprovedRiskDecision, DecisionState


@dataclass(frozen=True, slots=True)
class TargetWeight:
    instrument_id: str
    target: Decimal
    current: Decimal

    def __post_init__(self) -> None:
        target = exact_decimal(self.target)
        current = exact_decimal(self.current)
        if not Decimal("0") <= target <= Decimal("1"):
            raise InvariantViolation("target weight must be between zero and one")
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "current", current)


@dataclass(frozen=True, slots=True)
class OrderIntent:
    run_id: str
    policy_version: str
    portfolio_target_id: str
    portfolio_target_hash: str
    risk_authorization: ApprovedRiskDecision
    target_weights: tuple[TargetWeight, ...]
    rationale: tuple[str, ...]

    def __post_init__(self) -> None:
        authorization = self.risk_authorization
        if authorization.state not in (DecisionState.ALLOW, DecisionState.REDUCE):
            raise InvariantViolation("order intent requires an approved risk decision")
        if authorization.runtime_run_id != self.run_id:
            raise InvariantViolation("risk decision belongs to a different runtime run")
        if authorization.policy_version != self.policy_version:
            raise InvariantViolation("risk decision uses a different policy version")
        if authorization.portfolio_target_id != self.portfolio_target_id:
            raise InvariantViolation("risk decision belongs to a different portfolio target")
        if authorization.portfolio_target_hash != self.portfolio_target_hash:
            raise InvariantViolation("portfolio target changed after risk approval")
        if not self.target_weights:
            raise InvariantViolation("order intent requires target weights")
