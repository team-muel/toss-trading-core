"""Canonical pricing-baseline models plus explicit v1 replay compatibility.

``*_pricing_baseline_return`` is the only current pricing authority.  The
``*_required_return`` exports retain pricing-result@1 replay and migration
compatibility; they must not be used for new decision assembly.
"""
from .black_litterman import BlackLittermanView, equilibrium_returns, posterior_returns
from .capm import ReturnObservation, capm_required_return, capm_pricing_baseline_from_risk_free, capm_pricing_baseline_return, estimate_beta
from .factors import FACTORS, multifactor_pricing_baseline_from_risk_free, multifactor_required_return, multifactor_pricing_baseline_return, require_distinct_factor_roles
from .models import BetaEstimate, FactorPremium, HORIZONS, PricingResult, RiskFreePoint
from .reverse_dcf import DcfAssumptions, ReverseDcfResult, dcf_price, solve_implied
from .risk_free import RiskFreeCurve, RiskFreeReturn, annual_to_horizon, require_risk_free_alignment
