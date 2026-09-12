-- Canonical D2 consumers may only read provider/governance provenance that was
-- bound to a runtime before the bundle is assembled.  These tables deliberately
-- have no decision fields and are append-only.
CREATE TABLE am_fred_risk_free_freshness_policy (
  fred_risk_free_freshness_policy_id TEXT PRIMARY KEY,
  maximum_observation_age_seconds INTEGER NOT NULL CHECK (maximum_observation_age_seconds > 0 AND maximum_observation_age_seconds <= 604800),
  content_hash TEXT NOT NULL UNIQUE,
  published_at_utc TEXT NOT NULL
);

CREATE TABLE am_runtime_fred_risk_free_artifact (
  runtime_run_id TEXT PRIMARY KEY REFERENCES am_runtime_run(runtime_run_id),
  risk_free_manifest_id TEXT NOT NULL REFERENCES am_dataset_manifest(dataset_manifest_id),
  fred_risk_free_freshness_policy_id TEXT NOT NULL REFERENCES am_fred_risk_free_freshness_policy(fred_risk_free_freshness_policy_id),
  content_hash TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL
);

CREATE TABLE am_runtime_fred_risk_free_raw_provenance (
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  series_id TEXT NOT NULL CHECK (series_id IN ('DGS1MO', 'DGS3MO', 'DGS6MO', 'DGS1')),
  raw_response_id TEXT NOT NULL REFERENCES am_raw_api_response(raw_response_id),
  PRIMARY KEY (runtime_run_id, series_id),
  UNIQUE (raw_response_id),
  UNIQUE (runtime_run_id, raw_response_id)
);

-- A broker statement carries runtime-specific economic state.  It cannot be
-- rebound to another run even if its receipt and cutoff still validate.
CREATE UNIQUE INDEX idx_provider_accounting_snapshot_source_response_global
ON am_provider_accounting_snapshot(source_response_id);

CREATE TABLE am_model_governance_review_raw_provenance (
  review_evidence_id TEXT PRIMARY KEY REFERENCES am_model_governance_review_evidence(review_evidence_id),
  raw_response_id TEXT NOT NULL UNIQUE REFERENCES am_raw_api_response(raw_response_id),
  content_hash TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL
);

CREATE TABLE am_factor_risk_estimator_raw_provenance (
  factor_risk_estimator_evidence_id TEXT PRIMARY KEY REFERENCES am_factor_risk_estimator_evidence(factor_risk_estimator_evidence_id),
  source_manifest_id TEXT NOT NULL REFERENCES am_dataset_manifest(dataset_manifest_id),
  raw_response_id TEXT NOT NULL UNIQUE REFERENCES am_raw_api_response(raw_response_id),
  content_hash TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL
);

-- A raw API row is not itself proof of who produced it.  Canonical consumers
-- require one receipt signed by a public key from the runtime-bound immutable
-- registry snapshot.  The producer holds the private key outside this repository.
CREATE TABLE am_external_evidence_attestation (
  attestation_id TEXT PRIMARY KEY,
  raw_response_id TEXT NOT NULL UNIQUE REFERENCES am_raw_api_response(raw_response_id),
  attestor_id TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  signature_base64 TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  issued_at_utc TEXT NOT NULL
);

-- Trust roots are snapshotted and selected before a runtime begins.  Consumers
-- never reread a mutable deployment configuration while replaying evidence.
-- A separately trusted governance authority signs each registry snapshot;
-- append-only storage alone is never treated as authority.
CREATE TABLE am_evidence_attestor_registry_snapshot (
  evidence_attestor_registry_snapshot_id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  content_hash TEXT NOT NULL UNIQUE,
  published_at_utc TEXT NOT NULL,
  authority_id TEXT NOT NULL,
  authorization_payload_json TEXT NOT NULL CHECK (json_valid(authorization_payload_json)),
  authority_signature_base64 TEXT NOT NULL
);

CREATE TABLE am_runtime_evidence_attestor_registry (
  runtime_run_id TEXT PRIMARY KEY REFERENCES am_runtime_run(runtime_run_id),
  evidence_attestor_registry_snapshot_id TEXT NOT NULL REFERENCES am_evidence_attestor_registry_snapshot(evidence_attestor_registry_snapshot_id),
  bound_at_utc TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE
);

