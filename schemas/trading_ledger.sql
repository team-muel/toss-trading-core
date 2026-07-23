CREATE TABLE IF NOT EXISTS market_bars (
  ts TEXT NOT NULL,
  available_at TEXT,
  source_ts TEXT,
  exchange_local_date TEXT,
  interval TEXT NOT NULL DEFAULT '1d',
  symbol TEXT NOT NULL,
  venue TEXT,
  currency TEXT,
  session_label TEXT,
  source_timezone TEXT,
  adjustment TEXT NOT NULL DEFAULT 'raw',
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  volume REAL,
  source TEXT NOT NULL,
  source_revision TEXT,
  raw_manifest_id TEXT,
  schema_version TEXT NOT NULL DEFAULT 'market-bars-v1',
  ingested_at TEXT NOT NULL,
  quality_flag TEXT NOT NULL DEFAULT 'ok',
  PRIMARY KEY (ts, symbol, source)
);

CREATE TABLE IF NOT EXISTS instrument_master (
  symbol_id TEXT NOT NULL,
  toss_symbol TEXT,
  ticker TEXT NOT NULL,
  vendor_symbol TEXT,
  occ_symbol TEXT,
  cik TEXT,
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

CREATE TABLE IF NOT EXISTS external_event_log (
  id TEXT PRIMARY KEY,
  event_time_utc TEXT NOT NULL,
  source TEXT NOT NULL,
  source_event_id TEXT,
  symbol TEXT,
  cik TEXT,
  event_type TEXT NOT NULL,
  event_status TEXT,
  source_url TEXT,
  raw_snapshot_ref TEXT,
  quality_flag TEXT NOT NULL DEFAULT 'ok',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_series_observation (
  id TEXT PRIMARY KEY,
  series_id TEXT NOT NULL,
  observation_date TEXT NOT NULL,
  value REAL,
  realtime_start TEXT,
  realtime_end TEXT,
  source TEXT NOT NULL,
  raw_snapshot_ref TEXT,
  quality_flag TEXT NOT NULL DEFAULT 'ok',
  created_at TEXT NOT NULL,
  UNIQUE(series_id, observation_date, realtime_start, realtime_end, source)
);

CREATE TABLE IF NOT EXISTS etf_nav_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  symbol TEXT NOT NULL,
  nav REAL,
  indicative_value REAL,
  market_price REAL,
  premium_discount_pct REAL,
  source TEXT NOT NULL,
  raw_snapshot_ref TEXT,
  quality_flag TEXT NOT NULL DEFAULT 'ok',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS etf_distribution_event (
  id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  declaration_date TEXT,
  ex_date TEXT,
  record_date TEXT,
  pay_date TEXT,
  cash_amount REAL,
  currency TEXT,
  distribution_type TEXT,
  roc_flag INTEGER,
  tax_character_source TEXT,
  source TEXT NOT NULL,
  raw_snapshot_ref TEXT,
  quality_flag TEXT NOT NULL DEFAULT 'ok',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS options_chain_snapshot (
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

CREATE TABLE IF NOT EXISTS feature_snapshot (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  symbol TEXT NOT NULL,
  feature_namespace TEXT NOT NULL,
  feature_name TEXT NOT NULL,
  feature_value REAL,
  feature_text TEXT,
  lookback_window TEXT,
  source TEXT NOT NULL,
  available_at TEXT,
  dataset_manifest_ids TEXT,
  transformation_version TEXT,
  parameters_hash TEXT,
  code_revision TEXT,
  quality_flag TEXT NOT NULL DEFAULT 'ok',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_log (
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

CREATE TABLE IF NOT EXISTS signal_decision_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_seq TEXT,
  engine TEXT NOT NULL,
  symbol TEXT NOT NULL,
  decision TEXT NOT NULL,
  target_weight REAL,
  source_signal_id TEXT,
  source_feature_ids TEXT,
  gate_reason TEXT,
  created_at TEXT NOT NULL,
  CHECK (decision IN ('ALLOW', 'REDUCE', 'BLOCK'))
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
  source_signal_id TEXT,
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

CREATE TABLE IF NOT EXISTS position_log (
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

CREATE TABLE IF NOT EXISTS risk_snapshot (
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
