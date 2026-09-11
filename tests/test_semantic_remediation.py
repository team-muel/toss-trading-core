from dataclasses import replace
from decimal import Decimal as D
import pytest

from asset_management.calculations.semantic_migration import migrate_legacy_return
from asset_management.data.immutable import canonical, digest
from asset_management.domain.economics import CurrencyBasis
from asset_management.domain.scalars import Currency
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.expectations.engine import expected_return
from asset_management.expectations.equity import EquityGrowthBasis, AGGREGATE_EQUITY_COMPONENTS
from asset_management.expectations.models import AssetClass, ExpectedReturnComponent
from asset_management.risk.contributions import portfolio_risk
from asset_management.risk.models import CovarianceEstimate
from test_phase14_expected_returns import components, required, NOW, VALIDITY


def test_per_share_growth_cannot_add_buyback_again():
    values = components(AssetClass.EQUITY)
    result = expected_return(instrument_id='X', asset_class=AssetClass.EQUITY, components=values, horizon=252, as_of=NOW)
    assert result.gross_expected_return == D('.04')
    assert result.payload()['growth_basis'] == 'PER_SHARE'
    assert result.payload()['schema_version'] == 'expected-return@2'
    values['buyback_yield'] = replace(next(iter(values.values())), component_name='buyback_yield')
    with pytest.raises(DataQualityError, match='COMPONENT_MISMATCH'):
        expected_return(instrument_id='X', asset_class=AssetClass.EQUITY, components=values, horizon=252, as_of=NOW)


def test_aggregate_growth_requires_explicit_basis_and_new_component_names():
    values = {name: ExpectedReturnComponent(name, D('.01'), D('.001'), D('.8'), ('f',), 252, VALIDITY)
              for name in AGGREGATE_EQUITY_COMPONENTS}
    result = expected_return(instrument_id='X', asset_class=AssetClass.EQUITY, components=values,
                             horizon=252, as_of=NOW, growth_basis=EquityGrowthBasis.AGGREGATE)
    assert result.gross_expected_return == D('.05')
    with pytest.raises(DataQualityError):
        expected_return(instrument_id='X', asset_class=AssetClass.EQUITY, components=values, horizon=252, as_of=NOW)
    with pytest.raises(ValueError):
        replace(result, growth_basis=EquityGrowthBasis.PER_SHARE)


@pytest.mark.parametrize('kind', [AssetClass.BOND_ETF, AssetClass.CASH, AssetClass.COMMODITY_ETF])
def test_non_equity_forecasts_have_no_equity_pricing_dependency(kind):
    result = expected_return(instrument_id='X', asset_class=kind, components=components(kind), horizon=252, as_of=NOW)
    assert result.net_expected_return.is_finite()
    assert result.growth_basis is None
    assert 'model_relative_alpha' not in result.payload()


def test_risk_variance_and_volatility_reconcile_with_distinct_units():
    result = portfolio_risk((D('.5'), D('.5')), CovarianceEstimate(
        ((D('.04'), D(0)), (D(0), D('.09'))), 'GOLDEN', 100, True))
    assert abs(sum(result.variance_contribution)-D('.0325')) < D('1e-25')
    assert abs(sum(result.volatility_contribution)-D('.0325').sqrt()) < D('1e-25')
    assert result.payload()['variance_unit'] != result.payload()['volatility_unit']
    assert result.component == result.volatility_contribution  # Explicit legacy meaning.


def migration(raw, **changes):
    return migrate_legacy_return(raw, **(dict(source_hash=digest(canonical(raw)),
        legacy_contract='pricing-result@1', field='required_return', currency=Currency.USD,
        currency_basis=CurrencyBasis.BASE, formula_version='capm@1', reference_version='CAPM@1') | changes))


def test_pricing_migration_retains_original_hash_and_replays_deterministically():
    raw = required().payload()
    original = canonical(raw)
    migrated = migration(raw)
    assert migrated == migration(raw)
    assert migrated['economic_value']['semantic_type'] == 'PRICING_BASELINE_RETURN'
    assert migrated['economic_value']['value'] == raw['required_return']
    assert migrated['source_hash'] == digest(original)
    assert canonical(raw) == original
    with pytest.raises(DataQualityError, match='SOURCE_HASH'):
        migration(raw | {'required_return':'999'}, source_hash=digest(original))
    with pytest.raises(DataQualityError, match='PRICING_HASH'):
        migration(raw | {'required_return':'999'})


