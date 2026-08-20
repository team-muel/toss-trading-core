from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Iterable


POLICY_SCHEMA = "focused-research-policy-v1"
DOSSIER_SCHEMA = "focused-research-dossier-v1"


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


def _scenario_analysis(raw: Any, *, current_price: float) -> dict[str, Any]:
    if not isinstance(raw, list) or len(raw) != 3:
        raise ValueError("scenario_analysis must contain bear, base, and bull")
    scenarios: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or item.get("name") not in {"bear", "base", "bull"}:
            raise ValueError("scenario analysis has an invalid name")
        name = str(item["name"])
        if name in scenarios:
            raise ValueError("scenario analysis names must be unique")
        probability = _finite_number(item.get("probability"), field=f"{name}.probability")
        price = _finite_number(item.get("price_target"), field=f"{name}.price_target")
        if not 0 < probability < 1 or price <= 0:
            raise ValueError(f"{name} scenario values are invalid")
        scenarios[name] = {
            "name": name,
            "probability": probability,
            "price_target": price,
            "thesis": _bounded_text(item.get("thesis"), field=f"{name}.thesis"),
        }
    if set(scenarios) != {"bear", "base", "bull"}:
        raise ValueError("scenario analysis must contain bear, base, and bull")
    if not math.isclose(
        sum(item["probability"] for item in scenarios.values()), 1.0, abs_tol=1e-9
    ):
        raise ValueError("scenario probabilities must sum to one")
    if not (
        scenarios["bear"]["price_target"]
        < scenarios["base"]["price_target"]
        < scenarios["bull"]["price_target"]
    ):
        raise ValueError("scenario prices must increase from bear to bull")
    ordered = [scenarios[name] for name in ("bear", "base", "bull")]
    expected_price = sum(
        item["probability"] * item["price_target"] for item in ordered
    )
    return {
        "scenarios": ordered,
        "probability_weighted_price": expected_price,
        "probability_weighted_return": expected_price / current_price - 1,
        "bear_case_return": scenarios["bear"]["price_target"] / current_price - 1,
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

    catalysts_raw = payload.get("catalysts")
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

    chains_raw = payload.get("variant_chains")
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

    scenario = _scenario_analysis(payload.get("scenario_analysis"), current_price=current_price)
    recommendation = payload.get("recommendation")
    if recommendation not in {"buy", "watch", "avoid", "no_view"}:
        raise ValueError("focused research recommendation is invalid")
    if (
        recommendation == "buy"
        and policy["buy_requires_positive_expected_return"]
        and scenario["probability_weighted_return"] <= 0
    ):
        raise ValueError("buy recommendation requires positive expected return")
    if recommendation == "buy" and not any(
        chain["difference"]["variant_direction"] == "bullish" for chain in chains
    ):
        raise ValueError("buy recommendation requires a bullish variant chain")

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
        "market_narrative": _bounded_text(
            payload.get("market_narrative"), field="market_narrative"
        ),
        "variant_summary": _bounded_text(
            payload.get("variant_summary"), field="variant_summary"
        ),
        "sources": sorted(sources.values(), key=lambda item: item["source_id"]),
        "variant_chains": sorted(chains, key=lambda item: item["metric_id"]),
        "catalysts": sorted(catalysts, key=lambda item: item["catalyst_id"]),
        "scenario_analysis": scenario,
        "recommendation": recommendation,
        "monitoring_plan": _bounded_text(
            payload.get("monitoring_plan"), field="monitoring_plan"
        ),
        "validation": {
            "state": "passed",
            "chain_order": [
                "market_consensus",
                "price_implied_expectation",
                "house_estimate",
                "difference",
                "catalysts",
            ],
            "point_in_time": True,
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


def load_focused_research_dossiers(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    return [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
