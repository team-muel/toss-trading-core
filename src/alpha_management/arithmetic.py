"""Strict elementwise arithmetic for the canonical expression evaluator.

Only a scalar may broadcast. Instrument and observation axes never align by
position, union-fill, truncation or implicit zero. Undefined division stays
unavailable rather than being represented as a neutral or infinite score.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from operator import add, mul, sub

Panel = Mapping[str, Sequence[float | None]]
Operand = int | float | Panel
BINARY_OPERATORS = frozenset({"add", "subtract", "multiply", "divide"})


def _number(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not an arithmetic operand")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("arithmetic operand must be finite") from exc
    if not isfinite(number):
        raise ValueError("arithmetic operand must be finite")
    return number


def binary_panel(operator: str, left: Operand, right: Operand) -> dict[str, list[float | None]]:
    if operator not in BINARY_OPERATORS:
        raise ValueError("unknown arithmetic operator")
    left_panel, right_panel = isinstance(left, Mapping), isinstance(right, Mapping)
    if not left_panel and not right_panel:
        raise ValueError("arithmetic requires at least one panel")
    source = left if left_panel else right
    if left_panel and right_panel and set(left) != set(right):
        raise ValueError("arithmetic instrument axes differ")
    lengths = {len(values) for values in source.values()}
    if len(lengths) > 1:
        raise ValueError("arithmetic period axes differ")
    length = next(iter(lengths), 0)
    if right_panel and any(len(values) != length for values in right.values()):
        raise ValueError("arithmetic period axes differ")
    left_scalar = None if left_panel else _number(left)
    right_scalar = None if right_panel else _number(right)
    output = {}
    operations = {"add": add, "subtract": sub, "multiply": mul}
    for instrument in source:
        values = []
        for index in range(length):
            a = left[instrument][index] if left_panel else left_scalar
            b = right[instrument][index] if right_panel else right_scalar
            # Validate the present operand even when the other is missing.
            a = None if a is None else _number(a)
            b = None if b is None else _number(b)
            if a is None or b is None or (operator == "divide" and b == 0):
                values.append(None)
                continue
            result = a / b if operator == "divide" else operations[operator](a, b)
            if not isfinite(result):
                raise ValueError("arithmetic result must be finite")
            values.append(result)
        output[instrument] = values
    return output