def test_generic_alpha_and_legacy_active_meanings_are_not_guessed():
    raw = {'alpha':'.03', 'net_expected_return':'.08', 'required_return':'.05', 'horizon':252}
    record = migration(raw, legacy_contract='alpha-estimate@1', field='alpha')
    assert record['economic_value']['semantic_type'] == 'MODEL_RELATIVE_ALPHA'
    with pytest.raises(DataQualityError, match='ALPHA_IDENTITY'):
        migration(raw | {'alpha':'.04'}, legacy_contract='alpha-estimate@1', field='alpha')
    with pytest.raises(DataQualityError, match='UNSUPPORTED_LEGACY'):
        migration(raw, legacy_contract='unknown@1', field='alpha')
    with pytest.raises(DataQualityError, match='UNSUPPORTED_LEGACY'):
        migration({'ex_ante_active_return':'.03', 'horizon':252}, legacy_contract='unknown@1', field='ex_ante_active_return')


def access_v2(model_id):
    from datetime import date
    from asset_management.governance import ModelDefinition, ModelRegistry, ModelScope, ModelStatus
    registry = ModelRegistry()
    model = ModelDefinition(model_id, '2', 'pricing baseline', ('input',), ('pricing_baseline_return',),
        (ModelScope.PRICING_BASELINE_RETURN,), ('unstable',), date(2026,1,1), date(2026,12,31), 'owner')
    registry.register(model)
    for status in (ModelStatus.VALIDATED, ModelStatus.APPROVED, ModelStatus.ACTIVE):
        registry.transition(model.key, status, effective_at=NOW, reason='explicit new scope', evidence_ids=('approved:evidence',))
    return registry, registry.authorize(model.key, ModelScope.PRICING_BASELINE_RETURN, at=NOW)


def test_canonical_capm_requires_new_scope_and_preserves_equation():
    from asset_management.pricing.capm import capm_pricing_baseline_return
    from asset_management.pricing.models import BetaEstimate
    from asset_management.quality.models import QualityStatus
    from asset_management.domain.errors import InvariantViolation
    from test_phase14_expected_returns import CAPM_REGISTRY, CAPM_AUTH
    registry, authorization = access_v2('CAPM')
    arguments = dict(instrument_id='ETF', risk_free_rate=D('.03'),
        beta=BetaEstimate(D('1.2'), D('1.2'), D('.1'), 252, 252, D('.8'), NOW, QualityStatus.VALID, D(1)),
        market_risk_premium=D('.05'), horizon=252, as_of=NOW, validity=VALIDITY,
        currency=Currency.USD, currency_basis=CurrencyBasis.BASE, asset_scope='EQUITY_ETF',
        model_registry=registry, authorization=authorization)
    output = capm_pricing_baseline_return(**arguments)
    assert D(output['pricing_baseline_return']['value']) == D('.09')
    assert 'required_return' not in output
    assert output['model_key'] == 'CAPM@2'
    assert output == capm_pricing_baseline_return(**arguments)
    with pytest.raises(InvariantViolation):
        capm_pricing_baseline_return(**(arguments | dict(model_registry=CAPM_REGISTRY, authorization=CAPM_AUTH)))
    with pytest.raises(DataQualityError, match='NOT_APPLICABLE'):
        capm_pricing_baseline_return(**(arguments | dict(asset_scope='BOND_ETF')))
    with pytest.raises(DataQualityError, match='NOT_ELIGIBLE'):
        capm_pricing_baseline_return(**(arguments | dict(beta=replace(arguments['beta'], quality=QualityStatus.STALE))))


def test_legacy_pricing_result_cannot_mint_a_v2_payload():
    """A v1 result has no public conversion bypass around the v2 wrapper."""
    from asset_management.pricing.models import _authorized_pricing_baseline_payload
    from asset_management.domain.errors import InvariantViolation
    from test_phase14_expected_returns import CAPM_REGISTRY, CAPM_AUTH

    legacy = required()
    assert not hasattr(legacy, 'economic_payload')
    with pytest.raises(AttributeError):
        legacy.economic_payload(
            currency=Currency.USD, currency_basis=CurrencyBasis.BASE,
            formula_version='capm-pricing-baseline@2', model_key='CAPM@2')
    with pytest.raises(InvariantViolation):
        _authorized_pricing_baseline_payload(
            legacy, currency=Currency.USD, currency_basis=CurrencyBasis.BASE,
            formula_version='capm-pricing-baseline@2', model_key='CAPM@2',
            asset_scope='EQUITY', model_registry=CAPM_REGISTRY, authorization=CAPM_AUTH)


