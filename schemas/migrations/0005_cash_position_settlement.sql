CREATE TABLE IF NOT EXISTS am_cash_opening_balance (
  opening_balance_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  currency TEXT NOT NULL,
  as_of_utc TEXT NOT NULL,
  opening_balance_decimal TEXT NOT NULL,
  evidence TEXT NOT NULL CHECK (length(trim(evidence)) > 0),
  approved_by TEXT NOT NULL CHECK (length(trim(approved_by)) > 0),
  UNIQUE (account_id, currency)
);

CREATE TABLE IF NOT EXISTS am_position_opening_balance (
  position_opening_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  native_currency TEXT NOT NULL,
  as_of_utc TEXT NOT NULL,
  quantity_decimal TEXT NOT NULL,
  average_cost_decimal TEXT NOT NULL,
  evidence TEXT NOT NULL CHECK (length(trim(evidence)) > 0),
  approved_by TEXT NOT NULL CHECK (length(trim(approved_by)) > 0),
  UNIQUE (account_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS am_cash_event_metadata (
  cash_event_id TEXT PRIMARY KEY REFERENCES am_cash_ledger(cash_event_id),
  idempotency_key TEXT NOT NULL UNIQUE,
  reason TEXT,
  approved_by TEXT,
  CHECK (reason IS NULL OR length(trim(reason)) > 0),
  CHECK (approved_by IS NULL OR length(trim(approved_by)) > 0)
);

CREATE TABLE IF NOT EXISTS am_execution_cash_component (
  execution_delta_id TEXT NOT NULL REFERENCES am_execution_delta(execution_delta_id),
  component TEXT NOT NULL CHECK (component IN ('PRINCIPAL', 'COMMISSION', 'TAX')),
  cash_event_id TEXT NOT NULL UNIQUE REFERENCES am_cash_ledger(cash_event_id),
  PRIMARY KEY (execution_delta_id, component)
);

CREATE TABLE IF NOT EXISTS am_position_event_settlement (
  position_event_id TEXT PRIMARY KEY REFERENCES am_position_ledger(position_event_id),
  settlement_date TEXT
);

CREATE TABLE IF NOT EXISTS am_cash_reservation_event (
  reservation_event_id TEXT PRIMARY KEY,
  broker_order_id TEXT NOT NULL REFERENCES am_broker_order(broker_order_id),
  sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
  account_id TEXT NOT NULL,
  currency TEXT NOT NULL,
  reserved_amount_decimal TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('RESERVED', 'RELEASED')),
  source_response_id TEXT NOT NULL REFERENCES am_raw_api_response(raw_response_id),
  observed_at_utc TEXT NOT NULL,
  UNIQUE (broker_order_id, sequence_no),
  UNIQUE (broker_order_id, source_response_id)
);

CREATE TABLE IF NOT EXISTS am_position_reservation_event (
  reservation_event_id TEXT PRIMARY KEY,
  broker_order_id TEXT NOT NULL REFERENCES am_broker_order(broker_order_id),
  sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
  account_id TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  reserved_quantity_decimal TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('RESERVED', 'RELEASED')),
  source_response_id TEXT NOT NULL REFERENCES am_raw_api_response(raw_response_id),
  observed_at_utc TEXT NOT NULL,
  UNIQUE (broker_order_id, sequence_no),
  UNIQUE (broker_order_id, source_response_id)
);

