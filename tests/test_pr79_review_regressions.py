"""Negative and positive controls for the PR #79 convergence boundary."""
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from asset_management.cli.runtime_validate import main as validate_runtime
from asset_management.compatibility.account_evidence import HistoricalAccountReader
from asset_management.compatibility.account_evidence.audit import build_parser
from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import DataQualityError
from asset_management.pricing import RiskFreeCurve, RiskFreePoint, materialize_usd_fred_risk_free_curve
from asset_management.quality.models import QualityStatus
from asset_management.validation import (RiskFreeRuntimeEvidence, FactorRiskRuntimeEvidence,
    CheckEvidence, RUNTIME_D2_CHECKS, REQUIRED_PRICING_EXPECTATION_RISK_CHECKS,
    assemble_d2_runtime_evidence, build_d2_gate_input)
from asset_management.risk import publish_factor_risk_evidence
from test_factor_risk_evidence import assessment, policy, raw_tiingo, NOW, LICENSE


def test_validator_defaults_do_not_depend_on_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    # An unrelated current directory must not replace package config defaults.
    (tmp_path/'config').mkdir()
    (tmp_path/'config/application.yaml').write_text('not the application configuration')
    assert validate_runtime([]) == 0
    assert 'runtime_mode=READ_ONLY' in capsys.readouterr().out


@pytest.mark.parametrize('mode', ['DELETE', 'WAL'])
def test_historical_reader_does_not_mutate_evidence_bytes_or_journal(tmp_path, mode):
    path=tmp_path/'historical evidence ? .sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute(f'PRAGMA journal_mode={mode}')
        conn.execute('CREATE TABLE evidence(value TEXT)')
        conn.execute("INSERT INTO evidence VALUES ('synthetic')")
    conn.close()
    before=path.read_bytes()
    files=set(tmp_path.iterdir())
    path.chmod(0o444)
    reader=HistoricalAccountReader(path)
    try:
        assert reader.conn.execute('SELECT value FROM evidence').fetchone()[0]=='synthetic'
        with pytest.raises(sqlite3.OperationalError):
            reader.conn.execute("INSERT INTO evidence VALUES ('not allowed')")
    finally:
        reader.close()
    assert sha256(path.read_bytes()).digest()==sha256(before).digest()
    assert set(tmp_path.iterdir())==files


def test_nonfinal_journal_is_rejected_not_removed(tmp_path):
    path=tmp_path/'archive.sqlite'
    sqlite3.connect(path).close()
    journal=Path(str(path)+'-wal')
    journal.write_bytes(b'active-journal-marker')
    with pytest.raises(ValueError,match='finalized snapshot'):
        HistoricalAccountReader(path)
    assert journal.read_bytes()==b'active-journal-marker'


def test_historical_audit_keeps_deployed_default():
    assert build_parser().parse_args([]).db=='runtime/foundation_account_state.sqlite'


def test_historical_compatibility_has_no_fresh_collector():
    import importlib.util
    import asset_management.compatibility.account_evidence as boundary
    assert not hasattr(boundary,'AccountEvidenceSnapshotter')
    assert importlib.util.find_spec('asset_management.compatibility.account_evidence.snapshot') is None
    assert not hasattr(HistoricalAccountReader,'snapshot')


def write_curve(store, body, *, schema_version='fred-risk-free-curve@1', quality_status='RAW'):
    return store.write(body,layer='bronze',source='fred-alfred',dataset='risk-free-curve',
        schema_version=schema_version,retrieved_at=NOW,available_at=NOW,provider_timestamp=NOW,
        license_tag=LICENSE,code_revision='test',request_hash='b'*64,quality_status=quality_status).manifest_id


def curve_body():
    return {'observations':[
        {'series_id':s,'as_of':NOW.isoformat(),'available_at':NOW.isoformat(),'value_percent':'4'}
        for s in ('DGS1MO','DGS3MO','DGS6MO','DGS1')]}


def test_curve_with_empty_artifact_cannot_pass(tmp_path):
    from decimal import Decimal
    store=ImmutableDatasetStore(tmp_path)
    mid=write_curve(store, {'observations':[]})
    curve=RiskFreeCurve(tuple(RiskFreePoint(NOW,NOW,h,Decimal('.04'),'fred-alfred',mid,QualityStatus.VALID)
                              for h in (21,63,126,252)))
    evidence=RiskFreeRuntimeEvidence(curve,'USD',NOW,store)
    result=assemble_d2_runtime_evidence(risk_free=evidence,factor_risk=None,model_lineage=None)
    assert not result.checks['RISK_FREE_CURRENCY_HORIZON_COMPOUNDING_VERIFIED'].passed


