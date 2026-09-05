from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

from .decimal import exact_decimal
from .errors import InvariantViolation
from .scalars import Currency

_MINOR_UNITS = {"KRW": 0, "USD": 2}


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: Currency | str

    def __post_init__(self) -> None:
        currency = Currency.parse(str(self.currency))
        amount = exact_decimal(self.amount)
        quantum = Decimal(1).scaleb(-_MINOR_UNITS[currency.value])
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "amount", amount.quantize(quantum, ROUND_HALF_EVEN))

    @classmethod
    def of(cls, amount: Decimal | str | int, currency: str) -> Money:
        return cls(exact_decimal(amount), Currency.parse(currency))

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise InvariantViolation("cross-currency arithmetic requires an explicit FX rate")
