CREATE TABLE IF NOT EXISTS instrument_master (
  symbol_id TEXT NOT NULL,
  toss_symbol TEXT,
  ticker TEXT NOT NULL,
  asset_class TEXT NOT NULL,
  currency TEXT NOT NULL,
  timezone TEXT,
  mic TEXT,
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (symbol_id, effective_from)
);

CREATE TABLE IF NOT EXISTS raw_api_response (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  source_type TEXT NOT NULL,
  account_seq TEXT,
  channel TEXT,
  endpoint TEXT NOT NULL,
  http_method TEXT NOT NULL,
  request_id TEXT,
  request_hash TEXT,
  response_hash TEXT NOT NULL,
  status_code INTEGER,
  rate_limit_limit TEXT,
  rate_limit_remaining TEXT,
  rate_limit_reset TEXT,
  body_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  account_no_masked TEXT,
  account_type TEXT,
  broker TEXT NOT NULL DEFAULT 'toss',
  raw_response_ref TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holding_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  symbol TEXT NOT NULL,
  quantity REAL NOT NULL,
  average_purchase_price REAL,
  last_price REAL,
  market_value REAL,
  profit_loss REAL,
  cost REAL,
  currency TEXT,
  raw_response_ref TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS broker_order_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  broker_order_id TEXT NOT NULL,
  client_order_id TEXT,
  symbol TEXT NOT NULL,
  side TEXT,
  order_type TEXT,
  time_in_force TEXT,
  status TEXT NOT NULL,
  quantity REAL,
  order_amount REAL,
  price REAL,
  cumulative_filled_qty REAL,
  cumulative_filled_amount REAL,
  average_filled_price REAL,
  cumulative_commission REAL,
  cumulative_tax REAL,
  settlement_date TEXT,
  ordered_at TEXT,
  canceled_at TEXT,
  raw_response_ref TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS buying_power_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  currency TEXT NOT NULL,
  cash_buying_power REAL,
  raw_response_ref TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sellable_quantity_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  symbol TEXT NOT NULL,
  sellable_quantity REAL,
  raw_response_ref TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commission_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  market TEXT,
  symbol TEXT,
  side TEXT,
  order_amount REAL,
  commission_amount REAL,
  currency TEXT,
  raw_response_ref TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_health_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  channel TEXT NOT NULL,
  last_success_at TEXT,
  max_age_ms INTEGER,
  heartbeat_timeout_ms INTEGER,
  lag_ms INTEGER,
  dropped_events INTEGER DEFAULT 0,
  source_status TEXT NOT NULL,
  action TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  broker TEXT NOT NULL,
  mode TEXT NOT NULL,
  client_order_id TEXT NOT NULL,
  broker_order_id TEXT,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  order_basis TEXT NOT NULL,
  qty REAL,
  order_amount REAL,
  limit_px REAL,
  order_type TEXT,
  time_in_force TEXT,
  status TEXT NOT NULL,
  reject_code TEXT,
  raw_request_ref TEXT,
  raw_response_ref TEXT,
  created_at TEXT NOT NULL,
  CHECK (
    (order_basis = 'quantity' AND qty IS NOT NULL AND order_amount IS NULL)
    OR
    (order_basis = 'amount' AND order_amount IS NOT NULL AND qty IS NULL)
  )
);

CREATE TABLE IF NOT EXISTS execution_snapshot_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  order_id TEXT NOT NULL,
  broker_order_id TEXT,
  snapshot_seq INTEGER NOT NULL,
  order_status TEXT NOT NULL,
  cumulative_filled_qty REAL NOT NULL,
  cumulative_filled_amount REAL NOT NULL,
  average_filled_price REAL,
  cumulative_commission REAL DEFAULT 0,
  cumulative_tax REAL DEFAULT 0,
  settlement_date TEXT,
  raw_snapshot_ref TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(order_id, snapshot_seq)
);

CREATE TABLE IF NOT EXISTS execution_delta_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  order_id TEXT NOT NULL,
  broker_order_id TEXT,
  from_snapshot_id TEXT,
  to_snapshot_id TEXT NOT NULL,
  delta_filled_qty REAL NOT NULL,
  delta_filled_amount REAL NOT NULL,
  delta_commission REAL DEFAULT 0,
  delta_tax REAL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tax_lot_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  symbol TEXT NOT NULL,
  lot_id TEXT NOT NULL,
  acquisition_ts TEXT NOT NULL,
  acquisition_settlement_date TEXT,
  acquisition_qty REAL NOT NULL,
  acquisition_price REAL NOT NULL,
  acquisition_amount REAL NOT NULL,
  acquisition_commission REAL DEFAULT 0,
  acquisition_tax REAL DEFAULT 0,
  acquisition_fx_rate REAL,
  remaining_qty REAL NOT NULL,
  disposal_ts TEXT,
  disposal_settlement_date TEXT,
  disposal_qty REAL,
  disposal_price REAL,
  disposal_amount REAL,
  disposal_commission REAL DEFAULT 0,
  disposal_tax REAL DEFAULT 0,
  disposal_fx_rate REAL,
  realized_pnl_native REAL,
  realized_pnl_krw_estimate REAL,
  dividend_gross_native REAL DEFAULT 0,
  withholding_native REAL DEFAULT 0,
  roc_adjustment_native REAL DEFAULT 0,
  basis_adjustment_native REAL DEFAULT 0,
  tax_status TEXT NOT NULL DEFAULT 'open',
  source_order_id TEXT,
  source_execution_snapshot_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_ledger (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  currency TEXT NOT NULL,
  event_type TEXT NOT NULL,
  amount REAL NOT NULL,
  amount_decimal TEXT NOT NULL,
  settlement_date TEXT,
  source_ref TEXT,
  tax_relevant INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_ledger_source_event
  ON cash_ledger(source_ref, event_type)
  WHERE source_ref IS NOT NULL;

CREATE TABLE IF NOT EXISTS cash_ledger_genesis (
  account_seq TEXT NOT NULL,
  currency TEXT NOT NULL,
  as_of TEXT NOT NULL,
  opening_balance REAL NOT NULL,
  opening_balance_decimal TEXT NOT NULL,
  evidence_ref TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (account_seq, currency)
);

CREATE TABLE IF NOT EXISTS broker_reconciliation_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  item_type TEXT NOT NULL,
  broker_value TEXT,
  internal_value TEXT,
  difference TEXT,
  status TEXT NOT NULL,
  action_required TEXT,
  resolved_at TEXT,
  resolution_note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS client_order_id_registry (
  client_order_id TEXT PRIMARY KEY,
  account_seq TEXT NOT NULL,
  first_used_at TEXT NOT NULL,
  broker_order_id TEXT,
  final_status TEXT,
  reuse_forbidden INTEGER NOT NULL DEFAULT 1,
  raw_request_ref TEXT,
  raw_response_ref TEXT
);
