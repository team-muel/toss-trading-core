"""Read finalized historical SQLite evidence without opening a writer.

The input must be a quiescent snapshot, not a running account database. Active
journals are rejected, never checkpointed/deleted by the reader. immutable=1
avoids creating WAL/SHM files even for a finalized WAL-format archive.
"""
from pathlib import Path
import sqlite3
from .ledger import AccountLedger


class HistoricalAccountReader:
    def __init__(self, db_path: str | Path):
        path = Path(db_path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("historical evidence must be a regular file")
        for suffix in ("-wal", "-journal"):
            journal = Path(str(path) + suffix)
            if journal.exists() and journal.stat().st_size:
                raise ValueError("historical evidence requires a finalized snapshot without an active journal")
        self.conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only = ON")
        self.conn.execute("PRAGMA trusted_schema = OFF")

    def close(self):
        self.conn.close()

    # Reuse audited decoding/calculation logic without exposing collect/write APIs.
    latest_complete_run = AccountLedger.latest_complete_run
    explain_account_state = AccountLedger.explain_account_state
    cash_event_gaps = AccountLedger.cash_event_gaps
    reserved_open_buy_cash = AccountLedger.reserved_open_buy_cash
