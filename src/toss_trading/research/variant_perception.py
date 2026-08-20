from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Iterable


POLICY_SCHEMA = "focused-research-policy-v2"
DOSSIER_SCHEMA = "focused-research-dossier-v2"
RESEARCH_SECTION_ORDER = [
    "investment_thesis",
    "variant_view",
    "earnings_model",
    "valuation",
    "catalyst_path",
    "risk_disconfirming_evidence",
    "position_construction",
    "score_summary",
]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _bounded_text(value: Any, *, field: str, maximum: int = 1200) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} has invalid length")
    return normalized


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def load_focused_research_policy(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != POLICY_SCHEMA:
        raise ValueError("unsupported focused research policy")
    for field, minimum, maximum in (
        ("minimum_variant_chains", 1, 10),
        ("minimum_distinct_source_organizations", 2, 20),
        ("maximum_source_age_days", 1, 730),
        ("maximum_consensus_age_days", 1, 180),
        ("maximum_dossier_age_days", 1, 90),
        ("maximum_catalyst_horizon_days", 1, 1095),
        ("minimum_thesis_pillars", 2, 10),
        ("minimum_risks", 1, 20),
        ("minimum_contrary_evidence", 1, 20),
    ):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"focused research policy {field} is invalid")
    reconciliation_error = payload.get("maximum_implied_price_reconciliation_error")
    if (
        isinstance(reconciliation_error, bool)
        or not isinstance(reconciliation_error, (int, float))
        or not 0 < float(reconciliation_error) <= 0.05
    ):
        raise ValueError(
            "focused research policy maximum_implied_price_reconciliation_error is invalid"
        )
    for field in (
        "required_metric_categories",
        "allowed_units",
        "allowed_source_types",
        "allowed_implied_methods",
        "allowed_valuation_methods",
    ):
        value = payload.get(field)
        if (
            not isinstance(value, list)
            or not value
            or len(value) != len(set(value))
            or any(not isinstance(item, str) or not item for item in value)
        ):
            raise ValueError(f"focused research policy {field} is invalid")
    for field in (
        "require_price_implied_chain",
        "require_consensus_chain",
        "require_falsification",
        "require_bear_base_bull",
        "buy_requires_positive_expected_return",
    ):
        if payload.get(field) is not True:
            raise ValueError(f"focused research policy {field} must remain enabled")
    for field in ("execution_authorized", "promotion_authorized"):
        if payload.get(field) is not False:
            raise ValueError(f"focused research policy {field} must remain disabled")
    for field in (
        "score_may_gate_recommendation",
        "score_may_size_position",
    ):
        if payload.get(field) is not False:
            raise ValueError(f"focused research policy {field} must remain disabled")
    weight_limits = [
        _finite_number(payload.get(field), field=field)
        for field in (
            "maximum_initial_weight_percent",
            "maximum_target_weight_percent",
            "maximum_single_name_weight_percent",
        )
    ]
    if not 0 < weight_limits[0] <= weight_limits[1] <= weight_limits[2] <= 25:
        raise ValueError("focused research position weight limits are invalid")
    event_limit = _finite_number(
        payload.get("maximum_earnings_event_weight_percent"),
        field="maximum_earnings_event_weight_percent",
    )
    if not 0 < event_limit <= weight_limits[2]:
        raise ValueError("focused research earnings event weight limit is invalid")
    reward_to_risk = _finite_number(
        payload.get("minimum_reward_to_risk"), field="minimum_reward_to_risk"
    )
    if not 0 < reward_to_risk <= 10:
        raise ValueError("focused research minimum_reward_to_risk is invalid")
    return payload


def _source_registry(
    values: Any,
    *,
    as_of: date,
    policy: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list) or not values:
        raise ValueError("sources must be a non-empty list")
    registry: dict[str, dict[str, Any]] = {}
    allowed_types = set(policy["allowed_source_types"])
    for index, raw in enumerate(values):
        if not isinstance(raw, dict):
            raise ValueError("source must be an object")
        source_id = _bounded_text(raw.get("source_id"), field="source_id", maximum=120)
        if source_id in registry:
            raise ValueError("source_id must be unique")
        source_type = raw.get("source_type")
        if source_type not in allowed_types:
            raise ValueError(f"source {source_id} has an unsupported source_type")
        observed_at = date.fromisoformat(str(raw.get("observed_at")))
        if observed_at > as_of:
            raise ValueError(f"source {source_id} is from the future")
        maximum_age = (
            int(policy["maximum_consensus_age_days"])
            if source_type == "consensus_dataset"
            else int(policy["maximum_source_age_days"])
        )
        if (as_of - observed_at).days > maximum_age:
            raise ValueError(f"source {source_id} is stale")
        organization = _bounded_text(
            raw.get("organization"), field="source organization", maximum=160
        )
        locator = _bounded_text(raw.get("locator"), field="source locator", maximum=500)
        registry[source_id] = {
            "source_id": source_id,
            "source_type": source_type,
            "organization": organization,
            "observed_at": observed_at.isoformat(),
            "locator": locator,
        }
    organizations = {item["organization"] for item in registry.values()}
    if len(organizations) < int(policy["minimum_distinct_source_organizations"]):
        raise ValueError("focused research has too few distinct source organizations")
    return registry


