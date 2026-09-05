CREATE TABLE am_reference_record (
 record_id TEXT PRIMARY KEY,
 kind TEXT NOT NULL CHECK(kind IN ('INSTRUMENT','ALIAS','UNIVERSE','SESSION','ACTION')),
 entity_key TEXT NOT NULL,
 effective_from TEXT NOT NULL,
 effective_to TEXT,
 available_at TEXT NOT NULL,
 source TEXT NOT NULL CHECK(length(trim(source))>0),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
 CHECK(julianday(effective_from) IS NOT NULL AND substr(effective_from,-6)='+00:00'),
 CHECK(julianday(available_at) IS NOT NULL AND substr(available_at,-6)='+00:00'),
 CHECK(effective_to IS NULL OR (julianday(effective_to)>julianday(effective_from) AND substr(effective_to,-6)='+00:00')),
 UNIQUE(kind,entity_key,available_at)
);
CREATE INDEX am_reference_lookup ON am_reference_record(kind,entity_key,available_at);
CREATE TRIGGER am_reference_instrument_guard BEFORE INSERT ON am_reference_record
WHEN NEW.kind IN ('ALIAS','UNIVERSE','ACTION') AND NOT EXISTS (
 SELECT 1 FROM am_reference_record i WHERE i.kind='INSTRUMENT'
 AND i.entity_key=json_extract(NEW.payload_json,'$.instrument_id')
 AND i.available_at<=NEW.available_at
)
BEGIN SELECT RAISE(ABORT,'reference instrument lineage missing'); END;
CREATE TRIGGER am_reference_no_update BEFORE UPDATE ON am_reference_record
BEGIN SELECT RAISE(ABORT,'reference history is append-only'); END;
CREATE TRIGGER am_reference_no_delete BEFORE DELETE ON am_reference_record
BEGIN SELECT RAISE(ABORT,'reference history is append-only'); END;

CREATE TABLE am_corporate_action_comparison (
 comparison_hash TEXT PRIMARY KEY CHECK(length(comparison_hash)=64),
 action_id TEXT NOT NULL,
 as_of_utc TEXT NOT NULL,
 information_cutoff_utc TEXT NOT NULL,
 inputs_json TEXT NOT NULL CHECK(json_valid(inputs_json)),
 status TEXT NOT NULL CHECK(status IN ('MATCH','MISMATCH')),
 CHECK(julianday(as_of_utc) IS NOT NULL AND substr(as_of_utc,-6)='+00:00'),
 CHECK(julianday(information_cutoff_utc) IS NOT NULL AND substr(information_cutoff_utc,-6)='+00:00'),
 CHECK(julianday(information_cutoff_utc)<=julianday(as_of_utc))
);
CREATE TRIGGER am_action_comparison_no_update BEFORE UPDATE ON am_corporate_action_comparison
BEGIN SELECT RAISE(ABORT,'action comparisons are append-only'); END;
CREATE TRIGGER am_action_comparison_lineage BEFORE INSERT ON am_corporate_action_comparison
WHEN NOT EXISTS (
 SELECT 1 FROM am_reference_record r WHERE r.kind='ACTION' AND r.entity_key=NEW.action_id
 AND r.available_at<=NEW.information_cutoff_utc AND r.effective_from<=NEW.as_of_utc
)
BEGIN SELECT RAISE(ABORT,'corporate action evidence missing'); END;
CREATE TRIGGER am_action_comparison_no_delete BEFORE DELETE ON am_corporate_action_comparison
BEGIN SELECT RAISE(ABORT,'action comparisons are append-only'); END;
