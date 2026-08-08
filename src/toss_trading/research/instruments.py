from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from toss_trading.data.universe import InstrumentMapping


@dataclass(frozen=True)
class InstrumentAlias:
    instrument_id: str
    canonical_symbol: str
    provider: str
    provider_symbol: str
    effective_from: str
    effective_to: str | None
    event_type: str
    source_url: str
    reviewed_at: str


@dataclass(frozen=True)
class CorporateAction:
    event_id: str
    instrument_id: str
    event_type: str
    effective_date: str
    old_symbol: str
    new_symbol: str
    source_url: str
    reviewed_at: str


def load_instrument_history(path: str | Path) -> list[InstrumentAlias]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    aliases = [
        InstrumentAlias(
            instrument_id=row["instrument_id"].strip(),
            canonical_symbol=row["canonical_symbol"].strip().upper(),
            provider=row["provider"].strip().lower(),
            provider_symbol=row["provider_symbol"].strip().upper(),
            effective_from=row["effective_from"].strip(),
            effective_to=row.get("effective_to", "").strip() or None,
            event_type=row["event_type"].strip().lower(),
            source_url=row["source_url"].strip(),
            reviewed_at=row["reviewed_at"].strip(),
        )
        for row in rows
    ]
    if not aliases:
        raise ValueError("instrument history is empty")
    return aliases


def load_corporate_actions(path: str | Path) -> list[CorporateAction]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        CorporateAction(
            event_id=row["event_id"].strip(),
            instrument_id=row["instrument_id"].strip(),
            event_type=row["event_type"].strip().lower(),
            effective_date=row["effective_date"].strip(),
            old_symbol=row.get("old_symbol", "").strip().upper(),
            new_symbol=row.get("new_symbol", "").strip().upper(),
            source_url=row["source_url"].strip(),
            reviewed_at=row["reviewed_at"].strip(),
        )
        for row in rows
    ]


def _interval(alias: InstrumentAlias) -> tuple[date, date | None]:
    try:
        start = date.fromisoformat(alias.effective_from)
        end = date.fromisoformat(alias.effective_to) if alias.effective_to else None
    except ValueError as exc:
        raise ValueError(
            f"invalid instrument history date: {alias.instrument_id}/{alias.provider}"
        ) from exc
    if end is not None and end < start:
        raise ValueError(
            f"instrument history end precedes start: {alias.instrument_id}/{alias.provider}"
        )
    return start, end


def validate_instrument_history(
    mappings: Iterable[InstrumentMapping],
    aliases: Iterable[InstrumentAlias],
    actions: Iterable[CorporateAction] = (),
) -> None:
    mapping_list = list(mappings)
    alias_list = list(aliases)
    ids = {item.symbol_id for item in mapping_list}
    canonical_by_id = {item.symbol_id: item.ticker for item in mapping_list}
    if len(canonical_by_id) != len(mapping_list):
        raise ValueError("instrument master contains duplicate symbol ids")
    alias_ids = {item.instrument_id for item in alias_list}
    missing = sorted(ids - alias_ids)
    if missing:
        raise ValueError(f"instrument history is missing master ids: {missing}")
    extras = sorted(alias_ids - ids)
    if extras:
        raise ValueError(f"instrument history contains unknown ids: {extras}")

    grouped: dict[tuple[str, str], list[tuple[date, date | None]]] = {}
    for alias in alias_list:
        if not all(
            (
                alias.instrument_id,
                alias.canonical_symbol,
                alias.provider,
                alias.provider_symbol,
                alias.source_url,
                alias.reviewed_at,
            )
        ):
            raise ValueError(f"instrument history evidence is incomplete: {alias}")
        if canonical_by_id[alias.instrument_id] != alias.canonical_symbol:
            raise ValueError(
                f"instrument history canonical symbol mismatch: {alias.instrument_id}"
            )
        grouped.setdefault((alias.instrument_id, alias.provider), []).append(
            _interval(alias)
        )
    for key, intervals in grouped.items():
        ordered = sorted(intervals, key=lambda item: item[0])
        previous_end: date | None = None
        for index, (start, end) in enumerate(ordered):
            if index > 0 and previous_end is None:
                raise ValueError(f"open-ended instrument interval is not last: {key}")
            if previous_end is not None and start <= previous_end:
                raise ValueError(f"overlapping instrument history intervals: {key}")
            previous_end = end

    seen_events: set[str] = set()
    for action in actions:
        if action.event_id in seen_events:
            raise ValueError(f"duplicate corporate action id: {action.event_id}")
        seen_events.add(action.event_id)
        if action.instrument_id not in ids:
            raise ValueError(f"corporate action references unknown instrument: {action.event_id}")
        try:
            effective_date = date.fromisoformat(action.effective_date)
            date.fromisoformat(action.reviewed_at)
        except ValueError as exc:
            raise ValueError(f"invalid corporate action date: {action.event_id}") from exc
        if not action.source_url:
            raise ValueError(f"corporate action lacks source: {action.event_id}")
        if action.event_type == "ticker_change":
            exchange_aliases = [
                item
                for item in alias_list
                if item.instrument_id == action.instrument_id
                and item.provider == "exchange"
            ]
            old_matches = [
                item
                for item in exchange_aliases
                if item.provider_symbol == action.old_symbol
                and item.effective_to
                == (effective_date - timedelta(days=1)).isoformat()
            ]
            new_matches = [
                item
                for item in exchange_aliases
                if item.provider_symbol == action.new_symbol
                and item.effective_from == effective_date.isoformat()
            ]
            if len(old_matches) != 1 or len(new_matches) != 1:
                raise ValueError(
                    f"ticker action does not match exchange history: {action.event_id}"
                )


