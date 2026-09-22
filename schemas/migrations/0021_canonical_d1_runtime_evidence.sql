-- A Gate D1 submission must be assembled from one existing runtime run.  This
-- table records a replayable binding only; it is deliberately not a PASS flag.
CREATE TABLE am_canonical_d1_runtime_evidence (
  runtime_run_id TEXT PRIMARY KEY REFERENCES am_runtime_run(runtime_run_id),
  model_registry_snapshot_id TEXT NOT NULL REFERENCES am_model_registry_snapshot(model_registry_snapshot_id),
  model_registry_binding_hash TEXT NOT NULL,
  catalog_object_id TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  content_hash TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL
);

CREATE TRIGGER am_canonical_d1_runtime_evidence_no_update
BEFORE UPDATE ON am_canonical_d1_runtime_evidence BEGIN
  SELECT RAISE(ABORT, 'canonical D1 runtime evidence is append-only');
END;

CREATE TRIGGER am_canonical_d1_runtime_evidence_no_delete
BEFORE DELETE ON am_canonical_d1_runtime_evidence BEGIN
  SELECT RAISE(ABORT, 'canonical D1 runtime evidence is append-only');
END;
