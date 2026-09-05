CREATE TRIGGER am_account_snapshot_utc_guard
BEFORE INSERT ON am_account_snapshot
WHEN length(trim(NEW.account_id)) = 0
  OR julianday(NEW.observed_at_utc) IS NULL
  OR substr(NEW.observed_at_utc, -6) <> '+00:00'
  OR EXISTS (
    SELECT 1 FROM am_runtime_run runtime
    WHERE runtime.runtime_run_id=NEW.runtime_run_id
      AND julianday(NEW.observed_at_utc)>julianday(runtime.as_of_utc)
  )
  OR EXISTS (
    SELECT 1 FROM am_raw_api_response raw
    WHERE raw.raw_response_id=NEW.source_response_id
      AND julianday(NEW.observed_at_utc)<julianday(raw.received_at_utc)
  )
BEGIN SELECT RAISE(ABORT, 'account snapshot requires chronological UTC evidence'); END;

CREATE TRIGGER am_account_snapshot_raw_time_guard
BEFORE INSERT ON am_account_snapshot_raw
WHEN EXISTS (
  SELECT 1 FROM am_account_snapshot snapshot, am_raw_api_response raw
  WHERE snapshot.account_snapshot_id=NEW.account_snapshot_id
    AND raw.raw_response_id=NEW.raw_response_id
    AND julianday(snapshot.observed_at_utc)<julianday(raw.received_at_utc)
)
BEGIN SELECT RAISE(ABORT, 'account snapshot cannot cite future raw evidence'); END;
