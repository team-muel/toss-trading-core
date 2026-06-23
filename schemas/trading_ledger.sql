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
  broker TEXT NOT NULL,
  mode TEXT NOT NULL,
  client_order_id TEXT NOT NULL,
  broker_order_id TEXT,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  order_amount REAL,
  limit_px REAL,
  status TEXT NOT NULL,
  reject_code TEXT,
  source_signal_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE fill_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  order_id TEXT NOT NULL,
  fill_qty REAL NOT NULL,
  fill_px REAL NOT NULL,
  fees REAL DEFAULT 0,
  tax REAL DEFAULT 0,
  settlement_date TEXT,
  slippage_vs_model REAL,
  created_at TEXT NOT NULL
);

CREATE TABLE position_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
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
  portfolio_nav REAL NOT NULL,
  risk_nav REAL,
  estimated_cash_balance REAL NOT NULL,
  broker_cash_buying_power REAL,
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
  symbol TEXT NOT NULL,
  lot_id TEXT NOT NULL,
  open_fx_rate REAL,
  close_fx_rate REAL,
  dividend_gross REAL,
  withholding REAL,
  roc_adjustment REAL,
  created_at TEXT NOT NULL
);

CREATE TABLE cash_ledger (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_id TEXT NOT NULL,
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
  account_id TEXT NOT NULL,
  item_type TEXT NOT NULL,
  broker_value TEXT,
  internal_value TEXT,
  difference TEXT,
  status TEXT NOT NULL,
  action_required TEXT,
  created_at TEXT NOT NULL
);
