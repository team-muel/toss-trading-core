CREATE TABLE IF NOT EXISTS am_temporal_observation (
  observation_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL CHECK (length(trim(entity_id)) > 0),
  field_name TEXT NOT NULL CHECK (length(trim(field_name)) > 0),
  value_json TEXT NOT NULL CHECK (json_valid(value_json)),
  reference_period TEXT NOT NULL CHECK (length(trim(reference_period)) > 0),
  event_time_utc TEXT NOT NULL,
  scheduled_release_at_utc TEXT,
  official_release_at_utc TEXT,
  source_timestamp_utc TEXT NOT NULL,
  received_at_utc TEXT NOT NULL,
  available_at_utc TEXT NOT NULL,
  ingested_at_utc TEXT NOT NULL,
  revised_at_utc TEXT,
  source_timezone TEXT NOT NULL CHECK (length(trim(source_timezone)) > 0),
  schema_version TEXT NOT NULL CHECK (length(trim(schema_version)) > 0),
  raw_response_id TEXT REFERENCES am_raw_api_response(raw_response_id),
  dataset_manifest_id TEXT REFERENCES am_dataset_manifest(dataset_manifest_id),
  supersedes_observation_id TEXT REFERENCES am_temporal_observation(observation_id),
  content_hash TEXT NOT NULL UNIQUE,
  CHECK ((raw_response_id IS NOT NULL) <> (dataset_manifest_id IS NOT NULL)),
  CHECK ((supersedes_observation_id IS NULL) = (revised_at_utc IS NULL)),
  CHECK (julianday(event_time_utc) IS NOT NULL AND substr(event_time_utc, -6) = '+00:00'),
  CHECK (scheduled_release_at_utc IS NULL OR
         (julianday(scheduled_release_at_utc) IS NOT NULL AND substr(scheduled_release_at_utc, -6) = '+00:00')),
  CHECK (official_release_at_utc IS NULL OR
         (julianday(official_release_at_utc) IS NOT NULL AND substr(official_release_at_utc, -6) = '+00:00')),
  CHECK (julianday(source_timestamp_utc) IS NOT NULL AND substr(source_timestamp_utc, -6) = '+00:00'),
  CHECK (julianday(received_at_utc) IS NOT NULL AND substr(received_at_utc, -6) = '+00:00'),
  CHECK (julianday(available_at_utc) IS NOT NULL AND substr(available_at_utc, -6) = '+00:00'),
  CHECK (julianday(ingested_at_utc) IS NOT NULL AND substr(ingested_at_utc, -6) = '+00:00'),
  CHECK (revised_at_utc IS NULL OR
         (julianday(revised_at_utc) IS NOT NULL AND substr(revised_at_utc, -6) = '+00:00')),
  CHECK (julianday(received_at_utc) >= julianday(source_timestamp_utc)),
  CHECK (julianday(ingested_at_utc) >= julianday(received_at_utc)),
  CHECK (julianday(available_at_utc) >= julianday(ingested_at_utc)),
  CHECK (official_release_at_utc IS NULL OR
         julianday(available_at_utc) >= julianday(official_release_at_utc)),
  CHECK (revised_at_utc IS NULL OR
         julianday(available_at_utc) >= julianday(revised_at_utc))
);

CREATE INDEX IF NOT EXISTS am_temporal_observation_asof_idx
ON am_temporal_observation(entity_id, field_name, available_at_utc DESC);

CREATE INDEX IF NOT EXISTS am_temporal_observation_vintage_idx
ON am_temporal_observation(entity_id, field_name, reference_period, available_at_utc DESC);

CREATE TRIGGER IF NOT EXISTS am_temporal_observation_initial_vintage_guard
BEFORE INSERT ON am_temporal_observation
WHEN NEW.supersedes_observation_id IS NULL
BEGIN
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM am_temporal_observation prior
    WHERE prior.entity_id = NEW.entity_id
      AND prior.field_name = NEW.field_name
      AND prior.reference_period = NEW.reference_period
  ) THEN RAISE(ABORT, 'an existing vintage must be explicitly superseded') END;
END;

CREATE TRIGGER IF NOT EXISTS am_temporal_observation_revision_guard
BEFORE INSERT ON am_temporal_observation
WHEN NEW.supersedes_observation_id IS NOT NULL
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM am_temporal_observation prior
    WHERE prior.observation_id = NEW.supersedes_observation_id
      AND prior.entity_id = NEW.entity_id
      AND prior.field_name = NEW.field_name
      AND prior.reference_period = NEW.reference_period
      AND julianday(prior.available_at_utc) < julianday(NEW.available_at_utc)
      AND julianday(prior.available_at_utc) < julianday(NEW.revised_at_utc)
  ) THEN RAISE(ABORT, 'revision must supersede an earlier matching vintage') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM am_temporal_observation successor
    WHERE successor.supersedes_observation_id = NEW.supersedes_observation_id
  ) THEN RAISE(ABORT, 'revision history cannot branch') END;
END;

CREATE TRIGGER IF NOT EXISTS am_temporal_observation_update_block
BEFORE UPDATE ON am_temporal_observation
BEGIN SELECT RAISE(ABORT, 'point-in-time observations are append-only'); END;

CREATE TRIGGER IF NOT EXISTS am_temporal_observation_delete_block
BEFORE DELETE ON am_temporal_observation
BEGIN SELECT RAISE(ABORT, 'point-in-time observations are append-only'); END;
