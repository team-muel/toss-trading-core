CREATE TABLE am_component_forecast_calculation (
  component_forecast_calculation_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  canonical_artifact_json TEXT NOT NULL CHECK (json_valid(canonical_artifact_json)),
  content_hash TEXT NOT NULL UNIQUE CHECK (
    content_hash = component_forecast_calculation_id AND
    length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'
  ),
  calculated_at_utc TEXT NOT NULL
);
CREATE TRIGGER am_component_forecast_calculation_no_update BEFORE UPDATE ON am_component_forecast_calculation
BEGIN SELECT RAISE(ABORT, 'component forecast calculations are append-only'); END;
CREATE TRIGGER am_component_forecast_calculation_no_delete BEFORE DELETE ON am_component_forecast_calculation
BEGIN SELECT RAISE(ABORT, 'component forecast calculations are append-only'); END;
