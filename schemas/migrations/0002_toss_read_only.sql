CREATE TABLE IF NOT EXISTS am_raw_api_response (
  raw_response_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  http_method TEXT NOT NULL CHECK (http_method = 'GET' OR endpoint = '/oauth2/token'),
  request_hash TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  response_hash TEXT NOT NULL,
  body_json TEXT NOT NULL,
  requested_at_utc TEXT NOT NULL,
  received_at_utc TEXT NOT NULL,
  account_id TEXT,
  schema_version TEXT NOT NULL,
  headers_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS am_source_health (
  health_event_id TEXT PRIMARY KEY,
  raw_response_id TEXT REFERENCES am_raw_api_response(raw_response_id),
  source TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('OK', 'DEGRADED', 'BLOCKED')),
  reason TEXT,
  observed_at_utc TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS am_raw_response_no_update
BEFORE UPDATE ON am_raw_api_response
BEGIN SELECT RAISE(ABORT, 'raw API responses are append-only'); END;

CREATE TRIGGER IF NOT EXISTS am_raw_response_no_delete
BEFORE DELETE ON am_raw_api_response
BEGIN SELECT RAISE(ABORT, 'raw API responses are append-only'); END;
