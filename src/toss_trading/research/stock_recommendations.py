from __future__ import annotations

import hashlib
import json
import math
import statistics
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from toss_trading.research.variant_perception import (
    DOSSIER_SCHEMA,
    validate_focused_research_dossier,
)


def load_recommendation_policy(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "stock-recommendation-policy-v2":
        raise ValueError("unsupported stock recommendation policy")
    minimum = int(payload["minimum_universe_size"])
    maximum = int(payload["maximum_universe_size"])
    target = int(payload["target_universe_size"])
    if not 1 <= minimum <= target <= maximum:
        raise ValueError("invalid recommendation universe bounds")
    if not 1 <= int(payload["maximum_screening_candidates"]) <= 100:
        raise ValueError("invalid maximum screening candidates")
    if not 1 <= int(payload["maximum_buy_recommendations"]) <= 50:
        raise ValueError("invalid maximum buy recommendations")
    if int(payload["maximum_buy_recommendations"]) > int(
        payload["maximum_screening_candidates"]
    ):
        raise ValueError("buy recommendation limit exceeds screening limit")
    weights = payload.get("factor_weights")
    expected = {
        "momentum_12_1",
        "momentum_6_1",
        "low_volatility",
        "trend_strength",
    }
    if not isinstance(weights, dict) or set(weights) != expected:
        raise ValueError("recommendation factor weights are incomplete")
    if not math.isclose(sum(float(value) for value in weights.values()), 1.0):
        raise ValueError("recommendation factor weights must sum to one")
    if payload.get("execution_authorized") is not False:
        raise ValueError("stock recommendations may not authorize execution")
    if payload.get("promotion_authorized") is not False:
        raise ValueError("stock recommendations may not authorize promotion")
    if payload.get("require_focused_research_for_buy_recommendation") is not True:
        raise ValueError("stock buy recommendations require focused research")
    return payload


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _percentile_ranks(
    rows: list[dict[str, Any]], field: str
) -> dict[str, float]:
    ordered = sorted(rows, key=lambda item: (float(item[field]), item["symbol"]))
    denominator = max(1, len(ordered) - 1)
    return {
        item["symbol"]: index / denominator
        for index, item in enumerate(ordered)
    }


def _daily_volatility(closes: list[float], days: int = 126) -> float:
    sample = closes[-(days + 1) :]
    returns = [sample[index] / sample[index - 1] - 1 for index in range(1, len(sample))]
    if len(returns) < 2:
        raise ValueError("insufficient returns for volatility")
    return statistics.stdev(returns) * math.sqrt(252)


def generate_stock_recommendations(
    rows: Iterable[dict[str, Any]],
    *,
    policy: dict[str, Any],
    as_of_date: str,
    code_revision: str,
    source_manifest_ids: Iterable[str] = (),
    focused_research_dossiers: Iterable[dict[str, Any]] = (),
    focused_research_maximum_age_days: int = 14,
) -> dict[str, Any]:
    """Screen a broad universe and gate buy recommendations on focused research."""

    date.fromisoformat(as_of_date)
    if not code_revision or code_revision.lower() == "unknown":
        raise ValueError("recommendations require an immutable code revision")
    if policy.get("execution_authorized") is not False:
        raise ValueError("recommendation policy attempted to authorize execution")
    history: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        market_date = str(row.get("exchange_local_date") or "")
        if not symbol or not market_date or market_date > as_of_date:
            continue
        close = float(row["close"])
        volume = float(row["volume"])
        if close <= 0 or volume < 0 or not all(map(math.isfinite, (close, volume))):
            raise ValueError(f"invalid recommendation bar: {symbol} {market_date}")
        history[symbol][market_date] = (close, volume)

    minimum_history = int(policy["minimum_history_trading_days"])
    candidates: list[dict[str, Any]] = []
    for symbol, dated in history.items():
        ordered = sorted(dated.items())
        if len(ordered) < minimum_history or ordered[-1][0] != as_of_date:
            continue
        recent = ordered[-minimum_history:]
        closes = [value[0] for _, value in recent]
        volumes = [value[1] for _, value in recent]
        latest = closes[-1]
        median_dollar_volume = statistics.median(
            close * volume for close, volume in zip(closes[-21:], volumes[-21:])
        )
        if latest < float(policy["minimum_price_usd"]):
            continue
        if median_dollar_volume < float(
            policy["minimum_median_dollar_volume_usd"]
        ):
            continue
        moving_average_200 = statistics.fmean(closes[-200:])
        candidates.append(
            {
                "symbol": symbol,
                "latest_close": latest,
                "median_dollar_volume_21d": median_dollar_volume,
                "momentum_12_1": closes[-22] / closes[-253] - 1,
                "momentum_6_1": closes[-22] / closes[-127] - 1,
                "low_volatility": -_daily_volatility(closes),
                "trend_strength": latest / moving_average_200 - 1,
                "above_200_day_average": latest > moving_average_200,
            }
        )

    candidates.sort(
        key=lambda item: (-item["median_dollar_volume_21d"], item["symbol"])
    )
    universe = candidates[: int(policy["target_universe_size"])]
    if len(universe) < int(policy["minimum_universe_size"]):
        raise ValueError(
            "eligible common-stock universe is smaller than the configured minimum"
        )

    ranks = {
        factor: _percentile_ranks(universe, factor)
        for factor in policy["factor_weights"]
    }
    for item in universe:
        item["composite_score"] = sum(
            float(weight) * ranks[factor][item["symbol"]]
            for factor, weight in policy["factor_weights"].items()
        )
    eligible = [
        item
        for item in universe
        if (
            not policy.get("require_positive_12_1_momentum")
            or item["momentum_12_1"] > 0
        )
        and (
            not policy.get("require_above_200_day_average")
            or item["above_200_day_average"]
        )
    ]
    eligible.sort(key=lambda item: (-item["composite_score"], item["symbol"]))
    selected = eligible[: int(policy["maximum_screening_candidates"])]
    dossier_by_symbol: dict[str, dict[str, Any]] = {}
    stale_dossier_symbols: set[str] = set()
    driver_model_upgrade_symbols: set[str] = set()
    earnings_quality_upgrade_symbols: set[str] = set()
    for dossier in focused_research_dossiers:
        dossier_symbol = str(dossier.get("symbol") or "").upper()
        if dossier.get("schema_version") != DOSSIER_SCHEMA:
            if dossier_symbol:
                if dossier.get("schema_version") == "focused-research-dossier-v3":
                    earnings_quality_upgrade_symbols.add(dossier_symbol)
                else:
                    driver_model_upgrade_symbols.add(dossier_symbol)
            continue
        dossier_date = date.fromisoformat(str(dossier.get("as_of_date")))
        current_date = date.fromisoformat(as_of_date)
        if dossier_date > current_date:
            raise ValueError("focused research dossier is from the future")
        if (current_date - dossier_date).days > focused_research_maximum_age_days:
            if dossier_symbol:
                stale_dossier_symbols.add(dossier_symbol)
            continue
        validate_focused_research_dossier(
            dossier,
            as_of_date=as_of_date,
            maximum_age_days=focused_research_maximum_age_days,
        )
        symbol = dossier_symbol
        existing = dossier_by_symbol.get(symbol)
        if existing is None or str(dossier["as_of_date"]) > str(existing["as_of_date"]):
            dossier_by_symbol[symbol] = dossier

    recommendations: list[dict[str, Any]] = []
    screening_candidates: list[dict[str, Any]] = []
    for item in selected:
        dossier = dossier_by_symbol.get(item["symbol"])
        sections = dossier.get("research_sections", {}) if dossier is not None else {}
        thesis = sections.get("investment_thesis", {})
        if dossier is not None and thesis.get("recommendation") == "buy":
            focus_state = "buy_dossier_passed"
        elif dossier is not None:
            focus_state = f"focused_research_{thesis.get('recommendation', 'no_view')}"
        elif item["symbol"] in stale_dossier_symbols:
            focus_state = "focused_research_stale"
        elif item["symbol"] in earnings_quality_upgrade_symbols:
            focus_state = "focused_research_earnings_quality_required"
        elif item["symbol"] in driver_model_upgrade_symbols:
            focus_state = "focused_research_driver_model_required"
        else:
            focus_state = "focused_research_required"
        screened = {
            **item,
            "focused_research_state": focus_state,
            "focused_research_dossier_id": (
                dossier.get("dossier_id") if dossier is not None else None
            ),
        }
        screening_candidates.append(screened)
        if focus_state == "buy_dossier_passed":
            valuation = sections["valuation"]
            earnings = sections["earnings_model"]
            earnings_quality = sections["earnings_quality"]
            recommendations.append(
                {
                    "symbol": item["symbol"],
                    "focused_research_state": focus_state,
                    "focused_research_dossier_id": dossier["dossier_id"],
                    "investment_thesis": thesis,
                    "variant_view": sections["variant_view"],
                    "earnings_model": earnings,
                    "earnings_quality": earnings_quality,
                    "valuation": valuation,
                    "catalyst_path": sections["catalyst_path"],
                    "risk_disconfirming_evidence": sections["risk_disconfirming_evidence"],
                    "position_construction": sections["position_construction"],
                    "screening_summary": {
                        "composite_score": item["composite_score"],
                        "momentum_12_1": item["momentum_12_1"],
                    },
                    "score_summary": sections["score_summary"],
                }
            )
    recommendations.sort(
        key=lambda item: (
            -float(item["position_construction"]["target_weight_percent"]),
            -float(item["valuation"]["probability_weighted_return"]),
            item["symbol"],
        )
    )
    recommendations = recommendations[: int(policy["maximum_buy_recommendations"])]
    manifests = sorted(set(source_manifest_ids))
    identity = {
        "as_of_date": as_of_date,
        "code_revision": code_revision,
        "policy": policy,
        "source_manifest_ids": manifests,
        "screening_candidates": screening_candidates,
        "recommendations": recommendations,
        "focused_research_dossier_ids": sorted(
            str(item["focused_research_dossier_id"])
            for item in recommendations
        ),
    }
    recommendation_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, "stock-recommendation:" + _canonical_json(identity))
    )
    return {
        "schema_version": "stock-recommendation-run-v5",
        "recommendation_id": recommendation_id,
        "as_of_date": as_of_date,
        "code_revision": code_revision,
        "policy_sha256": hashlib.sha256(
            _canonical_json(policy).encode("utf-8")
        ).hexdigest(),
        "source_manifest_ids": manifests,
        "universe": {
            "requested_minimum": int(policy["minimum_universe_size"]),
            "requested_target": int(policy["target_universe_size"]),
            "screened_count": len(history),
            "liquid_history_eligible_count": len(candidates),
            "ranked_count": len(universe),
            "positive_trend_eligible_count": len(eligible),
        },
        "screening_candidates": screening_candidates,
        "recommendations": recommendations,
        "recommendation_gate": {
            "focused_research_required": True,
            "screening_candidate_count": len(screening_candidates),
            "buy_dossier_passed_count": len(recommendations),
            "withheld_pending_focused_research_count": sum(
                item["focused_research_state"] == "focused_research_required"
                for item in screening_candidates
            ),
            "stale_dossier_count": sum(
                item["focused_research_state"] == "focused_research_stale"
                for item in screening_candidates
            ),
            "driver_model_upgrade_required_count": sum(
                item["focused_research_state"]
                == "focused_research_driver_model_required"
                for item in screening_candidates
            ),
            "earnings_quality_upgrade_required_count": sum(
                item["focused_research_state"]
                == "focused_research_earnings_quality_required"
                for item in screening_candidates
            ),
            "non_buy_dossier_count": sum(
                item["focused_research_state"]
                in {
                    "focused_research_watch",
                    "focused_research_avoid",
                    "focused_research_no_view",
                }
                for item in screening_candidates
            ),
        },
        "focused_research_queue": [
            {
                "priority": index + 1,
                "symbol": item["symbol"],
                "screening_score": item["composite_score"],
                "state": item["focused_research_state"],
                "required_sections": [
                    "investment_thesis",
                    "variant_view",
                    "earnings_model",
                    "earnings_quality",
                    "valuation",
                    "catalyst_path",
                    "risk_disconfirming_evidence",
                    "position_construction",
                    "score_summary",
                ],
                "required_expectation_chain": [
                    "market_consensus",
                    "price_implied_expectation",
                    "house_estimate",
                    "difference",
                    "catalysts",
                ],
            }
            for index, item in enumerate(screening_candidates)
            if item["focused_research_state"] != "buy_dossier_passed"
        ],
        "prospective_tracking": {
            "state": "registered" if recommendations else "awaiting_buy_dossier",
            "benchmark": policy["benchmark"],
            "horizons_trading_days": policy[
                "prospective_horizons_trading_days"
            ],
            "performance_revealed": False,
            "tracked_population": "buy_recommendations_only",
        },
        "promotion_authorized": False,
        "execution_authorized": False,
        "disclosure": (
            "research_screening_output_not_personalized_advice_and_not_an_order"
        ),
    }