def resolve_provider_symbol(
    aliases: Iterable[InstrumentAlias],
    *,
    canonical_symbol: str,
    provider: str,
    as_of: str,
) -> str:
    target = canonical_symbol.strip().upper()
    provider_name = provider.strip().lower()
    when = date.fromisoformat(as_of)
    candidates = [
        item
        for item in aliases
        if item.canonical_symbol == target
        and item.provider in {provider_name, "all"}
        and date.fromisoformat(item.effective_from) <= when
        and (item.effective_to is None or when <= date.fromisoformat(item.effective_to))
    ]
    exact = [item for item in candidates if item.provider == provider_name]
    selected = exact or [item for item in candidates if item.provider == "all"]
    if len(selected) != 1:
        raise ValueError(
            f"provider symbol is not uniquely resolvable: {target}/{provider_name}/{as_of}"
        )
    return selected[0].provider_symbol


def build_instrument_lifetime_index(
    mappings: Iterable[InstrumentMapping],
) -> dict[str, tuple[date, date | None]]:
    lifetimes: dict[str, tuple[date, date | None]] = {}
    for mapping in mappings:
        symbol = mapping.ticker.strip().upper()
        if symbol in lifetimes:
            raise ValueError(f"duplicate instrument lifetime: {symbol}")
        lifetimes[symbol] = (
            date.fromisoformat(mapping.listed_from or mapping.effective_from),
            date.fromisoformat(mapping.delisted_on) if mapping.delisted_on else None,
        )
    return lifetimes


def observation_within_instrument_lifetime(
    lifetimes: dict[str, tuple[date, date | None]],
    symbol: str,
    value: str,
) -> bool:
    canonical_symbol = symbol.strip().upper()
    lifetime = lifetimes.get(canonical_symbol)
    if lifetime is None:
        raise ValueError(f"observation has no instrument identity: {symbol}")
    observed = date.fromisoformat(value)
    listed, delisted = lifetime
    return observed >= listed and (delisted is None or observed <= delisted)


def validate_point_in_time_dates(
    mappings: Iterable[InstrumentMapping],
    observations: Iterable[tuple[str, str]],
) -> None:
    lifetimes = build_instrument_lifetime_index(mappings)
    for symbol, value in observations:
        if not observation_within_instrument_lifetime(
            lifetimes,
            symbol,
            value,
        ):
            raise ValueError(
                f"observation falls outside instrument lifetime: {symbol}/{value}"
            )