def test_curve_rates_are_recalculated_not_just_metadata_checked(tmp_path):
    from decimal import Decimal
    store=ImmutableDatasetStore(tmp_path)
    mid=write_curve(store, curve_body())
    curve=materialize_usd_fred_risk_free_curve(store=store,manifest_id=mid,information_cutoff=NOW)
    assert RiskFreeRuntimeEvidence(curve,'USD',NOW,store).check().passed
    forged=RiskFreeCurve(tuple(RiskFreePoint(NOW,NOW,h,Decimal('.99'),'fred-alfred',mid,QualityStatus.VALID)
                              for h in (21,63,126,252)))
    with pytest.raises(ValueError,match='NOT_BOUND'):
        RiskFreeRuntimeEvidence(forged,'USD',NOW,store).check()


@pytest.mark.parametrize(('schema_version','quality_status'), [
    ('unapproved-schema','RAW'),
    ('fred-risk-free-curve@1','VALID'),
])
def test_curve_unapproved_manifest_contract_cannot_pass_d2(tmp_path, schema_version, quality_status):
    store=ImmutableDatasetStore(tmp_path)
    mid=write_curve(store, curve_body(), schema_version=schema_version, quality_status=quality_status)
    from decimal import Decimal
    curve=RiskFreeCurve(tuple(RiskFreePoint(NOW,NOW,h,Decimal('.04'),'fred-alfred',mid,QualityStatus.VALID)
                              for h in (21,63,126,252)))
    result=assemble_d2_runtime_evidence(
        risk_free=RiskFreeRuntimeEvidence(curve,'USD',NOW,store),factor_risk=None,model_lineage=None)
    name='RISK_FREE_CURRENCY_HORIZON_COMPOUNDING_VERIFIED'
    assert not result.checks[name].passed
    assert result.failure_reasons[name]=='RISK_FREE_MANIFEST_CONTEXT_INVALID'


def test_unrelated_tiingo_parent_cannot_approve_factor_risk(tmp_path):
    store=ImmutableDatasetStore(tmp_path)
    parent=raw_tiingo(store)  # Legacy fixture deliberately lacks actual returns.
    published=publish_factor_risk_evidence(store=store,assessment=assessment(),policy=policy(),
        source_manifest_ids=(parent,),published_at=NOW,code_revision='test')
    manifest,body=store.read(published.manifest_id)
    assert manifest.schema_version=='factor-specific-risk-evidence@2'
    assert body['validation_scope']=='MECHANISM_ONLY'
    assert body['estimation_lineage_verified'] is False
    assert body['covariance']==[['0.05']]
    with pytest.raises(ValueError,match='ESTIMATION_LINEAGE_UNVERIFIED'):
        FactorRiskRuntimeEvidence(assessment(),policy(),NOW,published.manifest_id,store).check()


def test_factor_policy_version_mismatch_cannot_be_published(tmp_path):
    store=ImmutableDatasetStore(tmp_path)
    with pytest.raises(DataQualityError,match='POLICY_VERSION_MISMATCH'):
        publish_factor_risk_evidence(store=store,assessment=assessment(),
            policy=replace(policy(),estimation_version='unrelated-version'),
            source_manifest_ids=(raw_tiingo(store),),published_at=NOW,code_revision='test')


def test_fake_runtime_source_is_not_trusted():
    fake=SimpleNamespace(check=lambda:CheckEvidence(True,('arbitrary',)))
    result=assemble_d2_runtime_evidence(risk_free=fake,factor_risk=fake,model_lineage=fake)
    assert all(not check.passed for check in result.checks.values())
    assert set(result.failure_reasons.values())=={'EVIDENCE_TYPE_INVALID'}


def test_builder_binds_cutoff_and_recomputes_missing_runtime_checks(tmp_path):
    static={name:CheckEvidence(True,('static-evidence',))
            for name in set(REQUIRED_PRICING_EXPECTATION_RISK_CHECKS)-RUNTIME_D2_CHECKS}
    result=build_d2_gate_input(evaluated_at=NOW,code_revision='test',static_checks=static)
    assert all(not result.checks[name].passed for name in RUNTIME_D2_CHECKS)
    evidence=RiskFreeRuntimeEvidence(None,'USD',NOW-timedelta(days=1),ImmutableDatasetStore(tmp_path))
    with pytest.raises(ValueError,match='EVALUATION_TIME_MISMATCH'):
        build_d2_gate_input(evaluated_at=NOW,code_revision='test',static_checks=static,risk_free=evidence)