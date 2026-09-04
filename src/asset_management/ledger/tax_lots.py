from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from asset_management.domain.errors import ReconciliationError
from asset_management.ledger.cash import exact


@dataclass(frozen=True, slots=True)
class TaxLot:
    lot_id: str
    account_id: str
    instrument_id: str
    acquisition_date: date
    settlement_date: date | None
    quantity: Decimal
    price: Decimal
    commission: Decimal
    currency: str
    fx_rate: Decimal
    remaining_quantity: Decimal


class TaxLotLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def acquire(self, *, execution_delta_id: str, account_id: str,
                instrument_id: str, acquisition_date: date,
                settlement_date: date | None, quantity: object, price: object,
                commission: object, currency: str, fx_rate: object,
                tax_policy_version: str) -> str:
        lot_quantity = exact(quantity, "lot quantity")
        lot_price = exact(price, "lot price")
        lot_commission = exact(commission, "lot commission")
        lot_fx = exact(fx_rate, "lot fx rate")
        if lot_quantity <= 0 or lot_price < 0 or lot_commission < 0 or lot_fx <= 0:
            raise ReconciliationError("tax-lot quantity, price, commission, or FX rate is invalid")
        if not tax_policy_version:
            raise ReconciliationError("tax policy version is required")
        lot_id = str(uuid5(NAMESPACE_URL, f"lot:{execution_delta_id}"))
        self._conn.execute(
            """INSERT OR IGNORE INTO am_tax_lot VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (lot_id, execution_delta_id, account_id, instrument_id,
             acquisition_date.isoformat(), settlement_date.isoformat() if settlement_date else None,
             format(lot_quantity, "f"), format(lot_price, "f"),
             format(lot_commission, "f"), currency.upper(),
             format(lot_fx, "f"), tax_policy_version),
        )
        return lot_id

    def lots(self, *, account_id: str, instrument_id: str) -> tuple[TaxLot, ...]:
        result = []
        rows = self._conn.execute(
            """SELECT lot_id, acquisition_date, settlement_date, quantity_decimal,
                      price_decimal, commission_decimal, currency, fx_rate_decimal
               FROM am_tax_lot WHERE account_id=? AND instrument_id=?
               ORDER BY acquisition_date, lot_id""", (account_id, instrument_id),
        )
        for row in rows:
            disposed_rows = self._conn.execute(
                "SELECT quantity_decimal FROM am_tax_lot_disposal WHERE lot_id=?",
                (row[0],),
            )
            disposed = sum((Decimal(item[0]) for item in disposed_rows), Decimal(0))
            quantity = Decimal(row[3])
            result.append(TaxLot(row[0], account_id, instrument_id,
                                 date.fromisoformat(row[1]),
                                 date.fromisoformat(row[2]) if row[2] else None,
                                 quantity, Decimal(row[4]), Decimal(row[5]), row[6],
                                 Decimal(row[7]), quantity - disposed))
        return tuple(result)

    def dispose_fifo(self, *, execution_delta_id: str, account_id: str,
                     instrument_id: str, quantity: object,
                     disposal_date: date) -> tuple[str, ...]:
        remaining = exact(quantity, "disposal quantity")
        if remaining <= 0:
            raise ReconciliationError("disposal quantity must be positive")
        existing = tuple(row[0] for row in self._conn.execute(
            "SELECT disposal_id FROM am_tax_lot_disposal WHERE execution_delta_id=?",
            (execution_delta_id,),
        ))
        if existing:
            return existing
        plans = []
        for lot in self.lots(account_id=account_id, instrument_id=instrument_id):
            take = min(remaining, lot.remaining_quantity)
            if take > 0:
                plans.append((lot.lot_id, take))
                remaining -= take
            if remaining == 0:
                break
        if remaining != 0:
            raise ReconciliationError("sell execution exceeds available tax lots")
        ids = []
        for lot_id, take in plans:
            disposal_id = str(uuid5(NAMESPACE_URL, f"disposal:{execution_delta_id}:{lot_id}"))
            self._conn.execute(
                "INSERT INTO am_tax_lot_disposal VALUES (?, ?, ?, ?, ?)",
                (disposal_id, lot_id, execution_delta_id, format(take, "f"),
                 disposal_date.isoformat()),
            )
            ids.append(disposal_id)
        return tuple(ids)
