CREATE TABLE IF NOT EXISTS am_execution_posting_context (
  execution_delta_id TEXT PRIMARY KEY REFERENCES am_execution_delta(execution_delta_id),
  broker_order_id TEXT NOT NULL REFERENCES am_broker_order(broker_order_id),
  account_id TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
  currency TEXT NOT NULL,
  settlement_date TEXT NOT NULL,
  tax_policy_version TEXT NOT NULL,
  fx_rate_decimal TEXT NOT NULL,
  context_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_tax_lot_timing (
  lot_id TEXT PRIMARY KEY REFERENCES am_tax_lot(lot_id),
  observed_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_tax_lot_disposal_timing (
  disposal_id TEXT PRIMARY KEY REFERENCES am_tax_lot_disposal(disposal_id),
  observed_at_utc TEXT NOT NULL
);

-- Authorization is inserted in the same transaction before a manual cash event.
-- It deliberately has no FK so the insert guard can observe it before the event exists.
CREATE TABLE IF NOT EXISTS am_manual_cash_authorization (
  cash_event_id TEXT PRIMARY KEY,
  reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  approved_by TEXT NOT NULL CHECK (length(trim(approved_by)) > 0),
  authorized_at_utc TEXT NOT NULL
);

-- Preserve v5 records using their immutable originating ledger timestamps.
INSERT INTO am_tax_lot_timing (lot_id, observed_at_utc)
SELECT l.lot_id, COALESCE(d.created_at_utc, o.as_of_utc)
FROM am_tax_lot l
LEFT JOIN am_execution_delta d ON d.execution_delta_id=l.execution_delta_id
LEFT JOIN am_position_opening_balance o
  ON l.lot_id=('opening:' || o.position_opening_id)
WHERE COALESCE(d.created_at_utc, o.as_of_utc) IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM am_tax_lot_timing t WHERE t.lot_id=l.lot_id);

INSERT INTO am_tax_lot_disposal_timing (disposal_id, observed_at_utc)
SELECT x.disposal_id, d.created_at_utc
FROM am_tax_lot_disposal x
JOIN am_execution_delta d ON d.execution_delta_id=x.execution_delta_id
WHERE NOT EXISTS (
  SELECT 1 FROM am_tax_lot_disposal_timing t WHERE t.disposal_id=x.disposal_id
);

INSERT INTO am_manual_cash_authorization
  (cash_event_id, reason, approved_by, authorized_at_utc)
SELECT l.cash_event_id, m.reason, m.approved_by, l.created_at_utc
FROM am_cash_ledger l JOIN am_cash_event_metadata m USING(cash_event_id)
WHERE l.event_type='MANUAL_ADJUSTMENT'
  AND m.reason IS NOT NULL AND m.approved_by IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM am_manual_cash_authorization a WHERE a.cash_event_id=l.cash_event_id
  );

CREATE TRIGGER IF NOT EXISTS am_manual_cash_insert_guard
BEFORE INSERT ON am_cash_ledger
WHEN NEW.event_type='MANUAL_ADJUSTMENT'
 AND NOT EXISTS (
   SELECT 1 FROM am_manual_cash_authorization a
   WHERE a.cash_event_id=NEW.cash_event_id
 )
BEGIN SELECT RAISE(ABORT, 'manual adjustment requires prior authorization'); END;

CREATE TRIGGER IF NOT EXISTS am_posting_context_no_update
BEFORE UPDATE ON am_execution_posting_context
BEGIN SELECT RAISE(ABORT, 'execution posting contexts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_posting_context_no_delete
BEFORE DELETE ON am_execution_posting_context
BEGIN SELECT RAISE(ABORT, 'execution posting contexts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_tax_lot_timing_no_update
BEFORE UPDATE ON am_tax_lot_timing
BEGIN SELECT RAISE(ABORT, 'tax lot timing is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_tax_lot_timing_no_delete
BEFORE DELETE ON am_tax_lot_timing
BEGIN SELECT RAISE(ABORT, 'tax lot timing is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_disposal_timing_no_update
BEFORE UPDATE ON am_tax_lot_disposal_timing
BEGIN SELECT RAISE(ABORT, 'tax disposal timing is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_disposal_timing_no_delete
BEFORE DELETE ON am_tax_lot_disposal_timing
BEGIN SELECT RAISE(ABORT, 'tax disposal timing is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_manual_authorization_no_update
BEFORE UPDATE ON am_manual_cash_authorization
BEGIN SELECT RAISE(ABORT, 'manual authorizations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_manual_authorization_no_delete
BEFORE DELETE ON am_manual_cash_authorization
BEGIN SELECT RAISE(ABORT, 'manual authorizations are append-only'); END;
