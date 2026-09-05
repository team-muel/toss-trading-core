ALTER TABLE am_reconciliation_tolerance_policy
ADD COLUMN max_age_seconds INTEGER CHECK (max_age_seconds > 0);

ALTER TABLE am_order_intent
ADD COLUMN account_id TEXT;

ALTER TABLE am_order_intent
ADD COLUMN runtime_run_id TEXT REFERENCES am_runtime_run(runtime_run_id);

DROP TRIGGER IF EXISTS am_order_intent_reconciliation_guard;

CREATE TRIGGER am_order_intent_reconciliation_guard
BEFORE INSERT ON am_order_intent
WHEN NEW.account_id IS NULL OR length(trim(NEW.account_id)) = 0
  OR NEW.runtime_run_id IS NULL OR length(trim(NEW.runtime_run_id)) = 0
  OR NOT EXISTS (
    SELECT 1
    FROM am_order_intent_reconciliation_authorization a
    JOIN am_account_reconciliation_v2 rr
      ON rr.reconciliation_run_id=a.reconciliation_run_id
    JOIN am_reconciliation_tolerance_policy policy
      ON policy.tolerance_policy_version=rr.tolerance_policy_version
    WHERE a.order_intent_id=NEW.order_intent_id
      AND a.account_id=NEW.account_id
      AND rr.account_id=NEW.account_id
      AND rr.runtime_run_id=NEW.runtime_run_id
      AND policy.max_age_seconds IS NOT NULL
      AND julianday(a.authorized_at_utc) IS NOT NULL
      AND julianday(rr.completed_at_utc) IS NOT NULL
      AND julianday(a.authorized_at_utc) >= julianday(rr.completed_at_utc)
      AND (julianday(a.authorized_at_utc) - julianday(rr.completed_at_utc)) * 86400.0
          <= policy.max_age_seconds
      AND rr.reconciliation_run_id=(
        SELECT newest.reconciliation_run_id
        FROM am_account_reconciliation_v2 newest
        WHERE newest.account_id=NEW.account_id
        ORDER BY newest.completed_at_utc DESC, newest.reconciliation_run_id DESC LIMIT 1
      )
      AND NOT EXISTS (
        SELECT 1 FROM am_reconciliation_item_v2 item
        WHERE item.reconciliation_run_id=rr.reconciliation_run_id
          AND item.status IN ('MISMATCH','UNVERIFIABLE','BLOCKED')
      )
      AND NOT EXISTS (
        SELECT 1 FROM am_reconciliation_issue_v2 issue
        LEFT JOIN am_reconciliation_resolution_v2 resolution USING(issue_id)
        WHERE issue.account_id=NEW.account_id AND resolution.issue_id IS NULL
      )
      AND EXISTS (
        SELECT 1
        FROM am_risk_decision decision
        JOIN am_portfolio_target target USING(portfolio_target_id)
        JOIN am_expectation_run expectation USING(expectation_run_id)
        JOIN am_pricing_run pricing USING(pricing_run_id)
        JOIN am_state_run state USING(state_run_id)
        JOIN am_feature_run feature USING(feature_run_id)
        JOIN am_risk_model_run risk_model USING(risk_model_run_id)
        JOIN am_state_run risk_state
          ON risk_state.state_run_id=risk_model.state_run_id
        JOIN am_feature_run risk_feature
          ON risk_feature.feature_run_id=risk_state.feature_run_id
        WHERE decision.risk_decision_id=NEW.risk_decision_id
          AND feature.runtime_run_id=NEW.runtime_run_id
          AND risk_feature.runtime_run_id=NEW.runtime_run_id
      )
  )
BEGIN
  SELECT RAISE(ABORT, 'order intent requires fresh account-bound reconciliation authorization');
END;
