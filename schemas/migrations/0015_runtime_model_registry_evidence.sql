-- Immutable model-governance evidence. A runtime may select only a review-backed
-- snapshot published before its information cutoff; it cannot mint authority in-run.
CREATE TABLE am_model_governance_review_evidence (
  review_evidence_id TEXT PRIMARY KEY,
  evidence_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL
);

CREATE TABLE am_model_registry_snapshot (
  model_registry_snapshot_id TEXT PRIMARY KEY,
  registry_hash TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL,
  review_evidence_json TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  published_at_utc TEXT NOT NULL
);

CREATE TABLE am_runtime_model_registry (
  runtime_run_id TEXT PRIMARY KEY REFERENCES am_runtime_run(runtime_run_id),
  model_registry_snapshot_id TEXT NOT NULL REFERENCES am_model_registry_snapshot(model_registry_snapshot_id),
  bound_at_utc TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE
);

CREATE TRIGGER am_model_registry_snapshot_no_update
BEFORE UPDATE ON am_model_registry_snapshot BEGIN
  SELECT RAISE(ABORT, 'model registry snapshots are append-only');
END;
CREATE TRIGGER am_model_registry_snapshot_no_delete
BEFORE DELETE ON am_model_registry_snapshot BEGIN
  SELECT RAISE(ABORT, 'model registry snapshots are append-only');
END;
CREATE TRIGGER am_model_governance_review_evidence_no_update
BEFORE UPDATE ON am_model_governance_review_evidence BEGIN
  SELECT RAISE(ABORT, 'model governance review evidence is append-only');
END;
CREATE TRIGGER am_model_governance_review_evidence_no_delete
BEFORE DELETE ON am_model_governance_review_evidence BEGIN
  SELECT RAISE(ABORT, 'model governance review evidence is append-only');
END;
CREATE TRIGGER am_runtime_model_registry_no_update
BEFORE UPDATE ON am_runtime_model_registry BEGIN
  SELECT RAISE(ABORT, 'runtime model registry bindings are append-only');
END;
CREATE TRIGGER am_runtime_model_registry_no_delete
BEFORE DELETE ON am_runtime_model_registry BEGIN
  SELECT RAISE(ABORT, 'runtime model registry bindings are append-only');
END;
CREATE TRIGGER am_runtime_run_no_update
BEFORE UPDATE ON am_runtime_run BEGIN
  SELECT RAISE(ABORT, 'runtime runs are append-only');
END;
CREATE TRIGGER am_runtime_run_no_delete
BEFORE DELETE ON am_runtime_run BEGIN
  SELECT RAISE(ABORT, 'runtime runs are append-only');
END;
