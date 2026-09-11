CREATE TABLE am_factor_risk_estimator_evidence (
  factor_risk_estimator_evidence_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  model_key TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  content_hash TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL
);

CREATE TRIGGER am_factor_risk_estimator_evidence_no_update
BEFORE UPDATE ON am_factor_risk_estimator_evidence
BEGIN SELECT RAISE(ABORT, 'factor-risk estimator evidence is append-only'); END;

CREATE TRIGGER am_factor_risk_estimator_evidence_no_delete
BEFORE DELETE ON am_factor_risk_estimator_evidence
BEGIN SELECT RAISE(ABORT, 'factor-risk estimator evidence is append-only'); END;

CREATE TABLE am_factor_risk_calculation (
  factor_risk_calculation_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  factor_risk_estimator_evidence_id TEXT NOT NULL REFERENCES am_factor_risk_estimator_evidence(factor_risk_estimator_evidence_id),
  input_lineage_json TEXT NOT NULL CHECK (json_valid(input_lineage_json)),
  estimator_payload_json TEXT NOT NULL CHECK (json_valid(estimator_payload_json)),
  assessment_payload_json TEXT NOT NULL CHECK (json_valid(assessment_payload_json)),
  content_hash TEXT NOT NULL UNIQUE,
  calculated_at_utc TEXT NOT NULL
);

CREATE TRIGGER am_factor_risk_calculation_no_update
BEFORE UPDATE ON am_factor_risk_calculation
BEGIN SELECT RAISE(ABORT, 'factor-risk calculations are append-only'); END;

CREATE TRIGGER am_factor_risk_calculation_no_delete
BEFORE DELETE ON am_factor_risk_calculation
BEGIN SELECT RAISE(ABORT, 'factor-risk calculations are append-only'); END;
