"""Typed Fast Expression AST, validator, canonical identity, and evaluator.

The small Lark frontend is structurally inspired by marketneutral/alphatools'
Apache-2.0 expression grammar. Its Zipline runtime is intentionally not used.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
from importlib.resources import files
from math import isfinite
from types import MappingProxyType
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
    def group(self, name: str) -> Mapping[str, str | Sequence[str | None]]: ...


def _reference_period_key(period: str) -> datetime:
    quarter = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if quarter:
        return datetime(int(quarter.group(1)), (int(quarter.group(2)) - 1) * 3 + 1, 1)
    month = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if month:
        try:
            return datetime(int(month.group(1)), int(month.group(2)), 1)
        except ValueError as exc:
            raise ExpressionError(f"invalid reference period: {period}") from exc
    try:
        parsed = datetime.fromisoformat(period.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExpressionError(
            "reference periods must use ISO date/datetime, YYYY-MM, or YYYY-QN format"
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


@dataclass(frozen=True, slots=True)
class RepositoryPanelResolver:
    """Resolve DSL fields exclusively through the validated repository bridge."""

    fields: RepositoryDataFields
    instrument_ids: tuple[str, ...]
    context: AsOfContext
    groups: Mapping[str, Mapping[str, Mapping[str, str]]]
    reference_periods: tuple[str, ...]
    universe_membership: Mapping[str, frozenset[str]]
    dataset_manifest_ids: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not self.reference_periods or len(set(self.reference_periods)) != len(self.reference_periods):
            raise ExpressionError("reference_periods must be non-empty and unique")
        period_keys = tuple(_reference_period_key(period) for period in self.reference_periods)
        if any(current >= following for current, following in zip(period_keys, period_keys[1:])):
            raise ExpressionError("reference_periods must be chronological oldest-to-newest")
        missing = set(self.reference_periods) - set(self.universe_membership)
        if missing:
            raise ExpressionError(f"universe membership misses periods: {sorted(missing)}")
        historical_members = set().union(
            *(self.universe_membership[period] for period in self.reference_periods)
        )
        missing_instruments = historical_members - set(self.instrument_ids)
        if missing_instruments:
            raise ExpressionError(
                "instrument_ids miss historical universe members: "
                f"{sorted(missing_instruments)}"
            )
        frozen_membership = MappingProxyType({
            period: frozenset(self.universe_membership[period])
            for period in self.reference_periods
        })
        frozen_groups = MappingProxyType({
            name: MappingProxyType({
                period: MappingProxyType(dict(classifications))
                for period, classifications in history.items()
            })
            for name, history in self.groups.items()
        })
        object.__setattr__(self, "universe_membership", frozen_membership)
        object.__setattr__(self, "groups", frozen_groups)
        object.__setattr__(
            self,
            "dataset_manifest_ids",
            self.fields.dataset_manifest_ids(self.context),
        )

    def field(self, name: str) -> PanelValue:
        require_as_of_context(self.context)
        if not self.instrument_ids:
            raise ExpressionError("repository panel requires instruments")
        observations = {
            instrument_id: self.fields.time_series_observations(
                name,
                instrument_id=instrument_id,
                context=self.context,
                dataset_manifest_id=(
                    self.dataset_manifest_ids[0]
                    if len(self.dataset_manifest_ids) == 1
                    else None
                ),
            )
            for instrument_id in self.instrument_ids
        }
        indexed = {instrument_id: dict(values) for instrument_id, values in observations.items()}
        return {
            instrument_id: [
                indexed[instrument_id].get(period)
                if instrument_id in self.universe_membership[period]
                else None
                for period in self.reference_periods
            ]
            for instrument_id in self.instrument_ids
        }

    def group(self, name: str) -> Mapping[str, Sequence[str | None]]:
        try:
            history = self.groups[name]
        except KeyError as exc:
            raise ExpressionError(f"unknown group datafield: {name}") from exc
        missing = set(self.reference_periods) - set(history)
        if missing:
            raise ExpressionError(f"group datafield {name} misses periods: {sorted(missing)}")
        return {
            instrument_id: [history[period].get(instrument_id) for period in self.reference_periods]
            for instrument_id in self.instrument_ids
        }

    def members_at(self, index: int) -> frozenset[str]:
        return self.universe_membership[self.reference_periods[index]]


def _copy_panel(value: Panel) -> PanelValue:
    lengths = {len(series) for series in value.values()}
    if len(lengths) > 1:
        raise ExpressionError("panel series must have equal length")
    return {key: [None if item is None else float(item) for item in series] for key, series in value.items()}


def _require_finite_panel(panel: PanelValue, operator: str) -> PanelValue:
    for instrument_id, series in panel.items():
        for index, value in enumerate(series):
            if value is not None and not isfinite(value):
                raise ExpressionError(
                    f"{operator} produced a non-finite value for "
                    f"{instrument_id} at index {index}"
                )
    return panel


def _cross_section(panel: PanelValue, function, resolver: PanelResolver) -> PanelValue:
    if not panel:
        return {}
    length = len(next(iter(panel.values())))
    result = {key: [None] * length for key in panel}
    for index in range(length):
        membership_reader = getattr(resolver, "members_at", None)
        members = set(panel) if membership_reader is None else set(membership_reader(index))
        values = {
            key: series[index]
            for key, series in panel.items()
            if key in members and series[index] is not None
        }
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
        return _require_finite_panel(
            {key: function(series, window) for key, series in panel.items()},
            node.operator,
        )
    if node.operator in {"group_neutralize", "group_rank"}:
        panel, groups = arguments
        first_group = next(iter(groups.values()), None)
        if isinstance(first_group, str) or first_group is None:
            return _require_finite_panel(
                _cross_section(panel, lambda values: function(values, groups), resolver),
                node.operator,
            )
        length = len(next(iter(panel.values()))) if panel else 0
        result = {key: [None] * length for key in panel}
        for index in range(length):
            membership_reader = getattr(resolver, "members_at", None)
            members = set(panel) if membership_reader is None else set(membership_reader(index))
            values = {
                key: series[index]
                for key, series in panel.items()
                if key in members and series[index] is not None
            }
            point_groups = {
                key: series[index]
                for key, series in groups.items()
                if key in members and series[index] is not None
            }
            transformed = function(values, point_groups)
            for key, value in transformed.items():
                result[key][index] = value
        return _require_finite_panel(result, node.operator)
    if spec.axis is Axis.CROSS_SECTION:
        return _require_finite_panel(
            _cross_section(arguments[0], function, resolver),
            node.operator,
        )
    panel = arguments[0]
    return _require_finite_panel(
        {
            key: [None if item is None else function({key: item})[key] for item in series]
            for key, series in panel.items()
        },
        node.operator,
    )


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
