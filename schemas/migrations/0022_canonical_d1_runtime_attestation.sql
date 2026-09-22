-- Gate D1 may consume only an independently signed binding for the exact
-- persisted source bundle.  A signed attestor registry by itself is reusable
-- authority material, not proof that a caller-owned runtime or store is
-- canonical.  This row is produced outside this repository; consumers only
-- verify it and it is append-only.
CREATE TABLE am_canonical_d1_runtime_attestation (
  runtime_run_id TEXT PRIMARY KEY REFERENCES am_runtime_run(runtime_run_id),
  attestation_id TEXT NOT NULL UNIQUE,
  attestor_id TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  signature_base64 TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  issued_at_utc TEXT NOT NULL
);

CREATE TRIGGER am_canonical_d1_runtime_attestation_no_update
BEFORE UPDATE ON am_canonical_d1_runtime_attestation
BEGIN SELECT RAISE(ABORT, 'canonical D1 runtime attestations are append-only'); END;

CREATE TRIGGER am_canonical_d1_runtime_attestation_no_delete
BEFORE DELETE ON am_canonical_d1_runtime_attestation
BEGIN SELECT RAISE(ABORT, 'canonical D1 runtime attestations are append-only'); END;