CREATE TRIGGER am_fred_risk_free_freshness_policy_no_update BEFORE UPDATE ON am_fred_risk_free_freshness_policy
BEGIN SELECT RAISE(ABORT, 'FRED risk-free freshness policies are append-only'); END;
CREATE TRIGGER am_fred_risk_free_freshness_policy_no_delete BEFORE DELETE ON am_fred_risk_free_freshness_policy
BEGIN SELECT RAISE(ABORT, 'FRED risk-free freshness policies are append-only'); END;
CREATE TRIGGER am_runtime_fred_risk_free_artifact_no_update BEFORE UPDATE ON am_runtime_fred_risk_free_artifact
BEGIN SELECT RAISE(ABORT, 'runtime FRED risk-free artifacts are append-only'); END;
CREATE TRIGGER am_runtime_fred_risk_free_artifact_no_delete BEFORE DELETE ON am_runtime_fred_risk_free_artifact
BEGIN SELECT RAISE(ABORT, 'runtime FRED risk-free artifacts are append-only'); END;
CREATE TRIGGER am_runtime_fred_risk_free_raw_provenance_no_update BEFORE UPDATE ON am_runtime_fred_risk_free_raw_provenance
BEGIN SELECT RAISE(ABORT, 'runtime FRED raw provenance is append-only'); END;
CREATE TRIGGER am_runtime_fred_risk_free_raw_provenance_no_delete BEFORE DELETE ON am_runtime_fred_risk_free_raw_provenance
BEGIN SELECT RAISE(ABORT, 'runtime FRED raw provenance is append-only'); END;
CREATE TRIGGER am_model_governance_review_raw_provenance_no_update BEFORE UPDATE ON am_model_governance_review_raw_provenance
BEGIN SELECT RAISE(ABORT, 'model governance raw provenance is append-only'); END;
CREATE TRIGGER am_model_governance_review_raw_provenance_no_delete BEFORE DELETE ON am_model_governance_review_raw_provenance
BEGIN SELECT RAISE(ABORT, 'model governance raw provenance is append-only'); END;
CREATE TRIGGER am_factor_risk_estimator_raw_provenance_no_update BEFORE UPDATE ON am_factor_risk_estimator_raw_provenance
BEGIN SELECT RAISE(ABORT, 'factor-risk estimator raw provenance is append-only'); END;
CREATE TRIGGER am_factor_risk_estimator_raw_provenance_no_delete BEFORE DELETE ON am_factor_risk_estimator_raw_provenance
BEGIN SELECT RAISE(ABORT, 'factor-risk estimator raw provenance is append-only'); END;
CREATE TRIGGER am_external_evidence_attestation_no_update BEFORE UPDATE ON am_external_evidence_attestation
BEGIN SELECT RAISE(ABORT, 'external evidence attestations are append-only'); END;
CREATE TRIGGER am_external_evidence_attestation_no_delete BEFORE DELETE ON am_external_evidence_attestation
BEGIN SELECT RAISE(ABORT, 'external evidence attestations are append-only'); END;
CREATE TRIGGER am_evidence_attestor_registry_snapshot_no_update BEFORE UPDATE ON am_evidence_attestor_registry_snapshot
BEGIN SELECT RAISE(ABORT, 'evidence attestor registry snapshots are append-only'); END;
CREATE TRIGGER am_evidence_attestor_registry_snapshot_no_delete BEFORE DELETE ON am_evidence_attestor_registry_snapshot
BEGIN SELECT RAISE(ABORT, 'evidence attestor registry snapshots are append-only'); END;
CREATE TRIGGER am_runtime_evidence_attestor_registry_no_update BEFORE UPDATE ON am_runtime_evidence_attestor_registry
BEGIN SELECT RAISE(ABORT, 'runtime evidence attestor bindings are append-only'); END;
CREATE TRIGGER am_runtime_evidence_attestor_registry_no_delete BEFORE DELETE ON am_runtime_evidence_attestor_registry
BEGIN SELECT RAISE(ABORT, 'runtime evidence attestor bindings are append-only'); END;
