-- One non-promoting, replayable bundle of the persisted evidence needed before
-- Gate D2 can be evaluated from production artifacts.  This table is not a
-- gate decision and cannot record PASS/FAIL.
CREATE TABLE IF NOT EXISTS am_canonical_d2_production_evidence (
  runtime_run_id TEXT PRIMARY KEY REFERENCES am_runtime_run(runtime_run_id),
  model_registry_snapshot_id TEXT NOT NULL REFERENCES am_model_registry_snapshot(model_registry_snapshot_id),
  model_registry_binding_hash TEXT NOT NULL,
  factor_risk_calculation_id TEXT NOT NULL REFERENCES am_factor_risk_calculation(factor_risk_calculation_id),
  risk_free_manifest_id TEXT NOT NULL REFERENCES am_dataset_manifest(dataset_manifest_id),
  risk_free_curve_hash TEXT NOT NULL,
  accounting_snapshot_id TEXT NOT NULL REFERENCES am_provider_accounting_snapshot(accounting_snapshot_id),
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS am_canonical_d2_production_evidence_no_update
BEFORE UPDATE ON am_canonical_d2_production_evidence
BEGIN SELECT RAISE(ABORT, 'canonical D2 production evidence is append-only'); END;

CREATE TRIGGER IF NOT EXISTS am_canonical_d2_production_evidence_no_delete
BEFORE DELETE ON am_canonical_d2_production_evidence
BEGIN SELECT RAISE(ABORT, 'canonical D2 production evidence is append-only'); END;
