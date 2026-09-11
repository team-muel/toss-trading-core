CREATE TRIGGER am_feature_run_no_update BEFORE UPDATE ON am_feature_run
BEGIN SELECT RAISE(ABORT, 'feature runs are append-only'); END;
CREATE TRIGGER am_feature_run_no_delete BEFORE DELETE ON am_feature_run
BEGIN SELECT RAISE(ABORT, 'feature runs are append-only'); END;

CREATE TRIGGER am_state_run_no_update BEFORE UPDATE ON am_state_run
BEGIN SELECT RAISE(ABORT, 'state runs are append-only'); END;
CREATE TRIGGER am_state_run_no_delete BEFORE DELETE ON am_state_run
BEGIN SELECT RAISE(ABORT, 'state runs are append-only'); END;

CREATE TRIGGER am_pricing_run_no_update BEFORE UPDATE ON am_pricing_run
BEGIN SELECT RAISE(ABORT, 'pricing runs are append-only'); END;
CREATE TRIGGER am_pricing_run_no_delete BEFORE DELETE ON am_pricing_run
BEGIN SELECT RAISE(ABORT, 'pricing runs are append-only'); END;

CREATE TRIGGER am_expectation_run_no_update BEFORE UPDATE ON am_expectation_run
BEGIN SELECT RAISE(ABORT, 'expectation runs are append-only'); END;
CREATE TRIGGER am_expectation_run_no_delete BEFORE DELETE ON am_expectation_run
BEGIN SELECT RAISE(ABORT, 'expectation runs are append-only'); END;

CREATE TRIGGER am_risk_model_run_no_update BEFORE UPDATE ON am_risk_model_run
BEGIN SELECT RAISE(ABORT, 'risk model runs are append-only'); END;
CREATE TRIGGER am_risk_model_run_no_delete BEFORE DELETE ON am_risk_model_run
BEGIN SELECT RAISE(ABORT, 'risk model runs are append-only'); END;

CREATE TRIGGER am_portfolio_target_no_update BEFORE UPDATE ON am_portfolio_target
BEGIN SELECT RAISE(ABORT, 'portfolio targets are append-only'); END;
CREATE TRIGGER am_portfolio_target_no_delete BEFORE DELETE ON am_portfolio_target
BEGIN SELECT RAISE(ABORT, 'portfolio targets are append-only'); END;
