CREATE TABLE am_provider_accounting_contract (
  provider_contract_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  http_method TEXT NOT NULL CHECK (http_method = 'GET'),
  provider_schema_version TEXT NOT NULL,
  approval_evidence_id TEXT NOT NULL REFERENCES am_raw_api_response(raw_response_id),
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  content_hash TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL
);

CREATE TRIGGER am_provider_accounting_contract_no_update
BEFORE UPDATE ON am_provider_accounting_contract
BEGIN SELECT RAISE(ABORT, 'provider accounting contracts are append-only'); END;

CREATE TRIGGER am_provider_accounting_contract_no_delete
BEFORE DELETE ON am_provider_accounting_contract
BEGIN SELECT RAISE(ABORT, 'provider accounting contracts are append-only'); END;

CREATE TABLE am_provider_accounting_snapshot (
  accounting_snapshot_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  provider_contract_id TEXT NOT NULL REFERENCES am_provider_accounting_contract(provider_contract_id),
  source_response_id TEXT NOT NULL REFERENCES am_raw_api_response(raw_response_id),
  account_id TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  content_hash TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL,
  UNIQUE(runtime_run_id, provider_contract_id, source_response_id)
);

CREATE INDEX idx_provider_accounting_snapshot_runtime
ON am_provider_accounting_snapshot(runtime_run_id, account_id, observed_at_utc);

CREATE TRIGGER am_provider_accounting_snapshot_no_update
BEFORE UPDATE ON am_provider_accounting_snapshot
BEGIN SELECT RAISE(ABORT, 'provider accounting snapshots are append-only'); END;

CREATE TRIGGER am_provider_accounting_snapshot_no_delete
BEFORE DELETE ON am_provider_accounting_snapshot
BEGIN SELECT RAISE(ABORT, 'provider accounting snapshots are append-only'); END;
