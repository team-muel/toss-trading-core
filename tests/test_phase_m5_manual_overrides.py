from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from asset_management.decisions import (DEFAULT_OVERRIDE_TTL, ManualOverride, ManualOverrideAction,
                                        ManualOverrideJournal)
from asset_management.domain.enums import DecisionAction
from asset_management.domain.errors import InvariantViolation, NoTrade


NOW = datetime(2026, 9, 7, 1, tzinfo=timezone.utc)


def override(**changes):
    values = {
        "decision_id": "risk-decision-1", "original_action": DecisionAction.ALLOW,
        "override_action": ManualOverrideAction.LIQUIDITY_RESERVE,
        "reason": "Client withdrawal requested", "requested_by": "client-17",
        "approved_by": "advisor-2", "created_at": NOW,
    }
    values.update(changes)
    return ManualOverride(**values)


def test_override_records_all_authority_fields_with_default_expiry_and_content_addressed_identity():
    event = override()
    assert event.expires_at == NOW + DEFAULT_OVERRIDE_TTL
    assert event.override_id.startswith("override-")
    assert event.payload() == {
        "event_type": "MANUAL_OVERRIDE", "event_version": 1, "override_id": event.override_id,
        "decision_id": "risk-decision-1", "original_action": "ALLOW",
        "override_action": "LIQUIDITY_RESERVE", "reason": "Client withdrawal requested",
        "requested_by": "client-17", "approved_by": "advisor-2",
        "created_at": NOW.isoformat(), "expires_at": (NOW + DEFAULT_OVERRIDE_TTL).isoformat(),
    }
    assert event.active_at(NOW) and not event.active_at(event.expires_at)


def test_journal_is_append_only_and_exact_replay_preserves_the_state_event(tmp_path):
    journal = ManualOverrideJournal(tmp_path / "manual-overrides.jsonl")
    event = override()
    journal.append(event)
    journal.append(event)
    assert journal.load() == (event,)
    with journal.path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event.payload(), separators=(",", ":")) + "\n")
    with pytest.raises(InvariantViolation, match="MANUAL_OVERRIDE_DUPLICATE_EVENT"):
        journal.load()


def test_resolve_exposes_one_active_intervention_without_changing_the_original_decision(tmp_path):
    journal = ManualOverrideJournal(tmp_path / "manual-overrides.jsonl")
    journal.append(override(override_action=ManualOverrideAction.EMERGENCY_LIQUIDATION,
                            reason="Emergency risk liquidation"))
    state = journal.resolve(decision_id="risk-decision-1", original_action=DecisionAction.ALLOW, at=NOW)
    assert state.original_action is DecisionAction.ALLOW
    assert state.effective_action is ManualOverrideAction.EMERGENCY_LIQUIDATION
    assert state.override_id is not None
    assert state.payload()["reason"] == "Emergency risk liquidation"


def test_expired_event_is_not_applied_and_multiple_active_events_fail_closed(tmp_path):
    journal = ManualOverrideJournal(tmp_path / "manual-overrides.jsonl")
    journal.append(override(expires_at=NOW + timedelta(minutes=5)))
    expired = journal.resolve(decision_id="risk-decision-1", original_action=DecisionAction.ALLOW,
                              at=NOW + timedelta(minutes=5))
    assert expired.effective_action is DecisionAction.ALLOW and expired.override_id is None
    journal.append(override(override_action=ManualOverrideAction.BLOCK, reason="Trade ban"))
    with pytest.raises(NoTrade, match="MANUAL_OVERRIDE_CONFLICT"):
        journal.resolve(decision_id="risk-decision-1", original_action=DecisionAction.ALLOW, at=NOW)


def test_mismatched_original_decision_and_tampered_event_fail_closed(tmp_path):
    journal = ManualOverrideJournal(tmp_path / "manual-overrides.jsonl")
    event = override()
    journal.append(event)
    with pytest.raises(NoTrade, match="MANUAL_OVERRIDE_ORIGINAL_ACTION_MISMATCH"):
        journal.resolve(decision_id=event.decision_id, original_action=DecisionAction.REDUCE, at=NOW)
    tampered = event.payload() | {"approved_by": "different-approver"}
    journal.path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(InvariantViolation, match="MANUAL_OVERRIDE_RECORD_INVALID"):
        journal.load()


@pytest.mark.parametrize("changes, reason", [
    ({"requested_by": ""}, "MANUAL_OVERRIDE_FIELD_INVALID"),
    ({"approved_by": ""}, "MANUAL_OVERRIDE_FIELD_INVALID"),
    ({"original_action": ManualOverrideAction.BLOCK}, "MANUAL_OVERRIDE_ORIGINAL_ACTION_INVALID"),
    ({"override_action": DecisionAction.ALLOW}, "MANUAL_OVERRIDE_ACTION_INVALID"),
    ({"override_action": ManualOverrideAction.BLOCK, "original_action": DecisionAction.BLOCK}, "MANUAL_OVERRIDE_NO_EFFECT"),
    ({"expires_at": NOW}, "MANUAL_OVERRIDE_EXPIRY_INVALID"),
])
def test_incomplete_or_ambiguous_manual_interventions_are_rejected(changes, reason):
    with pytest.raises(InvariantViolation, match=reason):
        override(**changes)


def test_schema_covers_serialized_event_contract():
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/manual_override.schema.json").read_text())
    assert set(schema["required"]) == set(override().payload())
