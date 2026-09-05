CREATE TABLE IF NOT EXISTS am_pipeline_stage_evidence (
  runtime_run_id TEXT NOT NULL REFERENCES am_runtime_run(runtime_run_id),
  stage_no INTEGER NOT NULL CHECK (stage_no BETWEEN 1 AND 8),
  stage_name TEXT NOT NULL CHECK (stage_name IN (
    'INVESTMENT_POLICY', 'ACCOUNT_TRUTH', 'TIME_TRUTH', 'DATA_TRUTH',
    'FINANCIAL_CALCULATION', 'TARGET_PORTFOLIO', 'RISK_CONTROL', 'ORDER'
  )),
  evidence_id TEXT NOT NULL CHECK (length(trim(evidence_id)) > 0),
  content_hash TEXT NOT NULL CHECK (length(trim(content_hash)) > 0),
  PRIMARY KEY (runtime_run_id, stage_no),
  UNIQUE (runtime_run_id, stage_name),
  CHECK (stage_no = CASE stage_name
    WHEN 'INVESTMENT_POLICY' THEN 1 WHEN 'ACCOUNT_TRUTH' THEN 2
    WHEN 'TIME_TRUTH' THEN 3 WHEN 'DATA_TRUTH' THEN 4
    WHEN 'FINANCIAL_CALCULATION' THEN 5 WHEN 'TARGET_PORTFOLIO' THEN 6
    WHEN 'RISK_CONTROL' THEN 7 WHEN 'ORDER' THEN 8 END)
);

CREATE TRIGGER am_pipeline_stage_order_guard
BEFORE INSERT ON am_pipeline_stage_evidence
WHEN (SELECT COUNT(*) FROM am_pipeline_stage_evidence existing
      WHERE existing.runtime_run_id=NEW.runtime_run_id) <> NEW.stage_no - 1
BEGIN SELECT RAISE(ABORT, 'pipeline stage evidence must be contiguous'); END;

CREATE TRIGGER am_pipeline_stage_artifact_guard
BEFORE INSERT ON am_pipeline_stage_evidence
WHEN NOT (
  (NEW.stage_name='INVESTMENT_POLICY' AND EXISTS (
    SELECT 1 FROM am_policy_version policy JOIN am_runtime_run runtime
      ON runtime.runtime_run_id=NEW.runtime_run_id
    WHERE policy.policy_version=NEW.evidence_id AND policy.policy_kind='investment'
      AND policy.content_hash=NEW.content_hash
      AND julianday(policy.effective_from_utc)<=julianday(runtime.as_of_utc)
      AND (policy.effective_to_utc IS NULL OR
           julianday(runtime.as_of_utc)<julianday(policy.effective_to_utc))))
  OR (NEW.stage_name='ACCOUNT_TRUTH' AND EXISTS (
    SELECT 1 FROM am_account_snapshot snapshot
    WHERE snapshot.account_snapshot_id=NEW.evidence_id
      AND snapshot.runtime_run_id=NEW.runtime_run_id
      AND snapshot.content_hash=NEW.content_hash))
  OR (NEW.stage_name='TIME_TRUTH' AND NEW.evidence_id=NEW.runtime_run_id
      AND length(NEW.content_hash)=64)
  OR (NEW.stage_name='DATA_TRUTH' AND EXISTS (
    SELECT 1 FROM am_dataset_manifest manifest
    JOIN am_ingestion_run ingestion USING(ingestion_run_id)
    WHERE manifest.dataset_manifest_id=NEW.evidence_id
      AND ingestion.runtime_run_id=NEW.runtime_run_id
      AND manifest.content_hash=NEW.content_hash))
  OR (NEW.stage_name='FINANCIAL_CALCULATION' AND EXISTS (
    SELECT 1 FROM am_expectation_run expectation
    JOIN am_pricing_run pricing USING(pricing_run_id)
    JOIN am_state_run state USING(state_run_id)
    JOIN am_feature_run feature USING(feature_run_id)
    WHERE expectation.expectation_run_id=NEW.evidence_id
      AND expectation.content_hash=NEW.content_hash
      AND feature.runtime_run_id=NEW.runtime_run_id))
  OR (NEW.stage_name='TARGET_PORTFOLIO' AND EXISTS (
    SELECT 1 FROM am_portfolio_target target
    JOIN am_expectation_run expectation USING(expectation_run_id)
    JOIN am_pricing_run pricing USING(pricing_run_id)
    JOIN am_state_run state USING(state_run_id)
    JOIN am_feature_run feature USING(feature_run_id)
    JOIN am_risk_model_run risk_model USING(risk_model_run_id)
    JOIN am_state_run risk_state ON risk_state.state_run_id=risk_model.state_run_id
    JOIN am_feature_run risk_feature ON risk_feature.feature_run_id=risk_state.feature_run_id
    WHERE target.portfolio_target_id=NEW.evidence_id
      AND target.content_hash=NEW.content_hash
      AND feature.runtime_run_id=NEW.runtime_run_id
      AND risk_feature.runtime_run_id=NEW.runtime_run_id))
  OR (NEW.stage_name='RISK_CONTROL' AND EXISTS (
    SELECT 1 FROM am_risk_decision decision
    JOIN am_portfolio_target target USING(portfolio_target_id)
    JOIN am_expectation_run expectation USING(expectation_run_id)
    JOIN am_pricing_run pricing USING(pricing_run_id)
    JOIN am_state_run state USING(state_run_id)
    JOIN am_feature_run feature USING(feature_run_id)
    JOIN am_risk_model_run risk_model USING(risk_model_run_id)
    JOIN am_state_run risk_state ON risk_state.state_run_id=risk_model.state_run_id
    JOIN am_feature_run risk_feature ON risk_feature.feature_run_id=risk_state.feature_run_id
    WHERE decision.risk_decision_id=NEW.evidence_id
      AND decision.content_hash=NEW.content_hash
      AND decision.action IN ('ALLOW','REDUCE')
      AND feature.runtime_run_id=NEW.runtime_run_id
      AND risk_feature.runtime_run_id=NEW.runtime_run_id))
  OR (NEW.stage_name='ORDER' AND EXISTS (
    SELECT 1 FROM am_order_intent intent
    WHERE intent.order_intent_id=NEW.evidence_id
      AND intent.runtime_run_id=NEW.runtime_run_id
      AND intent.content_hash=NEW.content_hash))
)
BEGIN SELECT RAISE(ABORT, 'pipeline evidence does not match its runtime artifact'); END;

CREATE TRIGGER am_pipeline_stage_no_update BEFORE UPDATE ON am_pipeline_stage_evidence
BEGIN SELECT RAISE(ABORT, 'pipeline stage evidence is append-only'); END;
CREATE TRIGGER am_pipeline_stage_no_delete BEFORE DELETE ON am_pipeline_stage_evidence
BEGIN SELECT RAISE(ABORT, 'pipeline stage evidence is append-only'); END;

CREATE TRIGGER am_order_intent_pipeline_guard
BEFORE INSERT ON am_order_intent
WHEN NOT EXISTS (
  SELECT 1 FROM am_pipeline_stage_evidence evidence
  WHERE evidence.runtime_run_id=NEW.runtime_run_id
    AND evidence.stage_name='RISK_CONTROL'
    AND evidence.evidence_id=NEW.risk_decision_id
)
BEGIN SELECT RAISE(ABORT, 'order intent requires verified risk-control pipeline evidence'); END;
