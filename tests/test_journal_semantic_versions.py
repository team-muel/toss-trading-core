from dataclasses import replace
import json
from pathlib import Path

import pytest

from asset_management.decisions.economic_journal import (
    EconomicDecisionJournal, EconomicDecisionRecord, ReturnMetricStatus, ReturnSemanticType,
)
from asset_management.domain.errors import InvariantViolation
from asset_management.risk.models import CurrencyBasis
from test_phase_m5_economic_decision_journal import (
    D, mandate_registry, metric, outcome, record, return_metrics,
)


def without_pricing():
    return tuple(metric(item.semantic_type) if item.semantic_type in {
        ReturnSemanticType.PRICING_BASELINE_RETURN, ReturnSemanticType.MODEL_RELATIVE_ALPHA
    } else item for item in return_metrics())


def test_non_equity_decision_and_outcome_replay_without_fabricated_pricing(tmp_path):
    item = record(return_metrics=without_pricing(), pricing_lineage_ids=(),
                  model_versions={"forecast": "cash-yield@1"})
    journal = EconomicDecisionJournal(tmp_path / "journal.jsonl", mandate_registry())
    journal.append(item)
    event = outcome(item)
    journal.append_outcome(event)
    restarted = EconomicDecisionJournal(journal.path, mandate_registry())
    assert restarted.records() == (item,)
    assert restarted.outcomes() == (event,)
    assert item.payload()["schema_version"] == "decision-economic-journal@2"
    assert event.payload()["schema_version"] == "decision-economic-journal@1"
    assert not item.pricing_lineage_ids


def test_v1_preserves_pre_remediation_identity_and_mixed_version_journal(tmp_path):
    old = record(schema_version="decision-economic-journal@1")
    # Captured using the original implementation at commit 5d5ffe3.
    assert old.content_hash == "ce0a8af9c989d77e96791629713d2ac0ee34c028a2e7076679d695841f3b138c"
    assert EconomicDecisionRecord.from_payload(old.payload()).payload() == old.payload()
    new = record()
    assert new.content_hash != old.content_hash
    journal = EconomicDecisionJournal(tmp_path / "journal.jsonl", mandate_registry())
    journal.append(old)
    original = journal.path.read_bytes()
    journal.append(new)
    assert journal.path.read_bytes().startswith(original)
    assert journal.records() == (old, new)
    with pytest.raises(InvariantViolation, match="DECISION_RETURN_REQUIRED_VALUE_MISSING"):
        record(schema_version="decision-economic-journal@1", return_metrics=without_pricing(),
               pricing_lineage_ids=())


def test_inapplicable_pricing_cannot_carry_alpha_or_pricing_lineage():
    metrics = tuple(metric(item.semantic_type, D('.04'))
                    if item.semantic_type is ReturnSemanticType.MODEL_RELATIVE_ALPHA else item
                    for item in without_pricing())
    with pytest.raises(InvariantViolation, match="DECISION_MODEL_ALPHA_WITHOUT_PRICING"):
        record(return_metrics=metrics, pricing_lineage_ids=())
    with pytest.raises(InvariantViolation, match="DECISION_INAPPLICABLE_PRICING_LINEAGE"):
        record(return_metrics=without_pricing())
    with pytest.raises(InvariantViolation, match="ECONOMIC_DECISION_LINEAGE_INVALID"):
        record(pricing_lineage_ids=())


@pytest.mark.parametrize("changes", [{"forecast_horizon": 63}, {"currency_basis": CurrencyBasis.LOCAL}])
def test_v2_rejects_mixed_context_without_reinterpreting_v1(changes):
    metrics = list(return_metrics())
    metrics[1] = replace(metrics[1], **changes)
    with pytest.raises(InvariantViolation, match="DECISION_RETURN_CONTEXT_MISMATCH"):
        record(return_metrics=tuple(metrics))
    old = record(return_metrics=tuple(metrics), schema_version="decision-economic-journal@1")
    assert EconomicDecisionRecord.from_payload(old.payload()) == old


def test_premature_pricing_and_unknown_or_relabelled_versions_fail():
    metrics = list(return_metrics())
    metrics[0] = replace(metrics[0], status=ReturnMetricStatus.NOT_MATURED, value=None)
    with pytest.raises(InvariantViolation, match="DECISION_PRICING_APPLICABILITY_INVALID"):
        record(return_metrics=tuple(metrics))
    with pytest.raises(InvariantViolation, match="SCHEMA_VERSION_UNSUPPORTED"):
        record(schema_version="decision-economic-journal@999")
    raw = record(schema_version="decision-economic-journal@1").payload()
    raw["schema_version"] = "decision-economic-journal@2"
    with pytest.raises(InvariantViolation, match="ECONOMIC_DECISION_RECORD_INVALID"):
        EconomicDecisionRecord.from_payload(raw)


def test_legacy_schema_is_preserved_and_v2_is_declared():
    root = Path(__file__).parents[1] / 'schemas'
    legacy = json.loads((root / 'decision_economic_journal.v1.schema.json').read_text())
    current = json.loads((root / 'decision_economic_journal.schema.json').read_text())
    assert legacy['properties']['schema_version']['const'] == 'decision-economic-journal@1'
    assert current['properties']['schema_version']['enum'] == [
        'decision-economic-journal@1', 'decision-economic-journal@2']
    assert set(current['required']) == set(record().payload())
