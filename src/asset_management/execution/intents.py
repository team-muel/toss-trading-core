from dataclasses import dataclass
from decimal import Decimal

from asset_management.domain.errors import InvariantViolation
from asset_management.domain.decimal import exact_decimal


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
    target_weights: tuple[TargetWeight, ...]
    rationale: tuple[str, ...]