CREATE TABLE IF NOT EXISTS am_tax_lot (
  lot_id TEXT PRIMARY KEY,
  execution_delta_id TEXT UNIQUE REFERENCES am_execution_delta(execution_delta_id),
  account_id TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  acquisition_date TEXT NOT NULL,
  settlement_date TEXT,
  quantity_decimal TEXT NOT NULL,
  price_decimal TEXT NOT NULL,
  commission_decimal TEXT NOT NULL,
  currency TEXT NOT NULL,
  fx_rate_decimal TEXT NOT NULL,
  tax_policy_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_tax_lot_disposal (
  disposal_id TEXT PRIMARY KEY,
  lot_id TEXT NOT NULL REFERENCES am_tax_lot(lot_id),
  execution_delta_id TEXT NOT NULL REFERENCES am_execution_delta(execution_delta_id),
  quantity_decimal TEXT NOT NULL,
  disposal_date TEXT NOT NULL,
  UNIQUE (lot_id, execution_delta_id)
);

CREATE TRIGGER IF NOT EXISTS am_cash_event_type_guard
BEFORE INSERT ON am_cash_ledger
WHEN NEW.event_type NOT IN (
  'DEPOSIT', 'WITHDRAWAL', 'TRADE_COST', 'TRADE_PROCEEDS', 'COMMISSION', 'TAX',
  'DIVIDEND', 'WITHHOLDING', 'INTEREST', 'FX_CONVERSION_IN', 'FX_CONVERSION_OUT',
  'CORPORATE_ACTION_CASH', 'MANUAL_ADJUSTMENT'
)
BEGIN SELECT RAISE(ABORT, 'unsupported cash event type'); END;

CREATE TRIGGER IF NOT EXISTS am_manual_cash_metadata_guard
BEFORE INSERT ON am_cash_event_metadata
WHEN (SELECT event_type FROM am_cash_ledger WHERE cash_event_id=NEW.cash_event_id)='MANUAL_ADJUSTMENT'
 AND (NEW.reason IS NULL OR NEW.approved_by IS NULL)
BEGIN SELECT RAISE(ABORT, 'manual adjustment requires reason and approver'); END;

CREATE TRIGGER IF NOT EXISTS am_cash_opening_no_update BEFORE UPDATE ON am_cash_opening_balance
BEGIN SELECT RAISE(ABORT, 'cash openings are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_cash_opening_no_delete BEFORE DELETE ON am_cash_opening_balance
BEGIN SELECT RAISE(ABORT, 'cash openings are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_position_opening_no_update BEFORE UPDATE ON am_position_opening_balance
BEGIN SELECT RAISE(ABORT, 'position openings are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_position_opening_no_delete BEFORE DELETE ON am_position_opening_balance
BEGIN SELECT RAISE(ABORT, 'position openings are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_cash_metadata_no_update BEFORE UPDATE ON am_cash_event_metadata
BEGIN SELECT RAISE(ABORT, 'cash event metadata is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_cash_metadata_no_delete BEFORE DELETE ON am_cash_event_metadata
BEGIN SELECT RAISE(ABORT, 'cash event metadata is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_execution_component_no_update BEFORE UPDATE ON am_execution_cash_component
BEGIN SELECT RAISE(ABORT, 'execution cash components are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_execution_component_no_delete BEFORE DELETE ON am_execution_cash_component
BEGIN SELECT RAISE(ABORT, 'execution cash components are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_position_settlement_no_update BEFORE UPDATE ON am_position_event_settlement
BEGIN SELECT RAISE(ABORT, 'position settlements are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_position_settlement_no_delete BEFORE DELETE ON am_position_event_settlement
BEGIN SELECT RAISE(ABORT, 'position settlements are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_cash_reservation_no_update BEFORE UPDATE ON am_cash_reservation_event
BEGIN SELECT RAISE(ABORT, 'cash reservations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_cash_reservation_no_delete BEFORE DELETE ON am_cash_reservation_event
BEGIN SELECT RAISE(ABORT, 'cash reservations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_position_reservation_no_update BEFORE UPDATE ON am_position_reservation_event
BEGIN SELECT RAISE(ABORT, 'position reservations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_position_reservation_no_delete BEFORE DELETE ON am_position_reservation_event
BEGIN SELECT RAISE(ABORT, 'position reservations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_tax_lot_no_update BEFORE UPDATE ON am_tax_lot
BEGIN SELECT RAISE(ABORT, 'tax lots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_tax_lot_no_delete BEFORE DELETE ON am_tax_lot
BEGIN SELECT RAISE(ABORT, 'tax lots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_tax_lot_disposal_no_update BEFORE UPDATE ON am_tax_lot_disposal
BEGIN SELECT RAISE(ABORT, 'tax lot disposals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_tax_lot_disposal_no_delete BEFORE DELETE ON am_tax_lot_disposal
BEGIN SELECT RAISE(ABORT, 'tax lot disposals are append-only'); END;
