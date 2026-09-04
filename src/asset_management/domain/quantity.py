from dataclasses import dataclass
from decimal import Decimal

from .errors import InvariantViolation
from .money import exact_decimal


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal

    def __post_init__(self) -> None:
        value = exact_decimal(self.value)
        if value < 0:
            raise InvariantViolation("quantity cannot be negative")
        object.__setattr__(self, "value", value)

    @classmethod
    def of(cls, value: Decimal | str | int) -> "Quantity":
        return cls(exact_decimal(value))

    def round_down(self, increment: Decimal | str | int) -> "Quantity":
        step = exact_decimal(increment)
        if step <= 0:
            raise InvariantViolation("quantity increment must be positive")
        return Quantity((self.value // step) * step)
