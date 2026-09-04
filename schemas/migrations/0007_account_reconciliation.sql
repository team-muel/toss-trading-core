CREATE TABLE IF NOT EXISTS am_reconciliation_tolerance_policy (
  tolerance_policy_version TEXT PRIMARY KEY,
  effective_from_utc TEXT NOT NULL,
  effective_to_utc TEXT,
  approved_by TEXT NOT NULL CHECK (length(trim(approved_by)) > 0),
  approval_reason TEXT NOT NULL CHECK (length(trim(approval_reason)) > 0),
  rules_json TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS am_account_reconciliation_v2 (
  reconciliation_run_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  account_snapshot_id TEXT NOT NULL REFERENCES am_account_snapshot(account_snapshot_id),
  account_id TEXT NOT NULL,
  tolerance_policy_version TEXT NOT NULL
    REFERENCES am_reconciliation_tolerance_policy(tolerance_policy_version),
  as_of_utc TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN (
    'MATCH', 'TOLERANCE_MATCH', 'MISMATCH', 'UNVERIFIABLE', 'BLOCKED'
  )),
  content_hash TEXT NOT NULL UNIQUE,
  completed_at_utc TEXT NOT NULL,
  UNIQUE (runtime_run_id, account_snapshot_id, tolerance_policy_version)
);

CREATE TABLE IF NOT EXISTS am_reconciliation_item_v2 (
  reconciliation_item_id TEXT PRIMARY KEY,
  reconciliation_run_id TEXT NOT NULL
    REFERENCES am_account_reconciliation_v2(reconciliation_run_id),
  target TEXT NOT NULL CHECK (target IN (
    'HOLDINGS', 'CASH', 'OPEN_ORDERS', 'ORDER_STATE', 'CUMULATIVE_EXECUTION',
    'COMMISSION', 'TAX', 'SETTLEMENT_DATE', 'SELLABLE_QUANTITY', 'BUYING_POWER'
  )),
  dimension_key TEXT NOT NULL,
  broker_value TEXT,
  internal_value TEXT,
  difference_decimal TEXT,
  tolerance_decimal TEXT,
  status TEXT NOT NULL CHECK (status IN (
    'MATCH', 'TOLERANCE_MATCH', 'MISMATCH', 'UNVERIFIABLE', 'BLOCKED'
  )),
  action_required TEXT,
  UNIQUE (reconciliation_run_id, target, dimension_key)
);

CREATE TABLE IF NOT EXISTS am_reconciliation_issue_v2 (
  issue_id TEXT PRIMARY KEY,
  detected_reconciliation_run_id TEXT NOT NULL
    REFERENCES am_account_reconciliation_v2(reconciliation_run_id),
  account_id TEXT NOT NULL,
  target TEXT NOT NULL,
  dimension_key TEXT NOT NULL,
  detected_at_utc TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('MISMATCH', 'UNVERIFIABLE', 'BLOCKED')),
  difference TEXT NOT NULL,
  action_required TEXT NOT NULL,
  UNIQUE (detected_reconciliation_run_id, target, dimension_key)
);

CREATE TABLE IF NOT EXISTS am_reconciliation_resolution_v2 (
  issue_id TEXT PRIMARY KEY REFERENCES am_reconciliation_issue_v2(issue_id),
  evidence_reconciliation_run_id TEXT NOT NULL
    REFERENCES am_account_reconciliation_v2(reconciliation_run_id),
  resolved_at_utc TEXT NOT NULL,
  resolution_note TEXT NOT NULL CHECK (length(trim(resolution_note)) > 0),
  approved_by TEXT NOT NULL CHECK (length(trim(approved_by)) > 0)
);

