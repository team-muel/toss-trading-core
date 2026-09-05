from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .decimal import exact_decimal
from .errors import InvariantViolation


class Currency(StrEnum):
    USD = "USD"
    KRW = "KRW"

    @classmethod
    def parse(cls, value: str) -> "Currency":
        try:
            return cls(value.strip().upper())
        except ValueError as exc:
            raise InvariantViolation(f"unsupported currency: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class Weight:
    value: Decimal

    def __post_init__(self) -> None:
        value = exact_decimal(self.value)
        if not Decimal("0") <= value <= Decimal("1"):
            raise InvariantViolation("weight must be between zero and one")
        object.__setattr__(self, "value", value)

    @classmethod
    def of(cls, value: Decimal | str | int) -> "Weight":
        return cls(exact_decimal(value))


@dataclass(frozen=True, slots=True)
class Return:
    """A simple return, expressed as a decimal (5% is 0.05)."""

    value: Decimal

    def __post_init__(self) -> None:
        value = exact_decimal(self.value)
        if value < Decimal("-1"):
            raise InvariantViolation("simple return cannot be below -100%")
        object.__setattr__(self, "value", value)

    @classmethod
    def of(cls, value: Decimal | str | int) -> "Return":
        return cls(exact_decimal(value))
