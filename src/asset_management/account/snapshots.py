from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Mapping, Sequence

from asset_management.domain.errors import InvariantViolation, TemporalViolation


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

    def __post_init__(self) -> None:
        if not self.runtime_run_id.strip() or not self.account_id.strip():
            raise InvariantViolation("account truth requires runtime and account identifiers")
        if self.observed_at_utc.tzinfo is None:
            raise TemporalViolation("account truth observation time must be timezone-aware")
        object.__setattr__(self, "observed_at_utc", self.observed_at_utc.astimezone(timezone.utc))
        if (not self.raw_response_ids or any(not item.strip() for item in self.raw_response_ids)
                or len(self.raw_response_ids) != len(set(self.raw_response_ids))):
            raise InvariantViolation("account truth requires unique raw-response evidence")

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
        runtime = self._conn.execute(
            "SELECT as_of_utc FROM am_runtime_run WHERE runtime_run_id=?",
            (snapshot.runtime_run_id,),
        ).fetchone()
        if runtime is None:
            raise InvariantViolation("account truth runtime does not exist")
        try:
            runtime_as_of = datetime.fromisoformat(str(runtime[0]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise TemporalViolation("runtime as-of timestamp is invalid") from exc
        if runtime_as_of.tzinfo is None:
            raise TemporalViolation("runtime as-of timestamp must be timezone-aware")
        if snapshot.observed_at_utc > runtime_as_of.astimezone(timezone.utc):
            raise TemporalViolation("account truth observation is after runtime as-of")
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

__all__ = ["AccountTruthRepository", "AccountTruthSnapshot"]
