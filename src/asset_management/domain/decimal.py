from decimal import Decimal, InvalidOperation

from .errors import InvariantViolation


def exact_decimal(value: Decimal | str | int) -> Decimal:
    if isinstance(value, float):
        raise TypeError("float is forbidden at exact-value boundaries")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise InvariantViolation(f"invalid decimal: {value!r}") from exc
    if not result.is_finite():
        raise InvariantViolation("decimal value must be finite")
    return result
