from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


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
    with Path(path).open(encoding="utf-8", newline="") as handle:
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
    with Path(path).open(encoding="utf-8", newline="") as handle:
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
    mapped_toss_symbols = {mapping.toss_symbol for mapping in mappings}
    missing = sorted(enabled_symbols - mapped_toss_symbols)
    if missing:
        raise ValueError(f"missing instrument mappings for enabled symbols: {missing}")
