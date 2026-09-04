CREATE TABLE IF NOT EXISTS am_order_state_event (
  order_state_event_id TEXT PRIMARY KEY,
  broker_order_id TEXT NOT NULL REFERENCES am_broker_order(broker_order_id),
  sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
  previous_state TEXT,
  state TEXT NOT NULL CHECK (state IN (
    'PLANNED', 'SUBMITTING', 'SUBMITTED', 'ACKNOWLEDGED', 'OPEN',
    'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING', 'CANCELED',
    'REPLACE_PENDING', 'REPLACED', 'REJECTED', 'UNKNOWN', 'REVIEW_REQUIRED'
  )),
  observed_at_utc TEXT NOT NULL,
  source_response_id TEXT REFERENCES am_raw_api_response(raw_response_id),
  reason TEXT,
  UNIQUE (broker_order_id, sequence_no),
  UNIQUE (broker_order_id, source_response_id)
);

CREATE TABLE IF NOT EXISTS am_execution_snapshot (
  execution_snapshot_id TEXT PRIMARY KEY,
  broker_order_id TEXT NOT NULL REFERENCES am_broker_order(broker_order_id),
  sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
  cumulative_quantity_decimal TEXT NOT NULL,
  cumulative_amount_decimal TEXT NOT NULL,
  average_price_decimal TEXT,
  cumulative_commission_decimal TEXT NOT NULL,
  cumulative_tax_decimal TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL,
  source_response_id TEXT NOT NULL REFERENCES am_raw_api_response(raw_response_id),
  content_hash TEXT NOT NULL,
  UNIQUE (broker_order_id, sequence_no),
  UNIQUE (broker_order_id, content_hash)
);

CREATE TABLE IF NOT EXISTS am_execution_delta (
  execution_delta_id TEXT PRIMARY KEY,
  broker_order_id TEXT NOT NULL REFERENCES am_broker_order(broker_order_id),
  from_snapshot_id TEXT REFERENCES am_execution_snapshot(execution_snapshot_id),
  to_snapshot_id TEXT NOT NULL UNIQUE REFERENCES am_execution_snapshot(execution_snapshot_id),
  quantity_decimal TEXT NOT NULL,
  amount_decimal TEXT NOT NULL,
  commission_decimal TEXT NOT NULL,
  tax_decimal TEXT NOT NULL,
  created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_execution_posting (
  execution_delta_id TEXT PRIMARY KEY REFERENCES am_execution_delta(execution_delta_id),
  cash_event_id TEXT UNIQUE REFERENCES am_cash_ledger(cash_event_id),
  position_event_id TEXT UNIQUE REFERENCES am_position_ledger(position_event_id),
  posted_at_utc TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS am_order_state_no_update
BEFORE UPDATE ON am_order_state_event BEGIN SELECT RAISE(ABORT, 'order states are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_order_state_no_delete
BEFORE DELETE ON am_order_state_event BEGIN SELECT RAISE(ABORT, 'order states are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_execution_snapshot_no_update
BEFORE UPDATE ON am_execution_snapshot BEGIN SELECT RAISE(ABORT, 'execution snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_execution_snapshot_no_delete
BEFORE DELETE ON am_execution_snapshot BEGIN SELECT RAISE(ABORT, 'execution snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_execution_delta_no_update
BEFORE UPDATE ON am_execution_delta BEGIN SELECT RAISE(ABORT, 'execution deltas are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_execution_delta_no_delete
BEFORE DELETE ON am_execution_delta BEGIN SELECT RAISE(ABORT, 'execution deltas are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_cash_ledger_no_update
BEFORE UPDATE ON am_cash_ledger BEGIN SELECT RAISE(ABORT, 'cash ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_cash_ledger_no_delete
BEFORE DELETE ON am_cash_ledger BEGIN SELECT RAISE(ABORT, 'cash ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_position_ledger_no_update
BEFORE UPDATE ON am_position_ledger BEGIN SELECT RAISE(ABORT, 'position ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_position_ledger_no_delete
BEFORE DELETE ON am_position_ledger BEGIN SELECT RAISE(ABORT, 'position ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_execution_posting_no_update
BEFORE UPDATE ON am_execution_posting BEGIN SELECT RAISE(ABORT, 'execution postings are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_execution_posting_no_delete
BEFORE DELETE ON am_execution_posting BEGIN SELECT RAISE(ABORT, 'execution postings are append-only'); END;
