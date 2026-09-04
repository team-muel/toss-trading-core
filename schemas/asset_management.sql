PRAGMA foreign_keys = ON;

-- Operational system of record. Monetary and quantity values are exact strings.
CREATE TABLE IF NOT EXISTS am_runtime_run (
  runtime_run_id TEXT PRIMARY KEY,
  as_of_utc TEXT NOT NULL,
  information_cutoff_utc TEXT NOT NULL,
  code_revision TEXT NOT NULL,
  created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_account_snapshot (
  account_snapshot_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  account_id TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL,
  source_response_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_holding_snapshot (
  holding_snapshot_id TEXT PRIMARY KEY,
  account_snapshot_id TEXT NOT NULL REFERENCES am_account_snapshot(account_snapshot_id),
  instrument_id TEXT NOT NULL,
  quantity_decimal TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_broker_order (
  broker_order_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  account_id TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  source_response_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_execution (
  execution_id TEXT PRIMARY KEY,
  broker_order_id TEXT NOT NULL REFERENCES am_broker_order(broker_order_id),
  quantity_decimal TEXT NOT NULL,
  amount_decimal TEXT NOT NULL,
  commission_decimal TEXT NOT NULL,
  tax_decimal TEXT NOT NULL,
  executed_at_utc TEXT NOT NULL,
  source_response_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_cash_ledger (
  cash_event_id TEXT PRIMARY KEY,
  execution_id TEXT REFERENCES am_execution(execution_id),
  account_id TEXT NOT NULL,
  currency TEXT NOT NULL,
  amount_decimal TEXT NOT NULL,
  settlement_date TEXT,
  event_type TEXT NOT NULL,
  created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_position_ledger (
  position_event_id TEXT PRIMARY KEY,
  execution_id TEXT REFERENCES am_execution(execution_id),
  account_id TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  quantity_delta_decimal TEXT NOT NULL,
  event_type TEXT NOT NULL,
  created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_reconciliation_run (
  reconciliation_run_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  account_snapshot_id TEXT NOT NULL REFERENCES am_account_snapshot(account_snapshot_id),
  status TEXT NOT NULL CHECK (status IN ('MATCH', 'UNRECONCILED')),
  differences_json TEXT NOT NULL,
  completed_at_utc TEXT NOT NULL
);

-- Immutable external datasets and their PIT transformation lineage.
CREATE TABLE IF NOT EXISTS am_ingestion_run (
  ingestion_run_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  provider TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  completed_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS am_dataset_manifest (
  dataset_manifest_id TEXT PRIMARY KEY,
  ingestion_run_id TEXT NOT NULL REFERENCES am_ingestion_run(ingestion_run_id),
  layer TEXT NOT NULL CHECK (layer IN ('bronze', 'silver', 'gold')),
  dataset_name TEXT NOT NULL,
  uri TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL,
  received_at_utc TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  row_count INTEGER,
  UNIQUE(layer, dataset_name, content_hash)
);

CREATE TABLE IF NOT EXISTS am_manifest_parent (
  child_manifest_id TEXT NOT NULL REFERENCES am_dataset_manifest(dataset_manifest_id),
  parent_manifest_id TEXT NOT NULL REFERENCES am_dataset_manifest(dataset_manifest_id),
  PRIMARY KEY (child_manifest_id, parent_manifest_id),
  CHECK (child_manifest_id <> parent_manifest_id)
);

-- Decision system of record. Each stage points to the exact preceding artifact.
CREATE TABLE IF NOT EXISTS am_feature_run (
  feature_run_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  dataset_manifest_id TEXT NOT NULL REFERENCES am_dataset_manifest(dataset_manifest_id),
  feature_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_state_run (
  state_run_id TEXT PRIMARY KEY,
  feature_run_id TEXT NOT NULL REFERENCES am_feature_run(feature_run_id),
  state_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_pricing_run (
  pricing_run_id TEXT PRIMARY KEY,
  state_run_id TEXT NOT NULL REFERENCES am_state_run(state_run_id),
  pricing_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_expectation_run (
  expectation_run_id TEXT PRIMARY KEY,
  pricing_run_id TEXT NOT NULL REFERENCES am_pricing_run(pricing_run_id),
  expectation_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_risk_model_run (
  risk_model_run_id TEXT PRIMARY KEY,
  state_run_id TEXT NOT NULL REFERENCES am_state_run(state_run_id),
  risk_model_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_policy_version (
  policy_version TEXT PRIMARY KEY,
  policy_kind TEXT NOT NULL CHECK (policy_kind IN ('investment', 'risk', 'execution', 'promotion', 'tax')),
  effective_from_utc TEXT NOT NULL,
  effective_to_utc TEXT,
  approved_by TEXT NOT NULL,
  approval_reason TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS am_parameter_set (
  parameter_set_id TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS am_portfolio_target (
  portfolio_target_id TEXT PRIMARY KEY,
  expectation_run_id TEXT NOT NULL REFERENCES am_expectation_run(expectation_run_id),
  risk_model_run_id TEXT NOT NULL REFERENCES am_risk_model_run(risk_model_run_id),
  policy_version TEXT NOT NULL REFERENCES am_policy_version(policy_version),
  parameter_set_id TEXT NOT NULL REFERENCES am_parameter_set(parameter_set_id),
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_risk_decision (
  risk_decision_id TEXT PRIMARY KEY,
  portfolio_target_id TEXT NOT NULL REFERENCES am_portfolio_target(portfolio_target_id),
  action TEXT NOT NULL CHECK (action IN ('ALLOW', 'REDUCE', 'BLOCK')),
  reason_codes_json TEXT NOT NULL,
  policy_version TEXT NOT NULL REFERENCES am_policy_version(policy_version),
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_order_intent (
  order_intent_id TEXT PRIMARY KEY,
  risk_decision_id TEXT NOT NULL REFERENCES am_risk_decision(risk_decision_id),
  idempotency_key TEXT NOT NULL UNIQUE,
  mode TEXT NOT NULL CHECK (mode IN ('PAPER', 'SHADOW', 'LIVE')),
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_client_order (
  client_order_id TEXT PRIMARY KEY,
  order_intent_id TEXT NOT NULL REFERENCES am_order_intent(order_intent_id),
  idempotency_key TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL,
  created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_order_link (
  client_order_id TEXT PRIMARY KEY REFERENCES am_client_order(client_order_id),
  broker_order_id TEXT NOT NULL UNIQUE REFERENCES am_broker_order(broker_order_id)
);

CREATE TABLE IF NOT EXISTS am_validation_result (
  validation_result_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  policy_version TEXT NOT NULL REFERENCES am_policy_version(policy_version),
  parameter_set_id TEXT NOT NULL REFERENCES am_parameter_set(parameter_set_id),
  result_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

-- Source events and decisions are append-only. Corrections are new records.
CREATE TRIGGER IF NOT EXISTS am_manifest_no_update
BEFORE UPDATE ON am_dataset_manifest BEGIN SELECT RAISE(ABORT, 'dataset manifests are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_manifest_no_delete
BEFORE DELETE ON am_dataset_manifest BEGIN SELECT RAISE(ABORT, 'dataset manifests are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_decision_no_update
BEFORE UPDATE ON am_risk_decision BEGIN SELECT RAISE(ABORT, 'risk decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_decision_no_delete
BEFORE DELETE ON am_risk_decision BEGIN SELECT RAISE(ABORT, 'risk decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_execution_no_update
BEFORE UPDATE ON am_execution BEGIN SELECT RAISE(ABORT, 'executions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_execution_no_delete
BEFORE DELETE ON am_execution BEGIN SELECT RAISE(ABORT, 'executions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_policy_no_update
BEFORE UPDATE ON am_policy_version BEGIN SELECT RAISE(ABORT, 'policy versions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_policy_no_delete
BEFORE DELETE ON am_policy_version BEGIN SELECT RAISE(ABORT, 'policy versions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_parameter_no_update
BEFORE UPDATE ON am_parameter_set BEGIN SELECT RAISE(ABORT, 'parameter sets are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_parameter_no_delete
BEFORE DELETE ON am_parameter_set BEGIN SELECT RAISE(ABORT, 'parameter sets are append-only'); END;
