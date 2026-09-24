-- Immutable manifest IDs include metadata such as availability and retrieval
-- time, so equal content can legitimately have multiple manifest identities.
DROP TRIGGER IF EXISTS am_manifest_no_update;
DROP TRIGGER IF EXISTS am_manifest_no_delete;

ALTER TABLE am_dataset_manifest RENAME TO am_dataset_manifest_legacy;

CREATE TABLE am_dataset_manifest (
  dataset_manifest_id TEXT PRIMARY KEY,
  ingestion_run_id TEXT NOT NULL REFERENCES am_ingestion_run(ingestion_run_id),
  layer TEXT NOT NULL CHECK (layer IN ('bronze', 'silver', 'gold')),
  dataset_name TEXT NOT NULL,
  uri TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL,
  received_at_utc TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  row_count INTEGER
);

INSERT INTO am_dataset_manifest (
  dataset_manifest_id, ingestion_run_id, layer, dataset_name, uri, content_hash,
  observed_at_utc, received_at_utc, schema_version, row_count
)
SELECT dataset_manifest_id, ingestion_run_id, layer, dataset_name, uri, content_hash,
       observed_at_utc, received_at_utc, schema_version, row_count
FROM am_dataset_manifest_legacy;

DROP TABLE am_dataset_manifest_legacy;

CREATE TRIGGER IF NOT EXISTS am_manifest_no_update
BEFORE UPDATE ON am_dataset_manifest BEGIN SELECT RAISE(ABORT, 'dataset manifests are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_manifest_no_delete
BEFORE DELETE ON am_dataset_manifest BEGIN SELECT RAISE(ABORT, 'dataset manifests are append-only'); END;
