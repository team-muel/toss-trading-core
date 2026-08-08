from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path


def load_execution_cost_policy(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    policy = payload.get("execution_cost_policy")
    if not isinstance(policy, dict):
        raise ValueError("validation protocol lacks execution_cost_policy")
    slippage = policy.get("slippage")
    tiers = slippage.get("tiers") if isinstance(slippage, dict) else None
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("execution cost policy lacks slippage tiers")
    if not isinstance(slippage.get("source"), str) or not slippage["source"].strip():
        raise ValueError("execution cost policy lacks a slippage source")
    try:
        notional = float(policy["portfolio_notional_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("execution cost policy lacks portfolio notional") from exc
    if notional <= 0:
        raise ValueError("execution cost policy portfolio notional must be positive")
    previous_limit = 0.0
    for index, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            raise ValueError("execution cost policy has an invalid slippage tier")
        try:
            slippage_bps = float(tier["slippage_bps"])
            maximum = tier.get("maximum_order_notional_usd")
            maximum_value = float(maximum) if maximum is not None else None
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("execution cost policy has an invalid slippage tier") from exc
        if slippage_bps < 0:
            raise ValueError("execution cost policy slippage must be nonnegative")
        if maximum_value is None:
            if index != len(tiers) - 1:
                raise ValueError("open slippage tier must be last")
        elif maximum_value <= previous_limit:
            raise ValueError("slippage tier limits must increase")
        else:
            previous_limit = maximum_value
    if tiers[-1].get("maximum_order_notional_usd") is not None:
        raise ValueError("execution cost policy must end with an open tier")
    return policy


def build_cost_calibration(
    db_path: str | Path,
    *,
    as_of: str,
    portfolio_notional_usd: float,
    slippage_source: str,
    slippage_tiers: list[dict],
) -> dict:
    target_date = date.fromisoformat(as_of)
    connection = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        schedule = connection.execute(
            """
            SELECT ts, commission_rate_decimal, start_date, end_date
            FROM commission_rate_schedule_snapshot
            WHERE market_country = 'US'
              AND (start_date IS NULL OR start_date <= ?)
              AND (end_date IS NULL OR end_date >= ?)
            ORDER BY ts DESC
            LIMIT 1
            """,
            (target_date.isoformat(), target_date.isoformat()),
        ).fetchone()
        if schedule is None:
            raise ValueError("no current US commission schedule is available")
        samples = connection.execute(
            """
            WITH ranked AS (
              SELECT
                ts,
                cumulative_filled_amount_decimal,
                cumulative_commission_decimal,
                ROW_NUMBER() OVER (
                  PARTITION BY order_id
                  ORDER BY snapshot_seq DESC, ts DESC
                ) AS latest_snapshot
              FROM execution_snapshot_log
            )
            SELECT cumulative_filled_amount_decimal, cumulative_commission_decimal
            FROM ranked
            WHERE latest_snapshot = 1
              AND CAST(COALESCE(cumulative_filled_amount_decimal, '0') AS REAL) > 0
            ORDER BY ts DESC
            LIMIT 100
            """
        ).fetchall()
    finally:
        connection.close()
    filled = sum(Decimal(str(row[0])) for row in samples)
    paid = sum(Decimal(str(row[1] or "0")) for row in samples)
    observed_bps = float(paid / filled * Decimal("10000")) if filled else None
    # Toss documents commissionRate in percentage units: 0.1 means 0.1%.
    schedule_percent = Decimal(str(schedule["commission_rate_decimal"]))
    if not schedule_percent.is_finite() or not Decimal("0") <= schedule_percent <= Decimal("5"):
        raise ValueError("US commission schedule is outside the supported percent range")
    if portfolio_notional_usd <= 0:
        raise ValueError("portfolio notional must be positive")
    normalized_bps = float(schedule_percent * Decimal("100"))
    return {
        "schema_version": "execution-cost-calibration-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": target_date.isoformat(),
        "portfolio_notional_usd": portfolio_notional_usd,
        "commission": {
            "source": "toss_openapi_account_commission_schedule",
            "rate_unit": "percent",
            "raw_rate_percent": format(schedule_percent, "f"),
            "normalized_bps": normalized_bps,
            "minimum_commission_usd": 0.0,
            "observed_at": str(schedule["ts"]),
            "valid_through": schedule["end_date"],
            "execution_sample_count": len(samples),
            "execution_sample_notional_usd": float(filled),
            "execution_sample_effective_bps": observed_bps,
        },
        "slippage": {
            "source": slippage_source,
            "tiers": slippage_tiers,
        },
    }


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    handle = tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a sanitized research cost calibration from the read-only account ledger."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--portfolio-notional-usd", type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    policy = load_execution_cost_policy(args.policy)
    notional = (
        args.portfolio_notional_usd
        if args.portfolio_notional_usd is not None
        else float(policy["portfolio_notional_usd"])
    )
    slippage = policy["slippage"]
    payload = build_cost_calibration(
        args.db,
        as_of=args.as_of,
        portfolio_notional_usd=notional,
        slippage_source=str(slippage["source"]),
        slippage_tiers=list(slippage["tiers"]),
    )
    destination = Path(args.output)
    _atomic_json(destination, payload)
    print(
        json.dumps(
            {
                "cost_calibration": str(destination),
                "commission_bps": payload["commission"]["normalized_bps"],
                "valid_through": payload["commission"]["valid_through"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
