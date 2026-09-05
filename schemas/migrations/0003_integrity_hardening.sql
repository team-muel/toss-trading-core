CREATE TRIGGER IF NOT EXISTS am_raw_method_guard
BEFORE INSERT ON am_raw_api_response
WHEN NEW.http_method <> 'GET'
 AND NOT (NEW.endpoint = '/oauth2/token' AND NEW.http_method = 'POST')
BEGIN SELECT RAISE(ABORT, 'read-only raw store rejects this HTTP method'); END;

CREATE TRIGGER IF NOT EXISTS am_account_raw_guard
BEFORE INSERT ON am_account_snapshot
WHEN NOT EXISTS (
  SELECT 1 FROM am_raw_api_response WHERE raw_response_id = NEW.source_response_id
)
BEGIN SELECT RAISE(ABORT, 'account snapshot requires raw response evidence'); END;

CREATE TRIGGER IF NOT EXISTS am_order_raw_guard
BEFORE INSERT ON am_broker_order
WHEN NOT EXISTS (
  SELECT 1 FROM am_raw_api_response WHERE raw_response_id = NEW.source_response_id
)
BEGIN SELECT RAISE(ABORT, 'broker order requires raw response evidence'); END;

CREATE TRIGGER IF NOT EXISTS am_execution_raw_guard
BEFORE INSERT ON am_execution
WHEN NOT EXISTS (
  SELECT 1 FROM am_raw_api_response WHERE raw_response_id = NEW.source_response_id
)
BEGIN SELECT RAISE(ABORT, 'execution requires raw response evidence'); END;

CREATE TRIGGER IF NOT EXISTS am_unapproved_live_intent_guard
BEFORE INSERT ON am_order_intent
WHEN NEW.mode = 'LIVE'
BEGIN SELECT RAISE(ABORT, 'live order intents are disabled'); END;

CREATE TABLE IF NOT EXISTS am_account_snapshot_raw (
  account_snapshot_id TEXT NOT NULL REFERENCES am_account_snapshot(account_snapshot_id),
  raw_response_id TEXT NOT NULL REFERENCES am_raw_api_response(raw_response_id),
  PRIMARY KEY (account_snapshot_id, raw_response_id)
);

CREATE TRIGGER IF NOT EXISTS am_account_snapshot_no_update
BEFORE UPDATE ON am_account_snapshot
BEGIN SELECT RAISE(ABORT, 'account snapshots are append-only'); END;

CREATE TRIGGER IF NOT EXISTS am_account_snapshot_no_delete
BEFORE DELETE ON am_account_snapshot
BEGIN SELECT RAISE(ABORT, 'account snapshots are append-only'); END;

CREATE TRIGGER IF NOT EXISTS am_source_health_no_update
BEFORE UPDATE ON am_source_health
BEGIN SELECT RAISE(ABORT, 'source health events are append-only'); END;

CREATE TRIGGER IF NOT EXISTS am_source_health_no_delete
BEFORE DELETE ON am_source_health
BEGIN SELECT RAISE(ABORT, 'source health events are append-only'); END;
