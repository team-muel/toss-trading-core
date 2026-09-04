from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import sqlite3
from typing import Mapping, Sequence

from asset_management.broker.contracts import BrokerSnapshot


@dataclass(frozen=True, slots=True)
class AccountTruthSnapshot:
    runtime_run_id: str
    account_id: str
    observed_at_utc: datetime
    accounts: Sequence[Mapping[str, object]]
    holdings: Sequence[Mapping[str, object]]
    open_orders: Sequence[Mapping[str, object]]
    closed_orders: Sequence[Mapping[str, object]]
    order_details: Sequence[Mapping[str, object]]
    buying_power: Sequence[Mapping[str, object]]
    sellable_quantities: Sequence[Mapping[str, object]]
    commissions: Sequence[Mapping[str, object]]
    market_calendars: Sequence[object]
    instrument_reference: object
    raw_response_ids: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "accounts": self.accounts,
            "holdings": self.holdings,
            "open_orders": self.open_orders,
            "closed_orders": self.closed_orders,
            "order_details": self.order_details,
            "buying_power": self.buying_power,
            "sellable_quantities": self.sellable_quantities,
            "commissions": self.commissions,
            "market_calendars": self.market_calendars,
            "instrument_reference": self.instrument_reference,
            "raw_response_ids": self.raw_response_ids,
        }

    def comparison_payload(self) -> dict[str, object]:
        payload = self.payload()
        payload.pop("raw_response_ids")
        return payload

    @property
    def content_hash(self) -> str:
        encoded = json.dumps(self.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class AccountTruthRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(self, snapshot: AccountTruthSnapshot) -> str:
        if not snapshot.raw_response_ids:
            raise ValueError("account truth requires raw response evidence")
        identifier = f"account-truth:{snapshot.runtime_run_id}:{snapshot.content_hash}"
        payload = json.dumps(snapshot.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO am_account_snapshot (
                  account_snapshot_id, runtime_run_id, account_id, observed_at_utc,
                  source_response_id, payload_json, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier, snapshot.runtime_run_id, snapshot.account_id,
                    snapshot.observed_at_utc.isoformat(), snapshot.raw_response_ids[0],
                    payload, snapshot.content_hash,
                ),
            )
            self._conn.executemany(
                "INSERT INTO am_account_snapshot_raw(account_snapshot_id, raw_response_id) VALUES (?, ?)",
                ((identifier, raw_id) for raw_id in snapshot.raw_response_ids),
            )
        return identifier

__all__ = ["AccountTruthRepository", "AccountTruthSnapshot", "BrokerSnapshot"]
