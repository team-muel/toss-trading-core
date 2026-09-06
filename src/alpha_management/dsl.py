"""Typed Fast Expression AST, validator, canonical identity, and evaluator.

The small Lark frontend is structurally inspired by marketneutral/alphatools'
Apache-2.0 expression grammar. Its Zipline runtime is intentionally not used.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
from importlib.resources import files
from typing import Protocol, TypeAlias, cast

from lark import Lark, Transformer

from asset_management.time.asof import AsOfContext, require_as_of_context

from . import operators as ops
from .datafields import RepositoryDataFields

Panel: TypeAlias = Mapping[str, Sequence[float | None]]
PanelValue: TypeAlias = dict[str, list[float | None]]
MAX_TIME_WINDOW = 10_000


class ExpressionError(ValueError):
    """Raised when syntax, typing, or evaluation violates the DSL contract."""


class ValueType(str, Enum):
    SCALAR = "Scalar"
    INTEGER = "Integer"
    PANEL = "Panel"
    GROUP_FIELD = "GroupField"


class Axis(str, Enum):
    ELEMENT = "ELEMENT"
    TIME = "TIME"
    CROSS_SECTION = "CROSS_SECTION"


@dataclass(frozen=True, slots=True)
class LiteralNode:
    value: Decimal


@dataclass(frozen=True, slots=True)
class DataFieldNode:
    name: str


@dataclass(frozen=True, slots=True)
class GroupFieldNode:
    name: str


@dataclass(frozen=True, slots=True)
class CallNode:
    operator: str
    arguments: tuple["ExpressionNode", ...]


ExpressionNode: TypeAlias = LiteralNode | DataFieldNode | GroupFieldNode | CallNode


class _AstTransformer(Transformer):
    def number(self, values):
        try:
            return LiteralNode(Decimal(str(values[0])))
        except InvalidOperation as exc:
            raise ExpressionError("invalid numeric literal") from exc

    def identifier(self, values):
        return str(values[0])

    def arguments(self, values):
        return tuple(values)

    def call(self, values):
        arguments = values[1] if len(values) == 2 and values[1] is not None else ()
        return CallNode(str(values[0]).lower(), tuple(arguments))


_PARSER = Lark(
    files("alpha_management").joinpath("fast_expression.lark").read_text(encoding="utf-8"),
    parser="lalr",
    start="start",
)


@dataclass(frozen=True, slots=True)
class OperatorSpec:
    name: str
    arguments: tuple[ValueType, ...]
    result: ValueType
    axis: Axis


def _spec(name: str, arguments: tuple[ValueType, ...], axis: Axis) -> OperatorSpec:
    return OperatorSpec(name, arguments, ValueType.PANEL, axis)


OPERATOR_REGISTRY = {
    name: _spec(name, (ValueType.PANEL,), Axis.CROSS_SECTION)
    for name in ("rank", "zscore", "scale", "winsorize")
}
OPERATOR_REGISTRY["sign"] = _spec("sign", (ValueType.PANEL,), Axis.ELEMENT)
OPERATOR_REGISTRY.update({
    name: _spec(name, (ValueType.PANEL, ValueType.INTEGER), Axis.TIME)
    for name in (
        "ts_delay", "ts_delta", "ts_sum", "ts_mean", "ts_stddev",
        "ts_zscore", "ts_rank", "ts_decay_linear", "ts_max", "ts_min",
    )
})
OPERATOR_REGISTRY.update({
    name: _spec(name, (ValueType.PANEL, ValueType.GROUP_FIELD), Axis.CROSS_SECTION)
    for name in ("group_neutralize", "group_rank")
})


def parse_expression(source: str) -> ExpressionNode:
    if not source.strip():
        raise ExpressionError("expression cannot be blank")
    try:
        return cast(ExpressionNode, _AstTransformer().transform(_PARSER.parse(source)))
    except ExpressionError:
        raise
    except Exception as exc:
        raise ExpressionError(f"invalid Fast Expression: {exc}") from exc


def validate_expression(
    node: ExpressionNode,
    *,
    data_fields: set[str] | frozenset[str],
    group_fields: set[str] | frozenset[str] = frozenset(),
) -> ValueType:
    if isinstance(node, LiteralNode):
        return ValueType.INTEGER if node.value == node.value.to_integral_value() else ValueType.SCALAR
    if isinstance(node, str):
        if node in data_fields:
            return ValueType.PANEL
        if node in group_fields:
            return ValueType.GROUP_FIELD
        raise ExpressionError(f"unknown datafield: {node}")
    if isinstance(node, DataFieldNode):
        return validate_expression(node.name, data_fields=data_fields, group_fields=group_fields)
    if isinstance(node, GroupFieldNode):
        return validate_expression(node.name, data_fields=data_fields, group_fields=group_fields)
    spec = OPERATOR_REGISTRY.get(node.operator)
    if spec is None:
        raise ExpressionError(f"unknown operator: {node.operator}")
    if len(node.arguments) != len(spec.arguments):
        raise ExpressionError(
            f"{node.operator} expects {len(spec.arguments)} arguments, got {len(node.arguments)}"
        )
    actual = tuple(
        validate_expression(item, data_fields=data_fields, group_fields=group_fields)
        for item in node.arguments
    )
    if actual != spec.arguments:
        raise ExpressionError(
            f"{node.operator} expects {[item.value for item in spec.arguments]}, "
            f"got {[item.value for item in actual]}"
        )
    if spec.axis is Axis.TIME:
        window = node.arguments[1]
        if not isinstance(window, LiteralNode) or window.value <= 0:
            raise ExpressionError(f"{node.operator} window must be a positive integer")
        if window.value > MAX_TIME_WINDOW:
            raise ExpressionError(
                f"{node.operator} window cannot exceed {MAX_TIME_WINDOW} observations"
            )
        if node.operator in {"ts_stddev", "ts_zscore"} and window.value < 2:
            raise ExpressionError(f"{node.operator} window must be at least 2")
    return spec.result


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ExpressionError("numeric literals must be finite")
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def canonical_expression(node: ExpressionNode) -> str:
    if isinstance(node, LiteralNode):
        return _decimal_text(node.value)
    if isinstance(node, (DataFieldNode, GroupFieldNode)):
        return node.name
    if isinstance(node, str):
        return node
    return f"{node.operator}({','.join(canonical_expression(item) for item in node.arguments)})"


def expression_hash(node: ExpressionNode) -> str:
    return sha256(canonical_expression(node).encode("utf-8")).hexdigest()


class PanelResolver(Protocol):
    def field(self, name: str) -> Panel: ...
    def group(self, name: str) -> Mapping[str, str]: ...


@dataclass(frozen=True, slots=True)
class RepositoryPanelResolver:
    """Resolve DSL fields exclusively through the validated repository bridge."""

    fields: RepositoryDataFields
    instrument_ids: tuple[str, ...]
    context: AsOfContext
    groups: Mapping[str, Mapping[str, str]]

    def field(self, name: str) -> PanelValue:
        require_as_of_context(self.context)
        if not self.instrument_ids:
            raise ExpressionError("repository panel requires instruments")
        observations = {
            instrument_id: self.fields.time_series_observations(
                name, instrument_id=instrument_id, context=self.context
            )
            for instrument_id in self.instrument_ids
        }
        periods = sorted({period for values in observations.values() for period, _ in values})
        if not periods:
            raise ExpressionError(f"datafield {name} has no observations")
        return {
            instrument_id: [dict(values).get(period) for period in periods]
            for instrument_id, values in observations.items()
        }

    def group(self, name: str) -> Mapping[str, str]:
        try:
            mapping = self.groups[name]
        except KeyError as exc:
            raise ExpressionError(f"unknown group datafield: {name}") from exc
        missing = set(self.instrument_ids) - set(mapping)
        if missing:
            raise ExpressionError(f"group datafield {name} misses instruments: {sorted(missing)}")
        return mapping


def _copy_panel(value: Panel) -> PanelValue:
    lengths = {len(series) for series in value.values()}
    if len(lengths) > 1:
        raise ExpressionError("panel series must have equal length")
    return {key: [None if item is None else float(item) for item in series] for key, series in value.items()}


def _cross_section(panel: PanelValue, function) -> PanelValue:
    if not panel:
        return {}
    length = len(next(iter(panel.values())))
    result = {key: [None] * length for key in panel}
    for index in range(length):
        values = {key: series[index] for key, series in panel.items() if series[index] is not None}
        transformed = function(values)
        for key, value in transformed.items():
            result[key][index] = value
    return result


def evaluate_expression(node: ExpressionNode, resolver: PanelResolver):
    if isinstance(node, LiteralNode):
        return int(node.value) if node.value == node.value.to_integral_value() else float(node.value)
    if isinstance(node, str):
        return _copy_panel(resolver.field(node))
    if isinstance(node, DataFieldNode):
        return _copy_panel(resolver.field(node.name))
    if isinstance(node, GroupFieldNode):
        return dict(resolver.group(node.name))

    spec = OPERATOR_REGISTRY.get(node.operator)
    if spec is None:
        raise ExpressionError(f"unknown operator: {node.operator}")
    arguments = [evaluate_expression(item, resolver) for item in node.arguments]
    function = getattr(ops, node.operator)
    if spec.axis is Axis.TIME:
        panel, window = arguments
        return {key: function(series, window) for key, series in panel.items()}
    if node.operator in {"group_neutralize", "group_rank"}:
        panel, groups = arguments
        return _cross_section(panel, lambda values: function(values, groups))
    if spec.axis is Axis.CROSS_SECTION:
        return _cross_section(arguments[0], function)
    panel = arguments[0]
    return {key: [None if item is None else function({key: item})[key] for item in series]
            for key, series in panel.items()}


@dataclass(frozen=True, slots=True)
class CompiledExpression:
    source: str
    root: ExpressionNode
    canonical: str
    expression_hash: str

    def evaluate(self, resolver: PanelResolver) -> PanelValue:
        return evaluate_expression(self.root, resolver)


def compile_expression(
    source: str,
    *,
    data_fields: set[str] | frozenset[str],
    group_fields: set[str] | frozenset[str] = frozenset(),
) -> CompiledExpression:
    parsed = parse_expression(source)
    root = _resolve_identifiers(parsed, data_fields=data_fields, group_fields=group_fields)
    if validate_expression(root, data_fields=data_fields, group_fields=group_fields) is not ValueType.PANEL:
        raise ExpressionError("top-level expression must produce a Panel")
    canonical = canonical_expression(root)
    return CompiledExpression(source, root, canonical, sha256(canonical.encode()).hexdigest())


def _resolve_identifiers(node, *, data_fields, group_fields):
    if isinstance(node, str):
        if node in data_fields:
            return DataFieldNode(node)
        if node in group_fields:
            return GroupFieldNode(node)
        raise ExpressionError(f"unknown datafield: {node}")
    if isinstance(node, CallNode):
        return CallNode(node.operator, tuple(
            _resolve_identifiers(item, data_fields=data_fields, group_fields=group_fields)
            for item in node.arguments
        ))
    return node
