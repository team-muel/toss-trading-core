"""Multi-period portfolio-transition plans; this module never creates orders."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.domain.horizon import SignalValidity

from .costs import transaction_cost
from .models import PortfolioTarget


_HASH = re.compile(r"[0-9a-f]{64}")


def _text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataQualityError(reason)
    return value


def _utc(value: object, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DataQualityError(reason)
    return value.astimezone(timezone.utc)


def _decimal(value: object, reason: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or (nonnegative and value < 0):
        raise DataQualityError(reason)
    return value


def _target(target: object, reason: str) -> PortfolioTarget:
    if not isinstance(target, PortfolioTarget):
        raise DataQualityError(reason)
    if (any(not isinstance(item, str) or not item.strip() for item in target.instruments) or
            not isinstance(target.stage, str) or not target.stage.strip() or
            any(weight < 0 for weight in target.weights) or sum(target.weights) != Decimal(1)):
        raise DataQualityError(reason)
    return target


def _decimal_map(values: object, instruments: tuple[str, ...], reason: str, *,
                 exact: bool, nonnegative: bool = False, unit_interval: bool = False) -> Mapping[str, Decimal]:
    if not isinstance(values, Mapping):
        raise DataQualityError(reason)
    result: dict[str, Decimal] = {}
    for key, value in values.items():
        result[_text(key, reason)] = _decimal(value, reason, nonnegative=nonnegative)
    keys = set(result)
    allowed = set(instruments)
    if (exact and keys != allowed) or (not exact and not keys <= allowed) or (
            unit_interval and any(value > 1 for value in result.values())):
        raise DataQualityError(reason)
    return MappingProxyType(dict(sorted(result.items())))


def _target_payload(value: PortfolioTarget) -> dict[str, object]:
    return {"instruments": list(value.instruments), "weights": [str(item) for item in value.weights],
            "stage": value.stage, "reason_codes": list(value.reason_codes)}


def _weight_at(validity: SignalValidity, produced_at: datetime, at: datetime) -> Decimal:
    # SignalValidity treats its exact endpoint as valid for a STEP profile. A transition cannot
    # schedule a step at that instant because there is no remaining validity window to execute it.
    if at >= validity.valid_until:
        return Decimal(0)
    return validity.effective_weight(produced_at=produced_at, evaluated_at=at)


class TransitionMode(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    STAGED = "STAGED"
    DEFER = "DEFER"


class TransitionAction(StrEnum):
    SELL = "SELL"
    BUY = "BUY"


class TransitionPrerequisite(StrEnum):
    NO_OPEN_ORDER_EXPOSURE = "NO_OPEN_ORDER_EXPOSURE"
    LIQUIDITY_WINDOW_OPEN = "LIQUIDITY_WINDOW_OPEN"
    EVENT_WINDOW_CLEAR = "EVENT_WINDOW_CLEAR"
    AVAILABLE_CASH_CONFIRMED = "AVAILABLE_CASH_CONFIRMED"
    SELL_FILL_CONFIRMED = "SELL_FILL_CONFIRMED"
    SETTLED_CASH_CONFIRMED = "SETTLED_CASH_CONFIRMED"
    PRIOR_STAGE_RECONCILED = "PRIOR_STAGE_RECONCILED"


@dataclass(frozen=True, slots=True)
class TransitionStep:
    sequence: int
    stage: int
    action: TransitionAction
    instrument: str
    weight_delta: Decimal
    depends_on_step_ids: tuple[str, ...]
    prerequisites: tuple[TransitionPrerequisite, ...]
    not_before_at: datetime
    expires_at: datetime
    expected_benefit: Decimal
    expected_cost: Decimal
    requires_reassessment: bool

    def __post_init__(self) -> None:
        if (type(self.sequence) is not int or self.sequence < 1 or type(self.stage) is not int or
                self.stage < 1 or not isinstance(self.action, TransitionAction) or
                not isinstance(self.requires_reassessment, bool)):
            raise InvariantViolation("TRANSITION_STEP_INVALID")
        _text(self.instrument, "TRANSITION_STEP_INVALID")
        _decimal(self.weight_delta, "TRANSITION_STEP_INVALID")
        if ((self.action is TransitionAction.SELL and self.weight_delta >= 0) or
                (self.action is TransitionAction.BUY and self.weight_delta <= 0)):
            raise InvariantViolation("TRANSITION_STEP_DIRECTION_INVALID")
        dependencies = tuple(sorted(_text(item, "TRANSITION_STEP_INVALID") for item in self.depends_on_step_ids))
        if len(dependencies) != len(set(dependencies)):
            raise InvariantViolation("TRANSITION_STEP_INVALID")
        if any(not isinstance(item, TransitionPrerequisite) for item in self.prerequisites):
            raise InvariantViolation("TRANSITION_STEP_INVALID")
        prerequisites = tuple(sorted(self.prerequisites, key=lambda item: item.value))
        if not prerequisites or len(prerequisites) != len(set(prerequisites)):
            raise InvariantViolation("TRANSITION_STEP_INVALID")
        not_before = _utc(self.not_before_at, "TRANSITION_STEP_TIME_INVALID")
        expires = _utc(self.expires_at, "TRANSITION_STEP_TIME_INVALID")
        if not_before >= expires:
            raise InvariantViolation("TRANSITION_STEP_WINDOW_INVALID")
        benefit = _decimal(self.expected_benefit, "TRANSITION_STEP_ECONOMICS_INVALID")
        cost = _decimal(self.expected_cost, "TRANSITION_STEP_ECONOMICS_INVALID", nonnegative=True)
        object.__setattr__(self, "depends_on_step_ids", dependencies)
        object.__setattr__(self, "prerequisites", prerequisites)
        object.__setattr__(self, "not_before_at", not_before)
        object.__setattr__(self, "expires_at", expires)
        object.__setattr__(self, "expected_benefit", benefit)
        object.__setattr__(self, "expected_cost", cost)

    @property
    def step_id(self) -> str:
        return f"{self.stage}:{self.action.value}:{self.instrument}"

    def payload(self) -> dict[str, object]:
        return {"step_id": self.step_id, "sequence": self.sequence, "stage": self.stage,
                "action": self.action.value, "instrument": self.instrument,
                "weight_delta": str(self.weight_delta), "depends_on_step_ids": list(self.depends_on_step_ids),
                "prerequisites": [item.value for item in self.prerequisites],
                "not_before_at": self.not_before_at.isoformat(), "expires_at": self.expires_at.isoformat(),
                "expected_benefit": str(self.expected_benefit), "expected_cost": str(self.expected_cost),
                "requires_reassessment": self.requires_reassessment}


@dataclass(frozen=True, slots=True)
class TransitionPlanningInput:
    current: PortfolioTarget
    executable_target: PortfolioTarget
    cash_instrument: str
    forecast_values: Mapping[str, Decimal]
    produced_at: datetime
    evaluated_at: datetime
    signal_validity: SignalValidity
    next_rebalance_at: datetime
    liquidity_horizon_at: datetime
    stage_interval: timedelta
    stage_count: int
    settlement_available_at: datetime | None
    upcoming_event_times: tuple[datetime, ...]
    open_order_exposure: Mapping[str, Decimal]
    available_cash_weight: Decimal
    linear_costs: Mapping[str, Decimal]
    impact_costs: Mapping[str, Decimal]
    tax_costs: Mapping[str, Decimal]
    liquidity_capacities: Mapping[str, Decimal]
    cost_curve_key: str
    tax_policy_key: str
    liquidity_policy_key: str

    def __post_init__(self) -> None:
        current = _target(self.current, "TRANSITION_CURRENT_PORTFOLIO_INVALID")
        target = _target(self.executable_target, "TRANSITION_TARGET_PORTFOLIO_INVALID")
        if current.instruments != target.instruments:
            raise DataQualityError("TRANSITION_TARGET_UNIVERSE_MISMATCH")
        cash = _text(self.cash_instrument, "TRANSITION_CASH_INSTRUMENT_INVALID")
        if cash not in current.instruments:
            raise DataQualityError("TRANSITION_CASH_INSTRUMENT_INVALID")
        produced = _utc(self.produced_at, "TRANSITION_TIME_INVALID")
        evaluated = _utc(self.evaluated_at, "TRANSITION_TIME_INVALID")
        rebalance = _utc(self.next_rebalance_at, "TRANSITION_TIME_INVALID")
        liquidity_horizon = _utc(self.liquidity_horizon_at, "TRANSITION_TIME_INVALID")
        if rebalance <= evaluated or liquidity_horizon <= evaluated:
            raise DataQualityError("TRANSITION_TIME_WINDOW_INVALID")
        if (not isinstance(self.signal_validity, SignalValidity) or
                not isinstance(self.stage_interval, timedelta) or self.stage_interval.total_seconds() <= 0 or
                type(self.stage_count) is not int or not 2 <= self.stage_count <= 8):
            raise DataQualityError("TRANSITION_SCHEDULE_INVALID")
        settlement = None if self.settlement_available_at is None else _utc(
            self.settlement_available_at, "TRANSITION_SETTLEMENT_TIME_INVALID")
        if settlement is not None and settlement < evaluated:
            raise DataQualityError("TRANSITION_SETTLEMENT_TIME_INVALID")
        events = tuple(_utc(item, "TRANSITION_EVENT_TIME_INVALID") for item in self.upcoming_event_times)
        if events != tuple(sorted(events)) or len(events) != len(set(events)) or any(item <= evaluated for item in events):
            raise DataQualityError("TRANSITION_EVENT_TIME_INVALID")
        instruments = current.instruments
        object.__setattr__(self, "forecast_values", _decimal_map(
            self.forecast_values, instruments, "TRANSITION_FORECAST_INVALID", exact=True))
        object.__setattr__(self, "open_order_exposure", _decimal_map(
            self.open_order_exposure, instruments, "TRANSITION_OPEN_ORDER_EXPOSURE_INVALID",
            exact=False, nonnegative=True, unit_interval=True))
        object.__setattr__(self, "linear_costs", _decimal_map(
            self.linear_costs, instruments, "TRANSITION_COST_CURVE_INVALID", exact=True, nonnegative=True))
        object.__setattr__(self, "impact_costs", _decimal_map(
            self.impact_costs, instruments, "TRANSITION_COST_CURVE_INVALID", exact=True, nonnegative=True))
        object.__setattr__(self, "tax_costs", _decimal_map(
            self.tax_costs, instruments, "TRANSITION_TAX_COST_INVALID", exact=True, nonnegative=True))
        object.__setattr__(self, "liquidity_capacities", _decimal_map(
            self.liquidity_capacities, instruments, "TRANSITION_LIQUIDITY_INVALID", exact=True,
            nonnegative=True, unit_interval=True))
        available = _decimal(self.available_cash_weight, "TRANSITION_CASH_CONSTRAINT_INVALID", nonnegative=True)
        if available > 1:
            raise DataQualityError("TRANSITION_CASH_CONSTRAINT_INVALID")
        for value in (self.cost_curve_key, self.tax_policy_key, self.liquidity_policy_key):
            _text(value, "TRANSITION_POLICY_KEY_INVALID")
        object.__setattr__(self, "produced_at", produced)
        object.__setattr__(self, "evaluated_at", evaluated)
        object.__setattr__(self, "next_rebalance_at", rebalance)
        object.__setattr__(self, "liquidity_horizon_at", liquidity_horizon)
        object.__setattr__(self, "settlement_available_at", settlement)
        object.__setattr__(self, "upcoming_event_times", events)
        object.__setattr__(self, "available_cash_weight", available)

    def payload(self) -> dict[str, object]:
        values = lambda source: {key: str(value) for key, value in source.items()}
        return {"current": _target_payload(self.current), "executable_target": _target_payload(self.executable_target),
                "cash_instrument": self.cash_instrument, "forecast_values": values(self.forecast_values),
                "produced_at": self.produced_at.isoformat(), "evaluated_at": self.evaluated_at.isoformat(),
                "signal_validity": self.signal_validity.payload(),
                "next_rebalance_at": self.next_rebalance_at.isoformat(),
                "liquidity_horizon_at": self.liquidity_horizon_at.isoformat(),
                "stage_interval_seconds": int(self.stage_interval.total_seconds()), "stage_count": self.stage_count,
                "settlement_available_at": None if self.settlement_available_at is None else self.settlement_available_at.isoformat(),
                "upcoming_event_times": [item.isoformat() for item in self.upcoming_event_times],
                "open_order_exposure": values(self.open_order_exposure),
                "available_cash_weight": str(self.available_cash_weight), "linear_costs": values(self.linear_costs),
                "impact_costs": values(self.impact_costs), "tax_costs": values(self.tax_costs),
                "liquidity_capacities": values(self.liquidity_capacities), "cost_curve_key": self.cost_curve_key,
                "tax_policy_key": self.tax_policy_key, "liquidity_policy_key": self.liquidity_policy_key}

    @property
    def input_hash(self) -> str:
        return digest(canonical(self.payload()))


@dataclass(frozen=True, slots=True)
class TransitionPlan:
    input_hash: str
    planning_input: TransitionPlanningInput
    current: PortfolioTarget
    executable_target: PortfolioTarget
    mode: TransitionMode
    steps: tuple[TransitionStep, ...]
    immediate_expected_utility: Decimal
    staged_expected_utility: Decimal
    reason_codes: tuple[str, ...]
    created_at: datetime
    reassess_at: datetime

    def __post_init__(self) -> None:
        if (not isinstance(self.input_hash, str) or _HASH.fullmatch(self.input_hash) is None or
                not isinstance(self.planning_input, TransitionPlanningInput) or
                self.input_hash != self.planning_input.input_hash or not isinstance(self.mode, TransitionMode)):
            raise InvariantViolation("TRANSITION_PLAN_INVALID")
        _target(self.current, "TRANSITION_PLAN_INVALID")
        _target(self.executable_target, "TRANSITION_PLAN_INVALID")
        immediate = _decimal(self.immediate_expected_utility, "TRANSITION_PLAN_ECONOMICS_INVALID")
        staged = _decimal(self.staged_expected_utility, "TRANSITION_PLAN_ECONOMICS_INVALID")
        reasons = tuple(sorted(_text(item, "TRANSITION_PLAN_INVALID") for item in self.reason_codes))
        if len(reasons) != len(set(reasons)):
            raise InvariantViolation("TRANSITION_PLAN_INVALID")
        created = _utc(self.created_at, "TRANSITION_PLAN_TIME_INVALID")
        reassess = _utc(self.reassess_at, "TRANSITION_PLAN_TIME_INVALID")
        if reassess < created:
            raise InvariantViolation("TRANSITION_PLAN_TIME_INVALID")
        if self.mode is TransitionMode.DEFER:
            if self.steps or not reasons:
                raise InvariantViolation("TRANSITION_DEFER_PLAN_INVALID")
        elif not self.steps:
            raise InvariantViolation("TRANSITION_EXECUTION_PLAN_EMPTY")
        known: set[str] = set()
        for sequence, step in enumerate(self.steps, 1):
            if not isinstance(step, TransitionStep) or step.sequence != sequence or step.step_id in known or not set(step.depends_on_step_ids) <= known:
                raise InvariantViolation("TRANSITION_STEP_DEPENDENCY_INVALID")
            known.add(step.step_id)
        object.__setattr__(self, "immediate_expected_utility", immediate)
        object.__setattr__(self, "staged_expected_utility", staged)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "reassess_at", reassess)

    @property
    def selected_expected_utility(self) -> Decimal:
        if self.mode is TransitionMode.IMMEDIATE:
            return self.immediate_expected_utility
        if self.mode is TransitionMode.STAGED:
            return self.staged_expected_utility
        return Decimal(0)

    def payload(self) -> dict[str, object]:
        return {"input_hash": self.input_hash, "planning_input": self.planning_input.payload(),
                "current": _target_payload(self.current),
                "executable_target": _target_payload(self.executable_target), "mode": self.mode.value,
                "steps": [item.payload() for item in self.steps],
                "immediate_expected_utility": str(self.immediate_expected_utility),
                "staged_expected_utility": str(self.staged_expected_utility),
                "reason_codes": list(self.reason_codes), "created_at": self.created_at.isoformat(),
                "reassess_at": self.reassess_at.isoformat(), "plan_hash": self.plan_hash}

    @property
    def plan_hash(self) -> str:
        body = self.payload_without_hash()
        return digest(canonical(body))

    def payload_without_hash(self) -> dict[str, object]:
        return {"input_hash": self.input_hash, "planning_input": self.planning_input.payload(),
                "current": _target_payload(self.current),
                "executable_target": _target_payload(self.executable_target), "mode": self.mode.value,
                "steps": [item.payload() for item in self.steps],
                "immediate_expected_utility": str(self.immediate_expected_utility),
                "staged_expected_utility": str(self.staged_expected_utility),
                "reason_codes": list(self.reason_codes), "created_at": self.created_at.isoformat(),
                "reassess_at": self.reassess_at.isoformat()}


class TransitionPlanner:
    """Compares immediate and staged target transitions without creating immediate orders."""

    def plan(self, inputs: TransitionPlanningInput) -> TransitionPlan:
        if not isinstance(inputs, TransitionPlanningInput):
            raise DataQualityError("TRANSITION_INPUT_INVALID")
        expiry = min(inputs.signal_validity.valid_until, inputs.next_rebalance_at, inputs.liquidity_horizon_at)
        if inputs.evaluated_at >= inputs.signal_validity.valid_until:
            return self._defer(inputs, ("FORECAST_EXPIRED",), expiry)
        forecast_weight = _weight_at(inputs.signal_validity, inputs.produced_at, inputs.evaluated_at)
        if forecast_weight <= 0:
            return self._defer(inputs, ("FORECAST_DECAYED",), expiry)
        if event := next((item for item in inputs.upcoming_event_times if item <= inputs.next_rebalance_at), None):
            return self._defer(inputs, ("UPCOMING_EVENT_DEFER",), event)
        deltas = tuple(target - current for target, current in zip(
            inputs.executable_target.weights, inputs.current.weights, strict=True))
        changed = tuple(index for index, value in enumerate(deltas) if value != 0)
        noncash = tuple(index for index in changed if inputs.current.instruments[index] != inputs.cash_instrument)
        if not noncash:
            return self._defer(inputs, ("NO_TRANSITION_REQUIRED",), expiry)
        if any(inputs.open_order_exposure.get(inputs.current.instruments[index], Decimal(0)) > 0 for index in noncash):
            return self._defer(inputs, ("OPEN_ORDER_EXPOSURE",), expiry)
        sells = tuple(index for index in noncash if deltas[index] < 0)
        buys = tuple(index for index in noncash if deltas[index] > 0)
        if buys and sells and inputs.settlement_available_at is None:
            return self._defer(inputs, ("SETTLEMENT_TIMING_UNKNOWN",), expiry)
        if buys and sells and inputs.settlement_available_at >= expiry:
            return self._defer(inputs, ("SETTLEMENT_AFTER_TRANSITION_EXPIRY",), expiry)
        immediate_cost = self._cost(inputs, deltas)
        staged_delta = tuple(value / Decimal(inputs.stage_count) for value in deltas)
        staged_cost = sum((self._cost(inputs, staged_delta) for _ in range(inputs.stage_count)), Decimal(0))
        immediate_benefit = self._benefit(inputs, deltas, sells, buys, 1)
        immediate_utility = immediate_benefit - immediate_cost
        stage_times = tuple(inputs.evaluated_at + inputs.stage_interval * stage
                            for stage in range(inputs.stage_count))
        staged_benefit = self._benefit(inputs, deltas, sells, buys, inputs.stage_count)
        staged_utility = staged_benefit - staged_cost
        immediate_liquid = all(abs(deltas[index]) <= inputs.liquidity_capacities[inputs.current.instruments[index]] for index in noncash)
        staged_liquid = all(abs(staged_delta[index]) <= inputs.liquidity_capacities[inputs.current.instruments[index]] for index in noncash)
        immediate_window = self._window_is_open(inputs, 1, expiry, sells, buys)
        staged_window = self._window_is_open(inputs, inputs.stage_count, expiry, sells, buys)
        gross_buys = sum((deltas[index] for index in buys), Decimal(0))
        gross_sells = sum((-deltas[index] for index in sells), Decimal(0))
        base_cash = max(Decimal(0), gross_buys - gross_sells)
        immediate_cash = inputs.available_cash_weight >= base_cash + immediate_cost
        staged_cash = inputs.available_cash_weight >= base_cash + staged_cost
        immediate_allowed = immediate_liquid and immediate_window and immediate_cash and immediate_utility > 0
        staged_allowed = staged_liquid and staged_window and staged_cash and staged_utility > 0
        if not immediate_allowed and not staged_allowed:
            reasons: list[str] = []
            if not immediate_liquid and not staged_liquid:
                reasons.append("LIQUIDITY_CONSTRAINT")
            if not immediate_window and not staged_window:
                reasons.append("TRANSITION_EXPIRY_BEFORE_STAGE")
            if not immediate_cash and not staged_cash:
                reasons.append("CASH_CONSTRAINT")
            if immediate_utility <= 0 and staged_utility <= 0:
                reasons.append("ECONOMIC_UTILITY_NON_POSITIVE")
            return self._defer(inputs, tuple(reasons or ["TRANSITION_NOT_EXECUTABLE"]), expiry,
                               immediate_utility, staged_utility)
        mode = (TransitionMode.IMMEDIATE if immediate_allowed and (
            not staged_allowed or immediate_utility >= staged_utility) else TransitionMode.STAGED)
        stages = 1 if mode is TransitionMode.IMMEDIATE else inputs.stage_count
        steps = self._steps(inputs, deltas, sells, buys, stages, expiry)
        reason = "IMMEDIATE_UTILITY_HIGHER" if mode is TransitionMode.IMMEDIATE else "STAGED_UTILITY_HIGHER"
        return TransitionPlan(inputs.input_hash, inputs, inputs.current, inputs.executable_target, mode, steps,
                              immediate_utility, staged_utility, (reason,), inputs.evaluated_at,
                              min(expiry, stage_times[-1] if mode is TransitionMode.STAGED else expiry))

    @staticmethod
    def _cost(inputs: TransitionPlanningInput, deltas: tuple[Decimal, ...]) -> Decimal:
        linear = tuple(inputs.linear_costs[item] for item in inputs.current.instruments)
        impact = tuple(inputs.impact_costs[item] for item in inputs.current.instruments)
        tax = sum((inputs.tax_costs[item] * abs(delta) for item, delta in zip(inputs.current.instruments, deltas, strict=True)), Decimal(0))
        return transaction_cost(deltas, linear, impact) + tax

    @staticmethod
    def _window_is_open(inputs: TransitionPlanningInput, stages: int, expiry: datetime,
                        sells: tuple[int, ...], buys: tuple[int, ...]) -> bool:
        last_stage = inputs.evaluated_at + inputs.stage_interval * (stages - 1)
        if last_stage >= expiry:
            return False
        if sells and buys and (inputs.settlement_available_at is None or
                               last_stage + (inputs.settlement_available_at - inputs.evaluated_at) >= expiry):
            return False
        return True

    @staticmethod
    def _benefit(inputs: TransitionPlanningInput, deltas: tuple[Decimal, ...], sells: tuple[int, ...],
                 buys: tuple[int, ...], stages: int) -> Decimal:
        settlement_lag = (inputs.settlement_available_at - inputs.evaluated_at
                          if sells and buys else timedelta(0))
        benefit = Decimal(0)
        for stage in range(stages):
            scheduled_at = inputs.evaluated_at + inputs.stage_interval * stage
            for index, delta in enumerate(deltas):
                at = scheduled_at + settlement_lag if index in buys else scheduled_at
                benefit += (delta / Decimal(stages) * inputs.forecast_values[inputs.current.instruments[index]] *
                            _weight_at(inputs.signal_validity, inputs.produced_at, at))
        return benefit

    def _steps(self, inputs: TransitionPlanningInput, deltas: tuple[Decimal, ...], sells: tuple[int, ...],
               buys: tuple[int, ...], stages: int, expiry: datetime) -> tuple[TransitionStep, ...]:
        result: list[TransitionStep] = []
        previous_ids: tuple[str, ...] = ()
        for stage in range(1, stages + 1):
            scheduled_at = inputs.evaluated_at + inputs.stage_interval * (stage - 1)
            step_deltas = tuple(value / Decimal(stages) for value in deltas)
            baseline = (TransitionPrerequisite.NO_OPEN_ORDER_EXPOSURE,
                        TransitionPrerequisite.LIQUIDITY_WINDOW_OPEN,
                        TransitionPrerequisite.EVENT_WINDOW_CLEAR)
            if stage > 1:
                baseline += (TransitionPrerequisite.PRIOR_STAGE_RECONCILED,)
            current_sell_ids: list[str] = []
            for index in sells:
                step = self._step(inputs, len(result) + 1, stage, TransitionAction.SELL, index,
                                  step_deltas[index], previous_ids, baseline, scheduled_at, expiry,
                                  stage > 1)
                result.append(step)
                current_sell_ids.append(step.step_id)
            buy_prerequisites = baseline + ((TransitionPrerequisite.SELL_FILL_CONFIRMED,
                                               TransitionPrerequisite.SETTLED_CASH_CONFIRMED)
                                              if sells else (TransitionPrerequisite.AVAILABLE_CASH_CONFIRMED,))
            settlement_lag = (inputs.settlement_available_at - inputs.evaluated_at
                              if sells and buys else timedelta(0))
            buy_at = scheduled_at + settlement_lag
            for index in buys:
                step = self._step(inputs, len(result) + 1, stage, TransitionAction.BUY, index,
                                  step_deltas[index], previous_ids + tuple(current_sell_ids),
                                  buy_prerequisites, buy_at, expiry,
                                  stage > 1 or buy_at > inputs.evaluated_at)
                result.append(step)
            previous_ids = tuple(item.step_id for item in result if item.stage == stage)
        return tuple(result)

    def _step(self, inputs: TransitionPlanningInput, sequence: int, stage: int, action: TransitionAction,
              index: int, delta: Decimal, dependencies: tuple[str, ...],
              prerequisites: tuple[TransitionPrerequisite, ...], not_before: datetime, expiry: datetime,
              requires_reassessment: bool) -> TransitionStep:
        instrument = inputs.current.instruments[index]
        benefit = delta * inputs.forecast_values[instrument] * _weight_at(
            inputs.signal_validity, inputs.produced_at, not_before)
        cost = inputs.linear_costs[instrument] * abs(delta) + inputs.impact_costs[instrument] * delta * delta + inputs.tax_costs[instrument] * abs(delta)
        return TransitionStep(sequence, stage, action, instrument, delta, dependencies, prerequisites,
                              not_before, expiry, benefit, cost, requires_reassessment)

    @staticmethod
    def _defer(inputs: TransitionPlanningInput, reasons: tuple[str, ...], reassess_at: datetime,
               immediate: Decimal = Decimal(0), staged: Decimal = Decimal(0)) -> TransitionPlan:
        return TransitionPlan(inputs.input_hash, inputs, inputs.current, inputs.executable_target, TransitionMode.DEFER,
                              (), immediate, staged, reasons, inputs.evaluated_at, reassess_at)
