from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from toss_trading.resources import resolve_resource


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    asset_class: str
    currency: str
    venue: str
    role: str
    engine_scope: str
    enabled: bool
    notes: str = ""


@dataclass(frozen=True)
class InstrumentMapping:
    symbol_id: str
    toss_symbol: str
    ticker: str
    vendor_symbol: str
    occ_symbol: str
    cik: str
    asset_class: str
    currency: str
    timezone: str
    mic: str
    effective_from: str
    effective_to: str | None


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def load_universe(path: str | Path) -> list[UniverseMember]:
    with resolve_resource(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    members = [
        UniverseMember(
            symbol=row["symbol"].strip().upper(),
            asset_class=row["asset_class"].strip(),
            currency=row["currency"].strip().upper(),
            venue=row["venue"].strip(),
            role=row["role"].strip(),
            engine_scope=row["engine_scope"].strip(),
            enabled=_bool(row["enabled"]),
            notes=row.get("notes", "").strip(),
        )
        for row in rows
    ]
    if not members:
        raise ValueError("universe is empty")
    duplicates = {m.symbol for m in members if [x.symbol for x in members].count(m.symbol) > 1}
    if duplicates:
        raise ValueError(f"duplicate universe symbols: {sorted(duplicates)}")
    return members


def load_instrument_mappings(path: str | Path) -> list[InstrumentMapping]:
    with resolve_resource(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    mappings = [
        InstrumentMapping(
            symbol_id=row["symbol_id"].strip(),
            toss_symbol=row["toss_symbol"].strip().upper(),
            ticker=row["ticker"].strip().upper(),
            vendor_symbol=row["vendor_symbol"].strip().upper(),
            occ_symbol=row.get("occ_symbol", "").strip(),
            cik=row.get("cik", "").strip(),
            asset_class=row["asset_class"].strip(),
            currency=row["currency"].strip().upper(),
            timezone=row["timezone"].strip(),
            mic=row["mic"].strip(),
            effective_from=row["effective_from"].strip(),
            effective_to=row.get("effective_to", "").strip() or None,
        )
        for row in rows
    ]
    if not mappings:
        raise ValueError("instrument mappings are empty")
    return mappings


def validate_universe_mapping(
    universe: list[UniverseMember],
    mappings: list[InstrumentMapping],
) -> None:
    enabled_symbols = {member.symbol for member in universe if member.enabled}
    mapping_counts: dict[str, int] = {}
    symbol_id_counts: dict[str, int] = {}
    for mapping in mappings:
        mapping_counts[mapping.toss_symbol] = mapping_counts.get(mapping.toss_symbol, 0) + 1
        symbol_id_counts[mapping.symbol_id] = symbol_id_counts.get(mapping.symbol_id, 0) + 1
        try:
            effective_from = date.fromisoformat(mapping.effective_from)
            effective_to = (
                date.fromisoformat(mapping.effective_to)
                if mapping.effective_to is not None
                else None
            )
        except ValueError as exc:
            raise ValueError(
                f"invalid instrument effective date for {mapping.symbol_id}"
            ) from exc
        if effective_to is not None and effective_to < effective_from:
            raise ValueError(
                f"instrument effective_to precedes effective_from: {mapping.symbol_id}"
            )
        if not all(
            (
                mapping.symbol_id,
                mapping.toss_symbol,
                mapping.ticker,
                mapping.vendor_symbol,
                mapping.asset_class,
                mapping.currency,
                mapping.timezone,
                mapping.mic,
            )
        ):
            raise ValueError(f"incomplete instrument mapping: {mapping.symbol_id}")
        if mapping.asset_class.upper() == "ETF" and (
            len(mapping.cik) != 10 or not mapping.cik.isdigit()
        ):
            raise ValueError(f"ETF mapping requires a 10-digit CIK: {mapping.symbol_id}")

    mapped_toss_symbols = set(mapping_counts)
    missing = sorted(enabled_symbols - mapped_toss_symbols)
    if missing:
        raise ValueError(f"missing instrument mappings for enabled symbols: {missing}")
    extras = sorted(mapped_toss_symbols - enabled_symbols)
    if extras:
        raise ValueError(f"instrument mappings are outside the enabled universe: {extras}")
    duplicates = sorted(symbol for symbol, count in mapping_counts.items() if count != 1)
    if duplicates:
        raise ValueError(f"enabled symbols require exactly one mapping: {duplicates}")
    duplicate_ids = sorted(symbol_id for symbol_id, count in symbol_id_counts.items() if count != 1)
    if duplicate_ids:
        raise ValueError(f"duplicate instrument symbol_id values: {duplicate_ids}")

    universe_by_symbol = {member.symbol: member for member in universe if member.enabled}
    for mapping in mappings:
        member = universe_by_symbol[mapping.toss_symbol]
        if mapping.asset_class.upper() != member.asset_class.upper():
            raise ValueError(f"asset class mismatch for {mapping.toss_symbol}")
        if mapping.currency != member.currency:
            raise ValueError(f"currency mismatch for {mapping.toss_symbol}")
