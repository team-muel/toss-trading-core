from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from .errors import InvariantViolation

_MINOR_UNITS = {"KRW": 0, "USD": 2}


def exact_decimal(value: Decimal | str | int) -> Decimal:
    if isinstance(value, float):
        raise TypeError("float is forbidden at exact-value boundaries")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise InvariantViolation(f"invalid decimal: {value!r}") from exc
    if not result.is_finite():
        raise InvariantViolation("money must be finite")
    return result


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        currency = self.currency.strip().upper()
        if currency not in _MINOR_UNITS:
            raise InvariantViolation(f"unsupported currency: {currency}")
        amount = exact_decimal(self.amount)
        quantum = Decimal(1).scaleb(-_MINOR_UNITS[currency])
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "amount", amount.quantize(quantum, ROUND_HALF_EVEN))

    @classmethod
    def of(cls, amount: Decimal | str | int, currency: str) -> Money:
        return cls(exact_decimal(amount), currency)

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise InvariantViolation("cross-currency arithmetic requires an explicit FX rate")
