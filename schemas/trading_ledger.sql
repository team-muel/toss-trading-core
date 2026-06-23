CREATE TABLE market_bars (
  ts TEXT NOT NULL,
  symbol TEXT NOT NULL,
  venue TEXT,
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  volume REAL,
  source TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  quality_flag TEXT NOT NULL DEFAULT 'ok',
  PRIMARY KEY (ts, symbol, source)
);

CREATE TABLE options_chain_snapshot (
  ts TEXT NOT NULL,
  engine TEXT NOT NULL DEFAULT 'option_carry',
  underlying TEXT NOT NULL,
  expiry TEXT NOT NULL,
  strike REAL NOT NULL,
  cp TEXT NOT NULL,
  bid REAL,
  ask REAL,
  mid REAL,
  iv REAL,
  delta REAL,
  gamma REAL,
  vega REAL,
  oi REAL,
  volume REAL,
  source TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  quality_flag TEXT NOT NULL DEFAULT 'ok',
  PRIMARY KEY (ts, underlying, expiry, strike, cp, source)
);

CREATE TABLE signal_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT,
  engine TEXT NOT NULL,
  symbol_or_pair TEXT NOT NULL,
  regime_tag TEXT,
  raw_score REAL,
  adjusted_score REAL,
  signal_side TEXT NOT NULL,
  target_weight REAL,
  expected_max_loss REAL,
  reason_code TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE order_log (
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
  source_signal_id TEXT,
  created_at TEXT NOT NULL,
  CHECK (
    (order_basis = 'quantity' AND qty IS NOT NULL AND order_amount IS NULL)
    OR
    (order_basis = 'amount' AND order_amount IS NOT NULL AND qty IS NULL)
  )
);

CREATE TABLE execution_snapshot_log (
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

CREATE TABLE execution_delta_log (
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

CREATE TABLE position_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  engine TEXT NOT NULL,
  position_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  net_qty REAL NOT NULL,
  delta REAL,
  vega REAL,
  max_loss REAL,
  collateral_reserved REAL,
  unrealized_pnl REAL,
  created_at TEXT NOT NULL
);

CREATE TABLE risk_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  portfolio_nav REAL NOT NULL,
  risk_nav REAL,
  estimated_cash_balance REAL NOT NULL,
  broker_cash_buying_power_constraint REAL,
  pending_settlement_cash REAL,
  reserved_cash_open_orders REAL,
  bill_ladder REAL,
  margin_used REAL,
  kill_switch_state TEXT,
  stress_2008 REAL,
  stress_2020 REAL,
  stress_2022 REAL,
  stress_2024 REAL,
  engine_pnl_corr_hash TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE tax_lot_log (
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

CREATE TABLE cash_ledger (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  currency TEXT NOT NULL,
  event_type TEXT NOT NULL,
  amount REAL NOT NULL,
  settlement_date TEXT,
  source_ref TEXT,
  tax_relevant INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE broker_reconciliation_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT NOT NULL,
  item_type TEXT NOT NULL,
  broker_value TEXT,
  internal_value TEXT,
  difference TEXT,
  status TEXT NOT NULL,
  action_required TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE raw_broker_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT,
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

CREATE TABLE client_order_id_registry (
  client_order_id TEXT PRIMARY KEY,
  account_seq TEXT NOT NULL,
  first_used_at TEXT NOT NULL,
  broker_order_id TEXT,
  final_status TEXT,
  reuse_forbidden INTEGER NOT NULL DEFAULT 1,
  raw_request_ref TEXT,
  raw_response_ref TEXT
);
