"""Aligned total-return panels without imputation."""
from datetime import date
from decimal import Decimal
from typing import Mapping, Sequence
from asset_management.domain.errors import DataQualityError
from .models import CurrencyBasis, MissingPolicy, ReturnPanel


def require_common_risk_context(*, expected_return_currency: CurrencyBasis,
                                risk_free_currency: CurrencyBasis, covariance_currency: CurrencyBasis,
                                factor_return_currency: CurrencyBasis, active_return_currency: CurrencyBasis,
                                return_horizon_days: int, risk_free_horizon_days: int,
                                covariance_horizon_days: int, factor_horizon_days: int,
                                active_horizon_days: int) -> None:
    """Prevent silent mixing of FX/return-model currencies or horizons."""
    currencies = (expected_return_currency, risk_free_currency, covariance_currency,
                  factor_return_currency, active_return_currency)
    horizons = (return_horizon_days, risk_free_horizon_days, covariance_horizon_days,
                factor_horizon_days, active_horizon_days)
    if any(not isinstance(item, CurrencyBasis) for item in currencies) or any(type(item) is not int or item < 1 for item in horizons):
        raise DataQualityError("RISK_CONTEXT_INVALID")
    if len(set(currencies)) != 1:
        raise DataQualityError("RISK_CURRENCY_BASIS_MISMATCH")
    if len(set(horizons)) != 1:
        raise DataQualityError("RISK_HORIZON_MISMATCH")

def build_return_panel(*, instruments: Sequence[str], observations: Mapping[date,Mapping[str,Decimal|None]],
                       total_return: bool, currency_basis: CurrencyBasis,
                       missing_policy: MissingPolicy=MissingPolicy.FAIL,
                       listing_dates: Mapping[str,date]|None=None,
                       winsor_limits: tuple[Decimal,Decimal]|None=None,
                       periods_per_year: int=252) -> ReturnPanel:
    if not total_return: raise DataQualityError("TOTAL_RETURN_BASIS_REQUIRED")
    if winsor_limits is not None and winsor_limits[0]>=winsor_limits[1]:
        raise DataQualityError("OUTLIER_POLICY_INVALID")
    rows=[]; dates=[]; dropped=0; prelisting=0
    for day in sorted(observations):
        if listing_dates and any(day<listing_dates[name] for name in instruments):
            prelisting+=1; continue
        source=observations[day]
        if any(name not in source or source[name] is None for name in instruments):
            if missing_policy is MissingPolicy.FAIL: raise DataQualityError("RETURN_PANEL_MISSING_DATA")
            dropped+=1; continue
        row=tuple(source[name] for name in instruments)
        if any(not isinstance(x,Decimal) or not x.is_finite() for x in row):
            raise DataQualityError("RETURN_PANEL_VALUE_INVALID")
        if winsor_limits is not None:
            row=tuple(min(winsor_limits[1],max(winsor_limits[0],x)) for x in row)
        dates.append(day); rows.append(row)
    if len(rows)<2: raise DataQualityError("RETURN_PANEL_HISTORY_INSUFFICIENT")
    return ReturnPanel(tuple(instruments),tuple(dates),tuple(rows),total_return,currency_basis,
                       missing_policy,dropped,"WINSORIZED" if winsor_limits else "NONE",prelisting,
                       periods_per_year)

def decompose_base_currency_return(local_return: Decimal, fx_return: Decimal) -> tuple[Decimal,Decimal,Decimal]:
    """Exact base return: (1+r_local)(1+r_fx)-1, preserving FX separately."""
    if any(not x.is_finite() or x<=-1 for x in (local_return,fx_return)):
        raise DataQualityError("FX_RETURN_INVALID")
    return local_return,fx_return,(1+local_return)*(1+fx_return)-1
