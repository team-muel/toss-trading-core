from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.config.versions import content_hash
from asset_management.domain.errors import InvariantViolation, NoTrade
from asset_management.orchestration.pipelines import (
    InvestmentPipeline,
    PipelineStage,
    StageEvidence,
)
from asset_management.time.clock import FrozenClock


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)


def database() -> tuple[sqlite3.Connection, dict[PipelineStage, StageEvidence]]:
    conn = sqlite3.connect(":memory:")
    Migrator(conn, FrozenClock(NOW)).migrate(load_migration_catalog(ROOT / "schemas"))
    stamp = NOW.isoformat()
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("run-1", stamp, stamp, "sha", stamp))
    conn.execute(
        "INSERT INTO am_raw_api_response VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("raw", "test", "/account", "GET", "request", 200, "response", "{}",
         stamp, stamp, "account-1", "v1", "{}"),
    )
    account_payload = json.dumps(
        {"raw_response_ids": ["raw"]}, sort_keys=True, separators=(",", ":")
    )
    account_hash = sha256(account_payload.encode()).hexdigest()
    conn.execute(
        "INSERT INTO am_account_snapshot VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("account", "run-1", "account-1", stamp, "raw", account_payload, account_hash),
    )
    conn.execute("INSERT INTO am_account_snapshot_raw VALUES ('account','raw')")
    conn.execute(
        "INSERT INTO am_policy_version VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("investment-v1", "investment", stamp, None, "owner", "approved", "{}", "policy-hash"),
    )
    conn.execute("INSERT INTO am_ingestion_run VALUES ('ingest','run-1','test',?,?)", (stamp, stamp))
    conn.execute(
        "INSERT INTO am_dataset_manifest VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("manifest", "ingest", "silver", "prices", "silver/prices", "manifest-hash",
         stamp, stamp, "v1", 1),
    )
    conn.execute(
        "INSERT INTO am_feature_run VALUES ('feature','run-1','manifest','v1','{}','feature-hash')"
    )
    conn.execute("INSERT INTO am_state_run VALUES ('state','feature','v1','{}','state-hash')")
    conn.execute("INSERT INTO am_pricing_run VALUES ('pricing','state','v1','{}','pricing-hash')")
    conn.execute(
        "INSERT INTO am_expectation_run VALUES ('expectation','pricing','v1','{}','expectation-hash')"
    )
    conn.execute("INSERT INTO am_risk_model_run VALUES ('risk-model','state','v1','{}','risk-hash')")
    conn.execute(
        "INSERT INTO am_parameter_set VALUES ('params',?,'{}','params-hash')", (stamp,)
    )
    conn.execute(
        "INSERT INTO am_portfolio_target VALUES (?,?,?,?,?,?,?)",
        ("target", "expectation", "risk-model", "investment-v1", "params", "{}", "target-hash"),
    )
    conn.execute(
        "INSERT INTO am_risk_decision VALUES (?,?,?,?,?,?)",
        ("decision", "target", "ALLOW", "[]", "investment-v1", "decision-hash"),
    )
    time_hash = content_hash({
        "runtime_run_id": "run-1", "as_of_utc": stamp,
        "information_cutoff_utc": stamp, "code_revision": "sha",
    })
    evidence = {
        PipelineStage.INVESTMENT_POLICY: StageEvidence(
            PipelineStage.INVESTMENT_POLICY, "investment-v1", "policy-hash"
        ),
        PipelineStage.ACCOUNT_TRUTH: StageEvidence(
            PipelineStage.ACCOUNT_TRUTH, "account", account_hash
        ),
        PipelineStage.TIME_TRUTH: StageEvidence(PipelineStage.TIME_TRUTH, "run-1", time_hash),
        PipelineStage.DATA_TRUTH: StageEvidence(
            PipelineStage.DATA_TRUTH, "manifest", "manifest-hash"
        ),
        PipelineStage.FINANCIAL_CALCULATION: StageEvidence(
            PipelineStage.FINANCIAL_CALCULATION, "expectation", "expectation-hash"
        ),
        PipelineStage.TARGET_PORTFOLIO: StageEvidence(
            PipelineStage.TARGET_PORTFOLIO, "target", "target-hash"
        ),
        PipelineStage.RISK_CONTROL: StageEvidence(
            PipelineStage.RISK_CONTROL, "decision", "decision-hash"
        ),
    }
    return conn, evidence


def test_pipeline_cannot_skip_policy_or_use_fabricated_evidence():
    conn, evidence = database()
    pipeline = InvestmentPipeline.start(conn, "run-1")
    with pytest.raises(InvariantViolation, match="required next stage"):
        pipeline.complete(evidence[PipelineStage.ACCOUNT_TRUTH])
    with pytest.raises(InvariantViolation, match="hash does not match"):
        pipeline.complete(StageEvidence(
            PipelineStage.INVESTMENT_POLICY, "investment-v1", "fabricated"
        ))
    pipeline.complete(evidence[PipelineStage.INVESTMENT_POLICY])
    with pytest.raises(InvariantViolation, match="required next stage"):
        pipeline.complete(evidence[PipelineStage.INVESTMENT_POLICY])
    assert pipeline.next_stage is PipelineStage.ACCOUNT_TRUTH


def test_order_is_blocked_until_persisted_risk_control_evidence_exists():
    conn, evidence = database()
    pipeline = InvestmentPipeline.start(conn, "run-1")
    for stage in PipelineStage:
        if stage in {PipelineStage.RISK_CONTROL, PipelineStage.ORDER}:
            break
        pipeline.complete(evidence[stage])
    with pytest.raises(NoTrade):
        pipeline.require_order_authorized()
    pipeline.complete(evidence[PipelineStage.RISK_CONTROL])
    pipeline.require_order_authorized()
    assert pipeline.next_stage is PipelineStage.ORDER
    assert InvestmentPipeline.start(conn, "run-1").completed == pipeline.completed


def test_database_rejects_out_of_order_or_fabricated_pipeline_rows():
    conn, evidence = database()
    with pytest.raises(sqlite3.IntegrityError, match="contiguous"):
        conn.execute(
            "INSERT INTO am_pipeline_stage_evidence VALUES (?,?,?,?,?)",
            ("run-1", 2, "ACCOUNT_TRUTH", "account", evidence[PipelineStage.ACCOUNT_TRUTH].content_hash),
        )
    with pytest.raises(sqlite3.IntegrityError, match="runtime artifact"):
        conn.execute(
            "INSERT INTO am_pipeline_stage_evidence VALUES ('run-1',1,'INVESTMENT_POLICY','fake','fake')"
        )
