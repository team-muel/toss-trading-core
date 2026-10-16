from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.states import (
    RegimeModelSpec,
    RegimeOutputSemantics,
    RegimeSnapshot,
    StateType,
)


NOW = datetime(2026, 9, 20, 7, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(minutes=1)


def ident(*parts: str) -> str:
    return sha256(":".join(parts).encode("utf-8")).hexdigest()


def spec() -> RegimeModelSpec:
    return RegimeModelSpec(
        model_id="market-regime",
        version="1",
        input_state_type=StateType.MARKET,
        input_component_ids=("growth", "credit", "volatility"),
        latent_state_ids=("state-0", "state-1", "state-2"),
        parameter_set_id="market-regime@1",
        purpose="Represent probabilistic market-state inference without trading authority.",
    )


def snapshot(semantics: RegimeOutputSemantics = RegimeOutputSemantics.FILTERED_CAUSAL):
    return RegimeSnapshot.create(
        source_state_id=ident("state"),
        spec=spec(),
        as_of=NOW,
        information_cutoff=CUTOFF,
        output_semantics=semantics,
        state_probabilities={
            "state-0": Decimal("0.50"),
            "state-1": Decimal("0.30"),
            "state-2": Decimal("0.20"),
        },
        entropy=Decimal("1.029653"),
        confidence=Decimal("0.70"),
        input_evidence_ids=(ident("evidence"),),
        code_revision="git:abcdef0",
    )


def test_regime_contract_is_probabilistic_and_deterministic():
    first = snapshot()
    second = snapshot()

    assert first == second
    assert first.regime_id == second.regime_id
    assert first.is_causal
    assert [item.state_id for item in first.state_probabilities] == [
        "state-0", "state-1", "state-2"
    ]
    assert sum(item.probability for item in first.state_probabilities) == Decimal(1)


def test_filtered_and_smoothed_outputs_are_distinct_evidence():
    filtered = snapshot(RegimeOutputSemantics.FILTERED_CAUSAL)
    smoothed = snapshot(RegimeOutputSemantics.SMOOTHED_RETROSPECTIVE)

    assert filtered.regime_id != smoothed.regime_id
    assert filtered.is_causal is True
    assert smoothed.is_causal is False


def test_regime_states_must_match_model_spec():
    with pytest.raises(InvariantViolation, match="REGIME_STATES_DO_NOT_MATCH_SPEC"):
        RegimeSnapshot.create(
            source_state_id=ident("state"),
            spec=spec(),
            as_of=NOW,
            information_cutoff=CUTOFF,
            output_semantics=RegimeOutputSemantics.FILTERED_CAUSAL,
            state_probabilities={"state-0": Decimal("1")},
            entropy=Decimal(0),
            confidence=Decimal(1),
            input_evidence_ids=(ident("evidence"),),
            code_revision="git:abcdef0",
        )


def test_probability_simplex_fails_closed():
    with pytest.raises(InvariantViolation, match="REGIME_PROBABILITY_SIMPLEX_INVALID"):
        RegimeSnapshot.create(
            source_state_id=ident("state"),
            spec=spec(),
            as_of=NOW,
            information_cutoff=CUTOFF,
            output_semantics=RegimeOutputSemantics.FILTERED_CAUSAL,
            state_probabilities={
                "state-0": Decimal("0.50"),
                "state-1": Decimal("0.30"),
                "state-2": Decimal("0.30"),
            },
            entropy=Decimal("1.0"),
            confidence=Decimal("0.5"),
            input_evidence_ids=(ident("evidence"),),
            code_revision="git:abcdef0",
        )


def test_regime_payload_has_no_trading_authority():
    payload = snapshot().payload()
    encoded = str(payload).upper()

    assert "RISK_MULTIPLIER" not in encoded
    assert "TARGET_WEIGHT" not in encoded
    assert "ORDER_INTENT" not in encoded
    assert "BUY" not in encoded
    assert "SELL" not in encoded


def test_naive_or_future_cutoff_is_rejected():
    with pytest.raises(InvariantViolation, match="REGIME_TIME_INVALID"):
        RegimeSnapshot.create(
            source_state_id=ident("state"),
            spec=spec(),
            as_of=datetime(2026, 9, 20, 7),
            information_cutoff=CUTOFF,
            output_semantics=RegimeOutputSemantics.FILTERED_CAUSAL,
            state_probabilities={
                "state-0": Decimal("0.5"),
                "state-1": Decimal("0.3"),
                "state-2": Decimal("0.2"),
            },
            entropy=Decimal("1.0"),
            confidence=Decimal("0.5"),
            input_evidence_ids=(ident("evidence"),),
            code_revision="git:abcdef0",
        )

    with pytest.raises(InvariantViolation, match="REGIME_CUTOFF_AFTER_AS_OF"):
        RegimeSnapshot.create(
            source_state_id=ident("state"),
            spec=spec(),
            as_of=NOW,
            information_cutoff=NOW + timedelta(seconds=1),
            output_semantics=RegimeOutputSemantics.FILTERED_CAUSAL,
            state_probabilities={
                "state-0": Decimal("0.5"),
                "state-1": Decimal("0.3"),
                "state-2": Decimal("0.2"),
            },
            entropy=Decimal("1.0"),
            confidence=Decimal("0.5"),
            input_evidence_ids=(ident("evidence"),),
            code_revision="git:abcdef0",
        )