-- Inserted immediately before an order intent in the same transaction. It has no
-- order-intent FK because the database guard must see it before that row exists.
CREATE TABLE IF NOT EXISTS am_order_intent_reconciliation_authorization (
  order_intent_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  reconciliation_run_id TEXT NOT NULL
    REFERENCES am_account_reconciliation_v2(reconciliation_run_id),
  authorized_at_utc TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS am_order_intent_reconciliation_guard
BEFORE INSERT ON am_order_intent
WHEN NOT EXISTS (
  SELECT 1
  FROM am_order_intent_reconciliation_authorization a
  JOIN am_account_reconciliation_v2 rr
    ON rr.reconciliation_run_id=a.reconciliation_run_id
  WHERE a.order_intent_id=NEW.order_intent_id
    AND rr.account_id=a.account_id
    AND rr.reconciliation_run_id=(
      SELECT newest.reconciliation_run_id
      FROM am_account_reconciliation_v2 newest
      WHERE newest.account_id=a.account_id
      ORDER BY newest.completed_at_utc DESC, newest.reconciliation_run_id DESC LIMIT 1
    )
    AND NOT EXISTS (
      SELECT 1 FROM am_reconciliation_item_v2 item
      WHERE item.reconciliation_run_id=rr.reconciliation_run_id
        AND item.status IN ('MISMATCH','UNVERIFIABLE','BLOCKED')
    )
    AND NOT EXISTS (
      SELECT 1 FROM am_reconciliation_issue_v2 issue
      LEFT JOIN am_reconciliation_resolution_v2 resolution USING(issue_id)
      WHERE issue.account_id=a.account_id AND resolution.issue_id IS NULL
    )
)
BEGIN SELECT RAISE(ABORT, 'order intent requires current reconciled account authorization'); END;

CREATE INDEX IF NOT EXISTS am_reconciliation_open_issue_idx
ON am_reconciliation_issue_v2(account_id, target, dimension_key);

CREATE VIEW IF NOT EXISTS am_reconciliation_issue_status_v2 AS
SELECT i.issue_id, i.account_id, i.target, i.dimension_key, i.detected_at_utc,
       CASE WHEN r.issue_id IS NULL THEN i.status ELSE 'RESOLVED' END AS status,
       i.difference, i.action_required, r.resolved_at_utc,
       r.resolution_note, r.approved_by, r.evidence_reconciliation_run_id
FROM am_reconciliation_issue_v2 i
LEFT JOIN am_reconciliation_resolution_v2 r USING(issue_id);

CREATE TRIGGER IF NOT EXISTS am_tolerance_policy_no_update BEFORE UPDATE ON am_reconciliation_tolerance_policy
BEGIN SELECT RAISE(ABORT, 'reconciliation tolerance policies are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_tolerance_policy_no_delete BEFORE DELETE ON am_reconciliation_tolerance_policy
BEGIN SELECT RAISE(ABORT, 'reconciliation tolerance policies are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_reconciliation_v2_no_update BEFORE UPDATE ON am_account_reconciliation_v2
BEGIN SELECT RAISE(ABORT, 'reconciliation runs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_reconciliation_v2_no_delete BEFORE DELETE ON am_account_reconciliation_v2
BEGIN SELECT RAISE(ABORT, 'reconciliation runs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_reconciliation_item_v2_no_update BEFORE UPDATE ON am_reconciliation_item_v2
BEGIN SELECT RAISE(ABORT, 'reconciliation items are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_reconciliation_item_v2_no_delete BEFORE DELETE ON am_reconciliation_item_v2
BEGIN SELECT RAISE(ABORT, 'reconciliation items are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_reconciliation_issue_v2_no_update BEFORE UPDATE ON am_reconciliation_issue_v2
BEGIN SELECT RAISE(ABORT, 'reconciliation issues are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_reconciliation_issue_v2_no_delete BEFORE DELETE ON am_reconciliation_issue_v2
BEGIN SELECT RAISE(ABORT, 'reconciliation issues are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_reconciliation_resolution_v2_no_update BEFORE UPDATE ON am_reconciliation_resolution_v2
BEGIN SELECT RAISE(ABORT, 'reconciliation resolutions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_reconciliation_resolution_v2_no_delete BEFORE DELETE ON am_reconciliation_resolution_v2
BEGIN SELECT RAISE(ABORT, 'reconciliation resolutions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_order_reconciliation_auth_no_update
BEFORE UPDATE ON am_order_intent_reconciliation_authorization
BEGIN SELECT RAISE(ABORT, 'order reconciliation authorizations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_order_reconciliation_auth_no_delete
BEFORE DELETE ON am_order_intent_reconciliation_authorization
BEGIN SELECT RAISE(ABORT, 'order reconciliation authorizations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_account_snapshot_raw_no_update
BEFORE UPDATE ON am_account_snapshot_raw
BEGIN SELECT RAISE(ABORT, 'account snapshot raw lineage is append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_account_snapshot_raw_no_delete
BEFORE DELETE ON am_account_snapshot_raw
BEGIN SELECT RAISE(ABORT, 'account snapshot raw lineage is append-only'); END;