@pytest.mark.parametrize('module_name,function_name,model_key', [
    ('asset_management.pricing.capm', 'capm_pricing_baseline_return', 'CAPM@2'),
    ('asset_management.pricing.factors', 'multifactor_pricing_baseline_return', 'MULTIFACTOR@2'),
])
def test_canonical_pricing_rejects_v1_authority_before_numeric_calculation(
        monkeypatch, module_name, function_name, model_key):
    """No pricing arithmetic may run before the public v2 authority gate."""
    import importlib
    from test_phase14_expected_returns import CAPM_REGISTRY, CAPM_AUTH

    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, '_capm_numeric' if model_key == 'CAPM@2' else '_multifactor_numeric',
                        lambda **_: pytest.fail('numeric pricing ran before authorization'))
    with pytest.raises(InvariantViolation):
        getattr(module, function_name)(
            currency=Currency.USD, currency_basis=CurrencyBasis.BASE, asset_scope='EQUITY',
            model_registry=CAPM_REGISTRY, authorization=CAPM_AUTH, as_of=NOW)


def test_canonical_multifactor_requires_pricing_only_authority():
    from asset_management.pricing.factors import FACTORS, multifactor_pricing_baseline_return
    from asset_management.pricing.models import FactorPremium
    from asset_management.quality.models import QualityStatus
    from asset_management.governance import ModelScope
    from asset_management.domain.errors import InvariantViolation
    registry, authorization = access_v2('MULTIFACTOR')
    output = multifactor_pricing_baseline_return(currency=Currency.USD, currency_basis=CurrencyBasis.BASE,
        asset_scope='EQUITY', model_registry=registry, authorization=authorization,
        instrument_id='X', risk_free_rate=D('.03'), loadings={k:D(1) for k in FACTORS},
        premiums={k:FactorPremium(k,D('.01'),D('.001'),NOW,NOW,'fixture',QualityStatus.VALID) for k in FACTORS},
        horizon=252, as_of=NOW, information_cutoff=NOW, validity=VALIDITY)
    assert abs(D(output['pricing_baseline_return']['value'])-D('.10')) < D('1e-25')
    with pytest.raises(InvariantViolation):
        registry.authorize('MULTIFACTOR@2', ModelScope.EXPECTED_BENCHMARK_ACTIVE_RETURN, at=NOW)
    model = registry.models['MULTIFACTOR@2']
    with pytest.raises(InvariantViolation, match='AUTHORITY_CONFLICT'):
        replace(model, outputs=('pricing_baseline_return','expected_benchmark_active_return'))
    with pytest.raises(InvariantViolation, match='AUTHORITY_CONFLICT'):
        replace(model, approved_scope=(ModelScope.MODEL_RELATIVE_ALPHA, ModelScope.EXPECTED_BENCHMARK_ACTIVE_RETURN),
                outputs=('model_relative_alpha','expected_benchmark_active_return'))


def test_portfolio_state_requires_new_components_but_can_replay_explicit_v1():
    from asset_management.states.portfolio import PortfolioStateEngine, LEGACY_PORTFOLIO_COMPONENTS
    from test_phase12_state_engines import components as state_components, POLICY
    legacy_components = state_components(LEGACY_PORTFOLIO_COMPONENTS)
    old = PortfolioStateEngine(contract_version='portfolio-state@1')
    first = old.build(as_of=NOW, components=legacy_components, policy=POLICY, code_revision='git:abcdef0')
    second = old.build(as_of=NOW, components=legacy_components, policy=POLICY, code_revision='git:abcdef0')
    assert first.state_id == second.state_id
    assert 'risk_contribution' in first.payload()['components']
    with pytest.raises(DataQualityError, match='INCOMPLETE'):
        PortfolioStateEngine().build(as_of=NOW, components=legacy_components, policy=POLICY, code_revision='git:abcdef0')
