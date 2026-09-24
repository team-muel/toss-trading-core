-- Preserve the exact Gold gate that authorized a Silver price observation.
CREATE TABLE IF NOT EXISTS am_price_observation_context (
  observation_id TEXT PRIMARY KEY REFERENCES am_temporal_observation(observation_id),
  context_manifest_id TEXT NOT NULL REFERENCES am_dataset_manifest(dataset_manifest_id)
);

CREATE TRIGGER IF NOT EXISTS am_price_observation_context_guard
BEFORE INSERT ON am_price_observation_context
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM am_temporal_observation observation
    JOIN am_dataset_manifest context
      ON context.dataset_manifest_id = NEW.context_manifest_id
    JOIN am_manifest_parent parent
      ON parent.child_manifest_id = context.dataset_manifest_id
     AND parent.parent_manifest_id = observation.dataset_manifest_id
    WHERE observation.observation_id = NEW.observation_id
      AND observation.field_name = 'price:total_return'
      AND context.layer = 'gold'
      AND context.dataset_name = 'daily-prices-with-context'
  ) THEN RAISE(ABORT, 'price context must authorize the observation Silver manifest') END;
END;

CREATE TRIGGER IF NOT EXISTS am_price_observation_context_update_block
BEFORE UPDATE ON am_price_observation_context
BEGIN SELECT RAISE(ABORT, 'price context admissions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS am_price_observation_context_delete_block
BEFORE DELETE ON am_price_observation_context
BEGIN SELECT RAISE(ABORT, 'price context admissions are append-only'); END;
