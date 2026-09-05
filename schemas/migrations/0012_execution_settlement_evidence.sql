CREATE TABLE am_execution_settlement_evidence (
  execution_delta_id TEXT PRIMARY KEY REFERENCES am_execution_delta(execution_delta_id),
  execution_snapshot_id TEXT NOT NULL UNIQUE REFERENCES am_execution_snapshot(execution_snapshot_id),
  source_response_id TEXT NOT NULL REFERENCES am_raw_api_response(raw_response_id),
  settlement_date TEXT NOT NULL CHECK (date(settlement_date)=settlement_date),
  extraction_paths_json TEXT NOT NULL CHECK (
    json_valid(extraction_paths_json) AND json_type(extraction_paths_json)='array'
    AND json_array_length(extraction_paths_json)>0
  ),
  response_hash TEXT NOT NULL CHECK (length(response_hash)=64),
  evidence_hash TEXT NOT NULL CHECK (length(evidence_hash)=64)
);

CREATE TRIGGER am_execution_settlement_lineage_guard
BEFORE INSERT ON am_execution_settlement_evidence
WHEN NOT EXISTS (
  SELECT 1
  FROM am_execution_delta d
  JOIN am_execution_snapshot s ON s.execution_snapshot_id=d.to_snapshot_id
  JOIN am_broker_order o ON o.broker_order_id=d.broker_order_id
  JOIN am_raw_api_response raw ON raw.raw_response_id=s.source_response_id
  WHERE d.execution_delta_id=NEW.execution_delta_id
    AND s.execution_snapshot_id=NEW.execution_snapshot_id
    AND raw.raw_response_id=NEW.source_response_id
    AND raw.source='toss'
    AND raw.account_id=o.account_id
    AND raw.status_code BETWEEN 200 AND 299
    AND raw.response_hash=NEW.response_hash
    AND EXISTS (
      SELECT 1 FROM json_tree(raw.body_json) item
      WHERE lower(replace(replace(item.key, '_', ''), '-', ''))='settlementdate'
        AND item.type='text' AND item.value=NEW.settlement_date
    )
    AND NOT EXISTS (
      SELECT 1 FROM json_tree(raw.body_json) item
      WHERE lower(replace(replace(item.key, '_', ''), '-', ''))='settlementdate'
        AND (item.type<>'text' OR item.value<>NEW.settlement_date)
    )
)
BEGIN SELECT RAISE(ABORT, 'settlement evidence conflicts with execution raw lineage'); END;

CREATE TRIGGER am_execution_posting_settlement_guard
BEFORE INSERT ON am_execution_posting_context
WHEN NOT EXISTS (
  SELECT 1 FROM am_execution_settlement_evidence evidence
  WHERE evidence.execution_delta_id=NEW.execution_delta_id
    AND evidence.settlement_date=NEW.settlement_date
)
BEGIN SELECT RAISE(ABORT, 'execution posting requires broker settlement evidence'); END;

CREATE TRIGGER am_execution_settlement_no_update
BEFORE UPDATE ON am_execution_settlement_evidence
BEGIN SELECT RAISE(ABORT, 'execution settlement evidence is append-only'); END;

CREATE TRIGGER am_execution_settlement_no_delete
BEFORE DELETE ON am_execution_settlement_evidence
BEGIN SELECT RAISE(ABORT, 'execution settlement evidence is append-only'); END;
