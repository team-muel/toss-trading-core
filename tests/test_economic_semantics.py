from dataclasses import replace
from decimal import Decimal as D
import json
from pathlib import Path
import pytest

from asset_management.domain.economics import (
    EconomicValue, EconomicUnit, CurrencyBasis, ReturnSemanticType as R,
    ReturnMetricStatus as S, ReturnUnit as U, RiskContributionType,
    BalanceSemanticType as B, model_relative_alpha,
    expected_benchmark_active_return, executable_buy_limit, require_same_unit,
)
from asset_management.domain.scalars import Currency
from asset_management.domain.errors import DataQualityError


def metric(**changes):
    return EconomicValue(**(dict(semantic_type=R.FORECAST_TOTAL_RETURN_NET, value=D('.08'),
        status=S.AVAILABLE, currency=Currency.USD, currency_basis=CurrencyBasis.BASE,
        forecast_horizon=252, unit=U.TOTAL_RETURN, formula_version='forecast@1',
        reference_version='model@1') | changes))


def test_model_residual_is_distinct_from_benchmark_active():
    forecast = metric()
    baseline = metric(semantic_type=R.PRICING_BASELINE_RETURN, value=D('.05'), reference_version='capm@1')
    residual = model_relative_alpha(forecast, baseline, formula_version='residual@1')
    assert residual.value == D('.03')
    assert residual.semantic_type is R.MODEL_RELATIVE_ALPHA
    active = expected_benchmark_active_return({'A': forecast, 'B': metric(value=D('.02'))},
        {'B': D('.25'), 'A': D('.75')}, {'A': D('.5'), 'B': D('.5')},
        benchmark_version='policy-benchmark@1', formula_version='active@1')
    assert active.value == D('.015')
    assert active.semantic_type is R.EXPECTED_BENCHMARK_ACTIVE_RETURN
    assert active.reference_version == 'policy-benchmark@1'
    with pytest.raises(DataQualityError, match='SEMANTICS'):
        expected_benchmark_active_return({'A': residual}, {'A': D(1)}, {'A': D(1)},
            benchmark_version='b@1', formula_version='a@1')


@pytest.mark.parametrize('change', [dict(currency=Currency.KRW), dict(currency_basis=CurrencyBasis.HEDGED),
    dict(forecast_horizon=21)])
def test_mixed_context_is_rejected(change):
    baseline = metric(semantic_type=R.PRICING_BASELINE_RETURN, **change)
    with pytest.raises(DataQualityError, match='CONTEXT_MISMATCH'):
        model_relative_alpha(metric(), baseline, formula_version='r@1')


@pytest.mark.parametrize('change', [dict(value=D('NaN')), dict(value=0.1), dict(value=None),
    dict(semantic_type='alpha'), dict(unit=U.ACTIVE_RETURN), dict(currency='USD'),
    dict(formula_version=''), dict(reference_version=''), dict(forecast_horizon=True),
    dict(status=S.NOT_APPLICABLE), dict(status=S.NOT_MATURED)])
def test_invalid_or_ambiguous_values_fail_closed(change):
    with pytest.raises(DataQualityError):
        metric(**change)


def test_missing_pricing_baseline_does_not_become_zero():
    unavailable = metric(semantic_type=R.PRICING_BASELINE_RETURN, status=S.NOT_APPLICABLE, value=None)
    assert unavailable.payload()['value'] is None
    with pytest.raises(DataQualityError, match='UNAVAILABLE'):
        model_relative_alpha(metric(), unavailable, formula_version='r@1')


def test_cash_and_broker_constraint_are_not_nav():
    cash = metric(semantic_type=B.INTERNAL_FREE_CASH, unit=EconomicUnit.MONEY, value=D(100))
    power = replace(cash, semantic_type=B.BROKER_BUYING_POWER, value=D(80))
    result = executable_buy_limit(cash, power, formula_version='limit@1')
    assert result.value == 80 and result.semantic_type is B.EXECUTABLE_BUY_LIMIT
    with pytest.raises(DataQualityError, match='SEMANTICS'):
        executable_buy_limit(replace(cash, semantic_type=B.ACCOUNTING_NAV), power, formula_version='limit@1')
    with pytest.raises(DataQualityError, match='COMPARISON_UNIT'):
        require_same_unit(cash, metric())


def test_variance_and_volatility_have_different_units():
    variance = metric(semantic_type=RiskContributionType.VARIANCE, unit=EconomicUnit.RETURN_SQUARED)
    volatility = metric(semantic_type=RiskContributionType.VOLATILITY, unit=EconomicUnit.RETURN)
    with pytest.raises(DataQualityError, match='COMPARISON_UNIT'):
        require_same_unit(variance, volatility)
    with pytest.raises(DataQualityError, match='UNIT_MISMATCH'):
        replace(variance, unit=EconomicUnit.RETURN)


@pytest.mark.parametrize('weights', [{'B': D(1)}, {'A': D('.9')}, {'A': D('NaN')}])
def test_benchmark_keys_and_weights_must_match(weights):
    with pytest.raises(DataQualityError, match='WEIGHTS'):
        expected_benchmark_active_return({'A': metric()}, weights, {'A': D(1)},
            benchmark_version='b@1', formula_version='a@1')


def test_existing_imports_share_enum_identity_and_wire_format():
    from asset_management.decisions.economic_journal import ReturnSemanticType, ReturnMetric, ReturnMetricStatus
    from asset_management.risk.models import CurrencyBasis as OldCurrencyBasis
    assert ReturnSemanticType is R and ReturnMetricStatus is S and OldCurrencyBasis is CurrencyBasis
    old = ReturnMetric(R.FORECAST_TOTAL_RETURN_NET, S.AVAILABLE, D('.08'), CurrencyBasis.BASE,
                       252, U.TOTAL_RETURN, 'f@1', 'm@1')
    assert ReturnMetric.from_payload(old.payload()).payload() == old.payload()


def test_schema_covers_all_semantics_and_metadata():
    schema = json.loads((Path(__file__).parents[1]/'schemas/economic_value.schema.json').read_text())
    enums = {v.value for t in (R, RiskContributionType, B) for v in t}
    assert set(schema['properties']['semantic_type']['enum']) == enums
    assert set(metric().payload()) == set(schema['required'])