def _source_ids(
    values: Any,
    *,
    field: str,
    registry: dict[str, dict[str, Any]],
    required_type: str | None = None,
) -> list[str]:
    if (
        not isinstance(values, list)
        or not values
        or len(values) != len(set(values))
        or any(not isinstance(item, str) for item in values)
    ):
        raise ValueError(f"{field} source_ids are invalid")
    missing = sorted(set(values) - set(registry))
    if missing:
        raise ValueError(f"{field} references unknown sources: {', '.join(missing)}")
    if required_type and not any(
        registry[source_id]["source_type"] == required_type for source_id in values
    ):
        raise ValueError(f"{field} requires a {required_type} source")
    return sorted(values)


def _estimate(
    raw: Any,
    *,
    field: str,
    registry: dict[str, dict[str, Any]],
    required_source_type: str | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must be an object")
    return {
        "value": _finite_number(raw.get("value"), field=f"{field}.value"),
        "source_ids": _source_ids(
            raw.get("source_ids"),
            field=field,
            registry=registry,
            required_type=required_source_type,
        ),
        "methodology": _bounded_text(
            raw.get("methodology"), field=f"{field}.methodology", maximum=800
        ),
    }


def _text_list(
    raw: Any,
    *,
    field: str,
    minimum: int = 1,
    maximum: int = 12,
) -> list[str]:
    if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
        raise ValueError(f"{field} must contain between {minimum} and {maximum} items")
    return [
        _bounded_text(item, field=field, maximum=600)
        for item in raw
    ]


def _earnings_model(raw: Any, *, registry: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("earnings_model must be an object")
    forecast_period = _bounded_text(
        raw.get("forecast_period"), field="earnings_model.forecast_period", maximum=40
    )
    market_implied_eps = _finite_number(
        raw.get("market_implied_eps_usd"), field="market_implied_eps_usd"
    )
    consensus_eps = _finite_number(
        raw.get("consensus_eps_usd"), field="consensus_eps_usd"
    )
    if market_implied_eps <= 0 or consensus_eps <= 0:
        raise ValueError("earnings model EPS anchors must be positive")
    market_implied_source_ids = _source_ids(
        raw.get("market_implied_eps_source_ids"),
        field="market_implied_eps",
        registry=registry,
        required_type="market_price",
    )
    consensus_source_ids = _source_ids(
        raw.get("consensus_eps_source_ids"),
        field="consensus_eps",
        registry=registry,
        required_type="consensus_dataset",
    )
    raw_scenarios = raw.get("scenarios")
    if not isinstance(raw_scenarios, list) or len(raw_scenarios) != 3:
        raise ValueError("earnings_model must contain bear, base, and bull")
    scenarios: dict[str, dict[str, Any]] = {}
    for item in raw_scenarios:
        if not isinstance(item, dict) or item.get("name") not in {"bear", "base", "bull"}:
            raise ValueError("earnings model has an invalid scenario name")
        name = str(item["name"])
        if name in scenarios:
            raise ValueError("earnings model scenario names must be unique")
        probability = _finite_number(item.get("probability"), field=f"{name}.probability")
        values = {
            field: _finite_number(item.get(field), field=f"{name}.{field}")
            for field in (
                "revenue_usd_millions",
                "gross_margin_percent",
                "operating_margin_percent",
                "eps_usd",
                "free_cash_flow_usd_millions",
                "capex_usd_millions",
            )
        }
        if not 0 < probability < 1 or values["revenue_usd_millions"] <= 0 or values["eps_usd"] <= 0:
            raise ValueError(f"{name} earnings scenario values are invalid")
        if not 0 < values["gross_margin_percent"] < 100 or not 0 < values["operating_margin_percent"] < 100:
            raise ValueError(f"{name} earnings margins are invalid")
        scenarios[name] = {
            "name": name,
            "probability": probability,
            **values,
            "key_assumptions": _text_list(
                item.get("key_assumptions"),
                field=f"{name}.key_assumptions",
                minimum=2,
            ),
            "source_ids": _source_ids(
                item.get("source_ids"), field=f"{name}.earnings_model", registry=registry
            ),
            "methodology": _bounded_text(
                item.get("methodology"), field=f"{name}.earnings_model.methodology"
            ),
        }
    if set(scenarios) != {"bear", "base", "bull"}:
        raise ValueError("earnings_model must contain bear, base, and bull")
    if not math.isclose(sum(item["probability"] for item in scenarios.values()), 1.0, abs_tol=1e-9):
        raise ValueError("earnings model probabilities must sum to one")
    if not scenarios["bear"]["eps_usd"] < scenarios["base"]["eps_usd"] < scenarios["bull"]["eps_usd"]:
        raise ValueError("earnings EPS must increase from bear to bull")
    ordered = [scenarios[name] for name in ("bear", "base", "bull")]
    base_eps = scenarios["base"]["eps_usd"]
    return {
        "forecast_period": forecast_period,
        "market_implied_eps_usd": market_implied_eps,
        "market_implied_eps_source_ids": market_implied_source_ids,
        "market_implied_eps_methodology": _bounded_text(
            raw.get("market_implied_eps_methodology"),
            field="market_implied_eps_methodology",
        ),
        "consensus_eps_usd": consensus_eps,
        "consensus_eps_source_ids": consensus_source_ids,
        "consensus_eps_methodology": _bounded_text(
            raw.get("consensus_eps_methodology"), field="consensus_eps_methodology"
        ),
        "scenarios": ordered,
        "base_vs_consensus_percent": (base_eps / consensus_eps - 1) * 100,
        "base_vs_market_implied_percent": (base_eps / market_implied_eps - 1) * 100,
        "probability_weighted_eps_usd": sum(
            item["probability"] * item["eps_usd"] for item in ordered
        ),
    }


def _valuation(
    raw: Any,
    *,
    current_price: float,
    earnings: dict[str, Any],
    policy: dict[str, Any],
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("valuation must be an object")
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or len(cases_raw) != 3:
        raise ValueError("valuation must contain bear, base, and bull")
    allowed_methods = set(policy["allowed_valuation_methods"])
    scenarios: dict[str, dict[str, Any]] = {}
    earnings_by_name = {item["name"]: item for item in earnings["scenarios"]}
    for item in cases_raw:
        if not isinstance(item, dict) or item.get("name") not in {"bear", "base", "bull"}:
            raise ValueError("valuation has an invalid scenario name")
        name = str(item["name"])
        if name in scenarios:
            raise ValueError("valuation scenario names must be unique")
        probability = _finite_number(item.get("probability"), field=f"{name}.probability")
        price = _finite_number(item.get("price_target"), field=f"{name}.price_target")
        if not 0 < probability < 1 or price <= 0:
            raise ValueError(f"{name} valuation values are invalid")
        if not math.isclose(probability, earnings_by_name[name]["probability"], abs_tol=1e-9):
            raise ValueError(f"{name} valuation probability must match earnings model")
        method = item.get("method")
        if method not in allowed_methods:
            raise ValueError(f"{name} valuation method is invalid")
        scenarios[name] = {
            "name": name,
            "probability": probability,
            "price_target": price,
            "method": method,
            "equation": _bounded_text(item.get("equation"), field=f"{name}.equation", maximum=800),
            "assumptions": _text_list(item.get("assumptions"), field=f"{name}.assumptions", minimum=2),
            "source_ids": _source_ids(
                item.get("source_ids"), field=f"{name}.valuation", registry=registry
            ),
        }
    if set(scenarios) != {"bear", "base", "bull"}:
        raise ValueError("valuation must contain bear, base, and bull")
    if not math.isclose(
        sum(item["probability"] for item in scenarios.values()), 1.0, abs_tol=1e-9
    ):
        raise ValueError("valuation probabilities must sum to one")
    if not (
        scenarios["bear"]["price_target"]
        < scenarios["base"]["price_target"]
        < scenarios["bull"]["price_target"]
    ):
        raise ValueError("valuation prices must increase from bear to bull")
    ordered = [scenarios[name] for name in ("bear", "base", "bull")]
    expected_price = sum(
        item["probability"] * item["price_target"] for item in ordered
    )
    result = {
        "framework": _bounded_text(raw.get("framework"), field="valuation.framework"),
        "scenarios": ordered,
        "probability_weighted_price": expected_price,
        "probability_weighted_return": expected_price / current_price - 1,
        "bear_case_return": scenarios["bear"]["price_target"] / current_price - 1,
        "base_case_return": scenarios["base"]["price_target"] / current_price - 1,
        "bull_case_return": scenarios["bull"]["price_target"] / current_price - 1,
    }
    downside = abs(min(result["bear_case_return"], 0))
    result["reward_to_risk"] = (
        result["base_case_return"] / downside if downside > 0 else None
    )
    return result


def _risk_evidence(raw: Any, *, registry: dict[str, dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("risk_disconfirming_evidence must be an object")
    risks_raw = raw.get("risks")
    if not isinstance(risks_raw, list) or len(risks_raw) < int(policy["minimum_risks"]):
        raise ValueError("focused research has too few explicit risks")
    risks = []
    risk_ids: set[str] = set()
    for item in risks_raw:
        if not isinstance(item, dict):
            raise ValueError("risk must be an object")
        risk_id = _bounded_text(item.get("risk_id"), field="risk_id", maximum=80)
        if risk_id in risk_ids:
            raise ValueError("risk_id must be unique")
        probability = item.get("probability")
        if probability not in {"low", "medium", "high"}:
            raise ValueError(f"risk {risk_id} probability is invalid")
        risks.append({
            "risk_id": risk_id,
            "statement": _bounded_text(item.get("statement"), field="risk.statement"),
            "probability": probability,
            "impact": _bounded_text(item.get("impact"), field="risk.impact"),
            "monitor": _bounded_text(item.get("monitor"), field="risk.monitor"),
            "source_ids": _source_ids(item.get("source_ids"), field="risk", registry=registry),
        })
        risk_ids.add(risk_id)
    contrary_raw = raw.get("contrary_evidence")
    if not isinstance(contrary_raw, list) or len(contrary_raw) < int(policy["minimum_contrary_evidence"]):
        raise ValueError("focused research has too little contrary evidence")
    contrary = []
    evidence_ids: set[str] = set()
    for item in contrary_raw:
        if not isinstance(item, dict):
            raise ValueError("contrary evidence must be an object")
        evidence_id = _bounded_text(item.get("evidence_id"), field="evidence_id", maximum=80)
        if evidence_id in evidence_ids:
            raise ValueError("evidence_id must be unique")
        contrary.append({
            "evidence_id": evidence_id,
            "observation": _bounded_text(item.get("observation"), field="contrary_evidence.observation"),
            "thesis_impact": _bounded_text(item.get("thesis_impact"), field="contrary_evidence.thesis_impact"),
            "source_ids": _source_ids(item.get("source_ids"), field="contrary_evidence", registry=registry),
        })
        evidence_ids.add(evidence_id)
    return {
        "risks": sorted(risks, key=lambda item: item["risk_id"]),
        "contrary_evidence": sorted(contrary, key=lambda item: item["evidence_id"]),
        "monitoring_plan": _bounded_text(raw.get("monitoring_plan"), field="monitoring_plan"),
    }


def _position_construction(
    raw: Any, *, policy: dict[str, Any], recommendation: str
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("position_construction must be an object")
    weights = {
        field: _finite_number(raw.get(field), field=f"position_construction.{field}")
        for field in (
            "initial_weight_percent",
            "target_weight_percent",
            "maximum_weight_percent",
            "earnings_event_weight_percent",
        )
    }
    if recommendation == "buy":
        if not 0 < weights["initial_weight_percent"] <= weights["target_weight_percent"] <= weights["maximum_weight_percent"]:
            raise ValueError("buy position weights must increase from initial to maximum")
    elif any(value != 0 for value in weights.values()):
        raise ValueError("non-buy research must specify a zero position")
    if weights["initial_weight_percent"] > float(policy["maximum_initial_weight_percent"]):
        raise ValueError("initial position weight exceeds policy")
    if weights["target_weight_percent"] > float(policy["maximum_target_weight_percent"]):
        raise ValueError("target position weight exceeds policy")
    if weights["maximum_weight_percent"] > float(policy["maximum_single_name_weight_percent"]):
        raise ValueError("maximum position weight exceeds policy")
    if weights["earnings_event_weight_percent"] > min(
        weights["maximum_weight_percent"], float(policy["maximum_earnings_event_weight_percent"])
    ):
        raise ValueError("earnings event weight exceeds policy")
    entry_low = _finite_number(raw.get("entry_price_low"), field="entry_price_low")
    entry_high = _finite_number(raw.get("entry_price_high"), field="entry_price_high")
    risk_budget = _finite_number(raw.get("risk_budget_bps"), field="risk_budget_bps")
    if not 0 < entry_low <= entry_high or not 0 <= risk_budget <= 1000:
        raise ValueError("position entry range or risk budget is invalid")
    if recommendation == "buy" and risk_budget == 0:
        raise ValueError("buy research requires a positive risk budget")
    return {
        **weights,
        "entry_price_low": entry_low,
        "entry_price_high": entry_high,
        "risk_budget_bps": risk_budget,
        "sizing_rationale": _bounded_text(raw.get("sizing_rationale"), field="sizing_rationale"),
        "add_conditions": _text_list(raw.get("add_conditions"), field="add_conditions"),
        "reduce_conditions": _text_list(raw.get("reduce_conditions"), field="reduce_conditions"),
        "exit_conditions": _text_list(raw.get("exit_conditions"), field="exit_conditions"),
        "execution_authorized": False,
    }


def _score_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("score_summary must be an object")
    conviction = raw.get("investment_conviction")
    if isinstance(conviction, bool) or not isinstance(conviction, int) or not 0 <= conviction <= 100:
        raise ValueError("investment conviction must be an integer from 0 to 100")
    return {
        "investment_conviction": conviction,
        "summary": _bounded_text(raw.get("summary"), field="score_summary.summary"),
        "used_for_recommendation_gate": False,
        "used_for_position_sizing": False,
    }


def build_focused_research_dossier(
    payload: dict[str, Any],
    *,
    policy: dict[str, Any],
    code_revision: str,
) -> dict[str, Any]:
    """Build an immutable, point-in-time Variant Perception research dossier."""

    if not isinstance(payload, dict):
        raise ValueError("focused research payload must be an object")
    if not code_revision or code_revision.lower() == "unknown":
        raise ValueError("focused research requires an immutable code revision")
    symbol = _bounded_text(payload.get("symbol"), field="symbol", maximum=20).upper()
    as_of = date.fromisoformat(str(payload.get("as_of_date")))
    current_price = _finite_number(payload.get("current_price"), field="current_price")
    if current_price <= 0:
        raise ValueError("current_price must be positive")
    currency = _bounded_text(payload.get("currency"), field="currency", maximum=8).upper()
    if currency != "USD":
        raise ValueError("focused research currently requires USD prices")
    sources = _source_registry(payload.get("sources"), as_of=as_of, policy=policy)
    price_source_ids = _source_ids(
        payload.get("price_source_ids"),
        field="current_price",
        registry=sources,
        required_type="market_price",
    )

    thesis_raw = payload.get("investment_thesis")
    if not isinstance(thesis_raw, dict):
        raise ValueError("investment_thesis must be an object")
    recommendation = thesis_raw.get("recommendation")
    if recommendation not in {"buy", "watch", "avoid", "no_view"}:
        raise ValueError("focused research recommendation is invalid")
    horizon_months = thesis_raw.get("time_horizon_months")
    if isinstance(horizon_months, bool) or not isinstance(horizon_months, int) or not 1 <= horizon_months <= 60:
        raise ValueError("investment thesis time horizon is invalid")
    investment_thesis = {
        "statement": _bounded_text(thesis_raw.get("statement"), field="investment_thesis.statement"),
        "time_horizon_months": horizon_months,
        "pillars": _text_list(
            thesis_raw.get("pillars"),
            field="investment_thesis.pillars",
            minimum=int(policy["minimum_thesis_pillars"]),
            maximum=8,
        ),
        "recommendation": recommendation,
    }
    variant_raw = payload.get("variant_view")
    if not isinstance(variant_raw, dict):
        raise ValueError("variant_view must be an object")

    catalysts_raw = payload.get("catalyst_path")
    if not isinstance(catalysts_raw, list) or not catalysts_raw:
        raise ValueError("focused research requires catalysts")
    catalysts: list[dict[str, Any]] = []
    catalyst_ids: set[str] = set()
    for raw in catalysts_raw:
        if not isinstance(raw, dict):
            raise ValueError("catalyst must be an object")
        catalyst_id = _bounded_text(raw.get("catalyst_id"), field="catalyst_id", maximum=80)
        if catalyst_id in catalyst_ids:
            raise ValueError("catalyst_id must be unique")
        window_start = date.fromisoformat(str(raw.get("window_start")))
        window_end = date.fromisoformat(str(raw.get("window_end")))
        if window_start < as_of or window_end < window_start:
            raise ValueError(f"catalyst {catalyst_id} has an invalid window")
        if (window_end - as_of).days > int(policy["maximum_catalyst_horizon_days"]):
            raise ValueError(f"catalyst {catalyst_id} is outside the allowed horizon")
        catalysts.append(
            {
                "catalyst_id": catalyst_id,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "event": _bounded_text(raw.get("event"), field="catalyst.event"),
                "observable": _bounded_text(
                    raw.get("observable"), field="catalyst.observable"
                ),
                "thesis_resolution": _bounded_text(
                    raw.get("thesis_resolution"), field="catalyst.thesis_resolution"
                ),
                "source_ids": _source_ids(
                    raw.get("source_ids"), field="catalyst", registry=sources
                ),
            }
        )
        catalyst_ids.add(catalyst_id)

    chains_raw = variant_raw.get("expectation_chains")
    if not isinstance(chains_raw, list) or len(chains_raw) < int(
        policy["minimum_variant_chains"]
    ):
        raise ValueError("focused research has too few variant chains")
    allowed_units = set(policy["allowed_units"])
    allowed_methods = set(policy["allowed_implied_methods"])
    required_categories = set(policy["required_metric_categories"])
    chains: list[dict[str, Any]] = []
    metric_ids: set[str] = set()
    for raw in chains_raw:
        if not isinstance(raw, dict):
            raise ValueError("variant chain must be an object")
        metric_id = _bounded_text(raw.get("metric_id"), field="metric_id", maximum=100)
        if metric_id in metric_ids:
            raise ValueError("metric_id must be unique")
        metric_ids.add(metric_id)
        category = raw.get("category")
        if category not in required_categories:
            raise ValueError(f"variant chain {metric_id} has an unsupported category")
        unit = raw.get("unit")
        if unit not in allowed_units:
            raise ValueError(f"variant chain {metric_id} has an unsupported unit")
        favorable_direction = raw.get("favorable_direction")
        if favorable_direction not in {"higher", "lower"}:
            raise ValueError(f"variant chain {metric_id} favorable_direction is invalid")
        consensus = _estimate(
            raw.get("market_consensus"),
            field=f"{metric_id}.market_consensus",
            registry=sources,
            required_source_type="consensus_dataset",
        )
        implied_raw = raw.get("price_implied_expectation")
        if not isinstance(implied_raw, dict) or implied_raw.get("method") not in allowed_methods:
            raise ValueError(f"variant chain {metric_id} has an invalid implied method")
        implied = _estimate(
            implied_raw,
            field=f"{metric_id}.price_implied_expectation",
            registry=sources,
            required_source_type="market_price",
        )
        model_price = _finite_number(
            implied_raw.get("model_price_at_implied"),
            field=f"{metric_id}.price_implied_expectation.model_price_at_implied",
        )
        if model_price <= 0:
            raise ValueError(f"variant chain {metric_id} has an invalid model price")
        reconciliation_error = abs(model_price / current_price - 1)
        if reconciliation_error > float(
            policy["maximum_implied_price_reconciliation_error"]
        ):
            raise ValueError(
                f"variant chain {metric_id} implied model does not reconcile to price"
            )
        assumptions = implied_raw.get("assumptions")
        if not isinstance(assumptions, list) or len(assumptions) < 2:
            raise ValueError(f"variant chain {metric_id} needs at least two implied assumptions")
        normalized_assumptions = []
        for assumption in assumptions:
            if not isinstance(assumption, dict):
                raise ValueError(f"variant chain {metric_id} assumption is invalid")
            normalized_assumptions.append(
                {
                    "name": _bounded_text(
                        assumption.get("name"), field="assumption.name", maximum=120
                    ),
                    "value": _finite_number(
                        assumption.get("value"), field="assumption.value"
                    ),
                    "unit": _bounded_text(
                        assumption.get("unit"), field="assumption.unit", maximum=40
                    ),
                    "source_ids": _source_ids(
                        assumption.get("source_ids"),
                        field="assumption",
                        registry=sources,
                    ),
                }
            )
        implied.update(
            {
                "method": implied_raw["method"],
                "equation": _bounded_text(
                    implied_raw.get("equation"),
                    field=f"{metric_id}.price_implied_expectation.equation",
                    maximum=800,
                ),
                "assumptions": normalized_assumptions,
                "price_reconciliation": {
                    "observed_price": current_price,
                    "model_price_at_implied": model_price,
                    "absolute_error": model_price - current_price,
                    "relative_error": reconciliation_error,
                },
            }
        )
        house = _estimate(
            raw.get("house_estimate"),
            field=f"{metric_id}.house_estimate",
            registry=sources,
        )
        linked_catalysts = raw.get("catalyst_ids")
        if (
            not isinstance(linked_catalysts, list)
            or not linked_catalysts
            or any(item not in catalyst_ids for item in linked_catalysts)
        ):
            raise ValueError(f"variant chain {metric_id} has invalid catalyst links")
        falsifiers_raw = raw.get("falsification_criteria")
        if not isinstance(falsifiers_raw, list) or not falsifiers_raw:
            raise ValueError(f"variant chain {metric_id} requires falsification criteria")
        implied_gap = house["value"] - implied["value"]
        consensus_gap = house["value"] - consensus["value"]
        favorable_gap = implied_gap if favorable_direction == "higher" else -implied_gap
        chains.append(
            {
                "metric_id": metric_id,
                "metric_name": _bounded_text(raw.get("metric_name"), field="metric_name"),
                "category": category,
                "horizon": _bounded_text(raw.get("horizon"), field="horizon", maximum=80),
                "unit": unit,
                "favorable_direction": favorable_direction,
                "market_consensus": consensus,
                "price_implied_expectation": implied,
                "house_estimate": house,
                "difference": {
                    "house_minus_price_implied": implied_gap,
                    "house_minus_consensus": consensus_gap,
                    "favorable_gap_vs_price_implied": favorable_gap,
                    "variant_direction": (
                        "bullish" if favorable_gap > 0 else "bearish" if favorable_gap < 0 else "neutral"
                    ),
                },
                "why_market_may_be_wrong": _bounded_text(
                    raw.get("why_market_may_be_wrong"),
                    field="why_market_may_be_wrong",
                ),
                "falsification_criteria": [
                    _bounded_text(item, field="falsification_criteria", maximum=500)
                    for item in falsifiers_raw
                ],
                "catalyst_ids": sorted(set(linked_catalysts)),
            }
        )
    categories = {item["category"] for item in chains}
    if not required_categories.issubset(categories):
        raise ValueError("variant chains do not cover every required metric category")

    earnings = _earnings_model(payload.get("earnings_model"), registry=sources)
    valuation = _valuation(
        payload.get("valuation"),
        current_price=current_price,
        earnings=earnings,
        policy=policy,
        registry=sources,
    )
    risk_evidence = _risk_evidence(
        payload.get("risk_disconfirming_evidence"), registry=sources, policy=policy
    )
    position = _position_construction(
        payload.get("position_construction"),
        policy=policy,
        recommendation=recommendation,
    )
    score = _score_summary(payload.get("score_summary"))
    if (
        recommendation == "buy"
        and policy["buy_requires_positive_expected_return"]
        and valuation["probability_weighted_return"] <= 0
    ):
        raise ValueError("buy recommendation requires positive expected return")
    if recommendation == "buy" and not any(
        chain["difference"]["variant_direction"] == "bullish" for chain in chains
    ):
        raise ValueError("buy recommendation requires a bullish variant chain")
    if recommendation == "buy" and (
        valuation["reward_to_risk"] is None
        or valuation["reward_to_risk"] < float(policy["minimum_reward_to_risk"])
    ):
        raise ValueError("buy recommendation has insufficient reward-to-risk")

    research_sections = {
        "investment_thesis": investment_thesis,
        "variant_view": {
            "market_expectation_summary": _bounded_text(
                variant_raw.get("market_expectation_summary"),
                field="variant_view.market_expectation_summary",
            ),
            "our_variant_summary": _bounded_text(
                variant_raw.get("our_variant_summary"),
                field="variant_view.our_variant_summary",
            ),
            "expectation_chains": sorted(chains, key=lambda item: item["metric_id"]),
        },
        "earnings_model": earnings,
        "valuation": valuation,
        "catalyst_path": sorted(catalysts, key=lambda item: (item["window_start"], item["catalyst_id"])),
        "risk_disconfirming_evidence": risk_evidence,
        "position_construction": position,
        "score_summary": score,
    }
    core = {
        "schema_version": DOSSIER_SCHEMA,
        "symbol": symbol,
        "as_of_date": as_of.isoformat(),
        "code_revision": code_revision,
        "policy_sha256": hashlib.sha256(
            _canonical_json(policy).encode("utf-8")
        ).hexdigest(),
        "current_price": current_price,
        "currency": currency,
        "price_source_ids": price_source_ids,
        "sources": sorted(sources.values(), key=lambda item: item["source_id"]),
        "research_sections": research_sections,
        "validation": {
            "state": "passed",
            "section_order": RESEARCH_SECTION_ORDER,
            "chain_order": [
                "market_consensus",
                "price_implied_expectation",
                "house_estimate",
                "difference",
                "catalysts",
            ],
            "point_in_time": True,
            "score_is_summary_only": True,
            "distinct_source_organizations": len(
                {item["organization"] for item in sources.values()}
            ),
        },
        "promotion_authorized": False,
        "execution_authorized": False,
    }
    core["dossier_id"] = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "focused-research:"
            + hashlib.sha256(_canonical_json(core).encode("utf-8")).hexdigest(),
        )
    )
    core["content_sha256"] = hashlib.sha256(
        _canonical_json(core).encode("utf-8")
    ).hexdigest()
    return core


def validate_focused_research_dossier(
    dossier: dict[str, Any],
    *,
    as_of_date: str,
    maximum_age_days: int,
) -> None:
    """Validate a built dossier before it can gate a research recommendation."""

    if dossier.get("schema_version") != DOSSIER_SCHEMA:
        raise ValueError("unsupported focused research dossier")
    if dossier.get("validation", {}).get("state") != "passed":
        raise ValueError("focused research dossier has not passed validation")
    sections = dossier.get("research_sections")
    if not isinstance(sections, dict) or list(sections) != RESEARCH_SECTION_ORDER:
        raise ValueError("focused research dossier section order is incomplete")
    score = sections.get("score_summary", {})
    if score.get("used_for_recommendation_gate") is not False or score.get(
        "used_for_position_sizing"
    ) is not False:
        raise ValueError("focused research score attempted to control a decision")
    if sections.get("position_construction", {}).get("execution_authorized") is not False:
        raise ValueError("position construction attempted to authorize execution")
    if dossier.get("execution_authorized") is not False or dossier.get(
        "promotion_authorized"
    ) is not False:
        raise ValueError("focused research dossier attempted to authorize execution")
    current = date.fromisoformat(as_of_date)
    dossier_date = date.fromisoformat(str(dossier.get("as_of_date")))
    if dossier_date > current:
        raise ValueError("focused research dossier is from the future")
    if (current - dossier_date).days > maximum_age_days:
        raise ValueError("focused research dossier is stale")
    supplied_hash = dossier.get("content_sha256")
    body = dict(dossier)
    body.pop("content_sha256", None)
    expected_hash = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
    if supplied_hash != expected_hash:
        raise ValueError("focused research dossier content hash mismatch")
    id_body = dict(body)
    supplied_id = id_body.pop("dossier_id", None)
    expected_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "focused-research:"
            + hashlib.sha256(_canonical_json(id_body).encode("utf-8")).hexdigest(),
        )
    )
    if supplied_id != expected_id:
        raise ValueError("focused research dossier identity mismatch")
    required_chain = [
        "market_consensus",
        "price_implied_expectation",
        "house_estimate",
        "difference",
        "catalysts",
    ]
    if dossier["validation"].get("chain_order") != required_chain:
        raise ValueError("focused research dossier chain is incomplete")
    if dossier["validation"].get("section_order") != RESEARCH_SECTION_ORDER:
        raise ValueError("focused research dossier section declaration is incomplete")


def render_focused_research_memo(dossier: dict[str, Any]) -> str:
    """Render the validated dossier in the required decision sequence, score last."""

    validate_focused_research_dossier(
        dossier,
        as_of_date=str(dossier["as_of_date"]),
        maximum_age_days=0,
    )
    sections = dossier["research_sections"]
    thesis = sections["investment_thesis"]
    variant = sections["variant_view"]
    earnings = sections["earnings_model"]
    valuation = sections["valuation"]
    risks = sections["risk_disconfirming_evidence"]
    position = sections["position_construction"]
    score = sections["score_summary"]
    earnings_cases = {item["name"]: item for item in earnings["scenarios"]}
    lines = [
        f"# {dossier['symbol']} Focused Research",
        "",
        f"As of: {dossier['as_of_date']} | Price: {dossier['currency']} {dossier['current_price']:.2f}",
        "",
        "## 1. Investment Thesis",
        "",
        thesis["statement"],
        f"Recommendation: {thesis['recommendation']} | Horizon: {thesis['time_horizon_months']} months",
        "",
        *[f"- {item}" for item in thesis["pillars"]],
        "",
        "## 2. Variant View",
        "",
        f"Market expectation: {variant['market_expectation_summary']}",
        f"Our view: {variant['our_variant_summary']}",
        "",
    ]
    for chain in variant["expectation_chains"]:
        lines.extend([
            f"### {chain['metric_name']} ({chain['horizon']})",
            "",
            f"Consensus {chain['market_consensus']['value']} {chain['unit']} → price-implied {chain['price_implied_expectation']['value']} → house {chain['house_estimate']['value']}",
            f"Gap vs price-implied: {chain['difference']['house_minus_price_implied']:+.2f} {chain['unit']} ({chain['difference']['variant_direction']})",
            f"Why different: {chain['why_market_may_be_wrong']}",
            f"Disconfirming test: {'; '.join(chain['falsification_criteria'])}",
            "",
        ])
    lines.extend([
        "## 3. Earnings Model",
        "",
        f"{earnings['forecast_period']} EPS — market-implied ${earnings['market_implied_eps_usd']:.2f}, consensus ${earnings['consensus_eps_usd']:.2f}, bear ${earnings_cases['bear']['eps_usd']:.2f}, base ${earnings_cases['base']['eps_usd']:.2f}, bull ${earnings_cases['bull']['eps_usd']:.2f}",
        f"Base vs consensus: {earnings['base_vs_consensus_percent']:+.1f}% | Base vs price-implied: {earnings['base_vs_market_implied_percent']:+.1f}%",
        "",
        "## 4. Valuation",
        "",
        f"Probability-weighted price: ${valuation['probability_weighted_price']:.2f} | Expected return: {valuation['probability_weighted_return']:+.1%} | Bear return: {valuation['bear_case_return']:+.1%}",
        f"Reward/risk: {valuation['reward_to_risk']:.2f}" if valuation["reward_to_risk"] is not None else "Reward/risk: not meaningful",
        "",
        "## 5. Catalyst Path",
        "",
        *[
            f"- {item['window_start']}–{item['window_end']}: {item['event']} — {item['observable']}"
            for item in sections["catalyst_path"]
        ],
        "",
        "## 6. Risk / Disconfirming Evidence",
        "",
        *[f"- Risk ({item['probability']}): {item['statement']} — monitor: {item['monitor']}" for item in risks["risks"]],
        *[f"- Contrary evidence: {item['observation']} — impact: {item['thesis_impact']}" for item in risks["contrary_evidence"]],
        "",
        "## 7. Position Construction",
        "",
        f"Initial {position['initial_weight_percent']:.2f}% → target {position['target_weight_percent']:.2f}% → maximum {position['maximum_weight_percent']:.2f}%",
        f"Entry ${position['entry_price_low']:.2f}–${position['entry_price_high']:.2f} | risk budget {position['risk_budget_bps']:.0f} bps | earnings-event cap {position['earnings_event_weight_percent']:.2f}%",
        position["sizing_rationale"],
        "",
        "## 8. Score Summary",
        "",
        f"Investment Conviction: {score['investment_conviction']}/100",
        score["summary"],
        "",
        "Score is summary-only and is not used for recommendation gating or position sizing.",
        "",
    ])
    return "\n".join(lines)


def load_focused_research_dossiers(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    return [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
