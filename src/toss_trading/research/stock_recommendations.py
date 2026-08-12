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


def load_recommendation_policy(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "stock-recommendation-policy-v1":
        raise ValueError("unsupported stock recommendation policy")
    minimum = int(payload["minimum_universe_size"])
    maximum = int(payload["maximum_universe_size"])
    target = int(payload["target_universe_size"])
    if not 1 <= minimum <= target <= maximum:
        raise ValueError("invalid recommendation universe bounds")
    if not 1 <= int(payload["maximum_recommendations"]) <= 50:
        raise ValueError("invalid maximum recommendations")
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
) -> dict[str, Any]:
    """Rank a broad common-stock universe without granting order authority."""

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
    selected = eligible[: int(policy["maximum_recommendations"])]
    manifests = sorted(set(source_manifest_ids))
    identity = {
        "as_of_date": as_of_date,
        "code_revision": code_revision,
        "policy": policy,
        "source_manifest_ids": manifests,
        "recommendations": [item["symbol"] for item in selected],
    }
    recommendation_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, "stock-recommendation:" + _canonical_json(identity))
    )
    return {
        "schema_version": "stock-recommendation-run-v1",
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
        "recommendations": selected,
        "prospective_tracking": {
            "state": "registered",
            "benchmark": policy["benchmark"],
            "horizons_trading_days": policy[
                "prospective_horizons_trading_days"
            ],
            "performance_revealed": False,
        },
        "promotion_authorized": False,
        "execution_authorized": False,
        "disclosure": (
            "research_screening_output_not_personalized_advice_and_not_an_order"
        ),
    }
