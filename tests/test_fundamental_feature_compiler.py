"""Synthetic reconciled sources, not company forecasts or investment advice."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.time.asof import AsOfContext
from asset_management.domain.errors import AssetManagementError as DomainError
from research_platform.fundamental_features import FundamentalSpec, compile_fundamental_features, _hash
import test_variant_perception as original

T = datetime(2026, 8, 20, 21, tzinfo=timezone.utc)
ID = "00000000-0000-0000-0000-000000000001"


def fixture(tmp_path, mutate=None, source_mutate=None):
    old = original.VariantPerceptionTest(); old.setUp()
    policy = deepcopy(old.policy)
    # Existing model fixtures intentionally contain future fiscal labels. This
    # PIT adapter fixture instead binds completed FY2025/FY2026 to actual dates.
    raw = json.dumps(old.payload).replace("FY2026", "__PRIOR__").replace("FY2027", "FY2026").replace("__PRIOR__", "FY2025")
    analysis = json.loads(raw)
    analysis['as_of_date'] = T.date().isoformat()
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    mapping = {}
    for index, source in enumerate(analysis['sources']):
        published = datetime.fromisoformat(source['observed_at']).replace(hour=18,tzinfo=timezone.utc)
        evidence = {'identity': deepcopy(source), 'content': 'Explicitly synthetic source fixture.', 'published_at': published.isoformat()}
        if source_mutate:
            source_mutate(evidence, index)
        m = store.write(evidence, source=source['organization'], dataset='source-evidence', layer='bronze',
            schema_version='fundamental-source-evidence-v1', retrieved_at=T-timedelta(hours=2),
            available_at=T-timedelta(hours=2), provider_timestamp=published,
            license_tag='purpose=synthetic;redistribution=forbidden;retention=perpetual',
            code_revision='synthetic-test', request_hash=sha256(str(index).encode()).hexdigest())
        mapping[source['source_id']] = m.manifest_id
    proposal = {'instrument_id': ID, 'analysis': analysis, 'source_manifest_ids': mapping,
        'period_contract': {'prior_label':'FY2025','current_label':'FY2026','forecast_label':'FY2028',
            'prior_end':'2025-07-31','current_end':'2026-07-31','forecast_end':'2028-07-31',
            'currency':'USD','forecast_eps_basis':'ADJUSTED','consensus_eps_basis':'ADJUSTED','implied_eps_basis':'ADJUSTED'}}
    if mutate:
        mutate(proposal)
    m = store.write(proposal, source='research-proposal', dataset='focused-research-proposal', layer='bronze',
        schema_version='fundamental-proposal-v1', retrieved_at=T-timedelta(hours=1), available_at=T-timedelta(hours=1),
        provider_timestamp=T-timedelta(hours=1), license_tag='purpose=synthetic;redistribution=forbidden;retention=perpetual',
        code_revision='synthetic-test', request_hash='a'*64)
    spec = FundamentalSpec(ID, analysis['symbol'], m.manifest_id, _hash(policy), 'fundamental-v1')
    context = AsOfContext('synthetic',T,T-timedelta(minutes=5),spec.policy_version,spec.spec_hash,'synthetic-test')
    return spec, store, context, policy


def test_reuses_reconciled_dossier_and_emits_only_typed_research_inputs(tmp_path):
    args = fixture(tmp_path)
    run = compile_fundamental_features(*args)
    assert compile_fundamental_features(*args).evidence_hash == run.evidence_hash
    payload = run.payload()
    assert payload['validation_scope'] == 'MECHANISM_ONLY'
    assert not payload['forecast_authorized'] and not payload['execution_authorized']
    assert not payload['source_interpretation_verified']
    assert len(payload['numeric_features']) == 7
    assert not {'recommendation','position_weight','expected_return'} & set(payload)
    assert all(value.available_at == T for value in run.feature_inputs())
    assert payload['source_evidence']


@pytest.mark.parametrize('field,value', [('currency','KRW'),('forecast_eps_basis','GAAP'),
    ('prior_end','2026-08-01'),('current_end','2027-07-31'),('forecast_end','2026-07-31'),('current_label','FY2099')])
def test_rejects_currency_basis_and_fiscal_period_mismatches(tmp_path,field,value):
    args = fixture(tmp_path, mutate=lambda p: p['period_contract'].__setitem__(field,value))
    with pytest.raises((ValueError, DomainError)): compile_fundamental_features(*args)


@pytest.mark.parametrize('eps',[0,-1,0.001,True])
def test_bad_eps_denominators_are_not_backfilled(tmp_path,eps):
    args = fixture(tmp_path, mutate=lambda p: p['analysis']['earnings_model'].__setitem__('consensus_eps_usd',eps))
    with pytest.raises((ValueError, DomainError)): compile_fundamental_features(*args)


def test_source_identity_cannot_be_fabricated_by_reusing_a_manifest(tmp_path):
    def mutate(p):
        keys=list(p['source_manifest_ids']);p['source_manifest_ids'][keys[1]]=p['source_manifest_ids'][keys[0]]
    with pytest.raises((ValueError, DomainError)): compile_fundamental_features(*fixture(tmp_path,mutate=mutate))


@pytest.mark.parametrize('source_mutation', ['content','identity','timestamp'])
def test_source_artifact_must_match_the_claimed_source(tmp_path,source_mutation):
    def mutate(e,index):
        if index: return
        if source_mutation=='content': e['content']=' '
        elif source_mutation=='identity': e['identity']['organization']='unrelated'
        else: e['published_at']='2026-08-20T18:00:00'
    with pytest.raises((ValueError, DomainError)): compile_fundamental_features(*fixture(tmp_path,source_mutate=mutate))


def test_source_reference_coverage_is_exact(tmp_path):
    def mutate(p): p['source_manifest_ids'].pop(next(iter(p['source_manifest_ids'])))
    with pytest.raises((ValueError, DomainError)): compile_fundamental_features(*fixture(tmp_path,mutate=mutate))


def test_policy_and_context_are_not_substitutable(tmp_path):
    spec,store,context,policy=fixture(tmp_path)
    with pytest.raises((ValueError, DomainError)): compile_fundamental_features(spec,store,replace(context,parameter_set_id='other'),policy)
    with pytest.raises((ValueError, DomainError)): compile_fundamental_features(spec,store,context,{**policy,'minimum_variant_chains':999})


def test_underlying_cash_flow_reconciliation_is_still_enforced(tmp_path):
    def mutate(p): p['analysis']['earnings_quality']['earnings_bridge']['current']['reported_gaap_net_income_usd_millions']=9999
    with pytest.raises((ValueError, DomainError)): compile_fundamental_features(*fixture(tmp_path,mutate=mutate))


def test_future_collected_proposal_cannot_replay_at_past_cutoff(tmp_path):
    spec,store,context,policy=fixture(tmp_path)
    with pytest.raises((ValueError, DomainError)): compile_fundamental_features(spec,store,replace(context,information_cutoff_utc=T-timedelta(days=1)),policy)


def test_source_hash_tampering_fails_closed(tmp_path):
    spec,store,context,policy=fixture(tmp_path)
    manifest,_=store.read(spec.proposal_manifest_id)
    store.layout.resolve('bronze',manifest.content_sha256+'.json').write_text('{}')
    with pytest.raises((ValueError, DomainError)): compile_fundamental_features(spec,store,context,policy)
