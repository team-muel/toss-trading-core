"""Append-only, expiring manual-intervention events for decision governance."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import json
from pathlib import Path
import re

from asset_management.data.immutable import canonical, digest
from asset_management.domain.enums import DecisionAction
from asset_management.domain.errors import InvariantViolation, NoTrade


DEFAULT_OVERRIDE_TTL = timedelta(days=1)
_HASH = re.compile(r"[0-9a-f]{64}")


def _text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvariantViolation(reason)
    return value


def _utc(value: object, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


class ManualOverrideAction(StrEnum):
    """Restrictions and interventions that cannot grant a new trading authorization."""

    BLOCK = "BLOCK"
    DEFER = "DEFER"
    LIQUIDITY_RESERVE = "LIQUIDITY_RESERVE"
    EMERGENCY_LIQUIDATION = "EMERGENCY_LIQUIDATION"


@dataclass(frozen=True, slots=True)
class ManualOverride:
    """An immutable state event, bound to one pre-existing decision and time window."""

    decision_id: str
    original_action: DecisionAction
    override_action: ManualOverrideAction
    reason: str
    requested_by: str
    approved_by: str
    created_at: datetime
    expires_at: datetime | None = None
    override_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.decision_id, self.reason, self.requested_by, self.approved_by):
            _text(value, "MANUAL_OVERRIDE_FIELD_INVALID")
        if not isinstance(self.original_action, DecisionAction):
            raise InvariantViolation("MANUAL_OVERRIDE_ORIGINAL_ACTION_INVALID")
        if not isinstance(self.override_action, ManualOverrideAction):
            raise InvariantViolation("MANUAL_OVERRIDE_ACTION_INVALID")
        if self.original_action.value == self.override_action.value:
            raise InvariantViolation("MANUAL_OVERRIDE_NO_EFFECT")
        created = _utc(self.created_at, "MANUAL_OVERRIDE_TIME_INVALID")
        expires = created + DEFAULT_OVERRIDE_TTL if self.expires_at is None else _utc(
            self.expires_at, "MANUAL_OVERRIDE_TIME_INVALID")
        if expires <= created:
            raise InvariantViolation("MANUAL_OVERRIDE_EXPIRY_INVALID")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)
        calculated = f"override-{digest(canonical(self._body()))}"
        if self.override_id is not None and (
                not isinstance(self.override_id, str) or self.override_id != calculated or
                _HASH.fullmatch(self.override_id.removeprefix("override-")) is None):
            raise InvariantViolation("MANUAL_OVERRIDE_ID_INVALID")
        object.__setattr__(self, "override_id", calculated)

    def _body(self) -> dict[str, object]:
        return {"decision_id": self.decision_id, "original_action": self.original_action.value,
                "override_action": self.override_action.value, "reason": self.reason,
                "requested_by": self.requested_by, "approved_by": self.approved_by,
                "created_at": self.created_at.isoformat(), "expires_at": self.expires_at.isoformat()}

    def payload(self) -> dict[str, object]:
        return {"event_type": "MANUAL_OVERRIDE", "event_version": 1,
                "override_id": self.override_id} | self._body()

    def active_at(self, at: datetime) -> bool:
        instant = _utc(at, "MANUAL_OVERRIDE_TIME_INVALID")
        return self.created_at <= instant < self.expires_at


@dataclass(frozen=True, slots=True)
class ManualInterventionState:
    """The one applicable manual state event, resolved without modifying its decision."""

    decision_id: str
    original_action: DecisionAction
    effective_action: DecisionAction | ManualOverrideAction
    override_id: str | None
    reason: str | None
    evaluated_at: datetime

    def __post_init__(self) -> None:
        _text(self.decision_id, "MANUAL_INTERVENTION_STATE_INVALID")
        if (not isinstance(self.original_action, DecisionAction) or
                not isinstance(self.effective_action, (DecisionAction, ManualOverrideAction))):
            raise InvariantViolation("MANUAL_INTERVENTION_STATE_INVALID")
        if self.override_id is None:
            if self.reason is not None or self.effective_action is not self.original_action:
                raise InvariantViolation("MANUAL_INTERVENTION_STATE_INVALID")
        elif (not isinstance(self.override_id, str) or not self.override_id.startswith("override-") or
              _HASH.fullmatch(self.override_id.removeprefix("override-")) is None):
            raise InvariantViolation("MANUAL_INTERVENTION_STATE_INVALID")
        if self.reason is not None:
            _text(self.reason, "MANUAL_INTERVENTION_STATE_INVALID")
        object.__setattr__(self, "evaluated_at", _utc(
            self.evaluated_at, "MANUAL_INTERVENTION_STATE_INVALID"))

    def payload(self) -> dict[str, object]:
        return {"decision_id": self.decision_id, "original_action": self.original_action.value,
                "effective_action": self.effective_action.value, "override_id": self.override_id,
                "reason": self.reason, "evaluated_at": self.evaluated_at.isoformat()}


class ManualOverrideJournal:
    """Append-only event journal; ambiguity or mismatched replay state fails closed."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: ManualOverride) -> None:
        if not isinstance(event, ManualOverride):
            raise InvariantViolation("MANUAL_OVERRIDE_EVENT_INVALID")
        existing = {item.override_id: item for item in self.load()}
        previous = existing.get(event.override_id)
        if previous is not None:
            if previous != event:
                raise InvariantViolation("MANUAL_OVERRIDE_ID_CONFLICT")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")

    def load(self) -> tuple[ManualOverride, ...]:
        if not self.path.exists():
            return ()
        events: list[ManualOverride] = []
        seen: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            event = self._parse(line)
            if event.override_id in seen:
                raise InvariantViolation("MANUAL_OVERRIDE_DUPLICATE_EVENT")
            seen.add(event.override_id)
            events.append(event)
        return tuple(events)

    def resolve(self, *, decision_id: str, original_action: DecisionAction,
                at: datetime) -> ManualInterventionState:
        identifier = _text(decision_id, "MANUAL_OVERRIDE_DECISION_INVALID")
        if not isinstance(original_action, DecisionAction):
            raise InvariantViolation("MANUAL_OVERRIDE_ORIGINAL_ACTION_INVALID")
        instant = _utc(at, "MANUAL_OVERRIDE_TIME_INVALID")
        active = tuple(item for item in self.load()
                       if item.decision_id == identifier and item.active_at(instant))
        if len(active) > 1:
            raise NoTrade("MANUAL_OVERRIDE_CONFLICT")
        if not active:
            return ManualInterventionState(identifier, original_action, original_action, None, None, instant)
        event = active[0]
        if event.original_action is not original_action:
            raise NoTrade("MANUAL_OVERRIDE_ORIGINAL_ACTION_MISMATCH")
        return ManualInterventionState(identifier, original_action, event.override_action,
                                       event.override_id, event.reason, instant)

    @staticmethod
    def _parse(line: str) -> ManualOverride:
        try:
            raw = json.loads(line)
            expected = {"event_type", "event_version", "override_id", "decision_id", "original_action",
                        "override_action", "reason", "requested_by", "approved_by", "created_at", "expires_at"}
            if (not isinstance(raw, dict) or set(raw) != expected or raw["event_type"] != "MANUAL_OVERRIDE" or
                    raw["event_version"] != 1):
                raise ValueError
            return ManualOverride(
                decision_id=raw["decision_id"], original_action=DecisionAction(raw["original_action"]),
                override_action=ManualOverrideAction(raw["override_action"]), reason=raw["reason"],
                requested_by=raw["requested_by"], approved_by=raw["approved_by"],
                created_at=datetime.fromisoformat(raw["created_at"]),
                expires_at=datetime.fromisoformat(raw["expires_at"]), override_id=raw["override_id"],
            )
        except (KeyError, TypeError, ValueError, InvariantViolation) as error:
            raise InvariantViolation("MANUAL_OVERRIDE_RECORD_INVALID") from error
