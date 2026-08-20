from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Iterable


POLICY_SCHEMA = "focused-research-policy-v6"
DOSSIER_SCHEMA = "focused-research-dossier-v6"
RESEARCH_SECTION_ORDER = [
    "investment_thesis",
    "variant_view",
    "earnings_model",
    "earnings_quality",
    "supply_chain_read_through",
    "valuation",
    "catalyst_path",
    "risk_disconfirming_evidence",
    "position_construction",
    "score_summary",
]
EARNINGS_QUALITY_ADJUSTMENT_IDS = (
    "stock_based_compensation",
    "restructuring",
    "acquisition_adjustment",
    "tax_benefit",
    "one_off_gain",
)
SUPPLY_CHAIN_ENTITY_ROLES = (
    "subject_company",
    "customer",
    "supplier",
    "competitor",
    "industry_peer",
)
SUPPLY_CHAIN_RELATIONSHIP_TYPES = (
    "customer_of",
    "supplier_to",
    "competes_with",
    "peer_of",
)
PRIMARY_COMPANY_SOURCE_TYPES = (
    "company_filing",
    "company_guidance",
    "earnings_transcript",
    "regulatory_filing",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _bounded_text(value: Any, *, field: str, maximum: int = 1200) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} has invalid length")
    return normalized


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def load_focused_research_policy(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != POLICY_SCHEMA:
        raise ValueError("unsupported focused research policy")
    for field, minimum, maximum in (
        ("minimum_variant_chains", 1, 10),
        ("minimum_distinct_source_organizations", 2, 20),
        ("maximum_source_age_days", 1, 730),
        ("maximum_consensus_age_days", 1, 180),
        ("maximum_dossier_age_days", 1, 90),
        ("maximum_catalyst_horizon_days", 1, 1095),
        ("minimum_thesis_pillars", 2, 10),
        ("minimum_risks", 1, 20),
        ("minimum_contrary_evidence", 1, 20),
        ("minimum_revenue_drivers", 1, 20),
        ("minimum_sensitivity_cases", 1, 20),
        ("minimum_supply_chain_external_entities", 2, 20),
        ("minimum_supply_chain_external_roles", 2, 4),
        ("minimum_supply_chain_supporting_entities", 2, 20),
        ("minimum_supply_chain_counter_signals", 1, 20),
        ("minimum_supply_chain_relationships", 2, 50),
    ):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"focused research policy {field} is invalid")
    reconciliation_error = payload.get("maximum_implied_price_reconciliation_error")
    if (
        isinstance(reconciliation_error, bool)
        or not isinstance(reconciliation_error, (int, float))
        or not 0 < float(reconciliation_error) <= 0.05
    ):
        raise ValueError(
            "focused research policy maximum_implied_price_reconciliation_error is invalid"
        )
    for field in (
        "required_metric_categories",
        "allowed_units",
        "allowed_driver_units",
        "allowed_source_types",
        "allowed_implied_methods",
        "allowed_valuation_methods",
        "required_earnings_quality_adjustment_ids",
    ):
        value = payload.get(field)
        if (
            not isinstance(value, list)
            or not value
            or len(value) != len(set(value))
            or any(not isinstance(item, str) or not item for item in value)
        ):
            raise ValueError(f"focused research policy {field} is invalid")
    if set(payload["required_earnings_quality_adjustment_ids"]) != set(
        EARNINGS_QUALITY_ADJUSTMENT_IDS
    ):
        raise ValueError(
            "focused research policy earnings quality adjustments are incomplete"
        )
    for field in (
        "require_price_implied_chain",
        "require_consensus_chain",
        "require_falsification",
        "require_bear_base_bull",
        "buy_requires_positive_expected_return",
        "require_driver_based_earnings_model",
        "require_cash_flow_bridge",
        "require_incremental_roic",
        "require_incremental_economics_bridge",
        "require_incremental_roic_hurdle_rate",
        "require_earnings_quality_analysis",
        "require_eps_growth_attribution",
        "require_gaap_non_gaap_reconciliation",
        "require_supply_chain_read_through",
        "require_supply_chain_primary_company_sources",
        "require_supply_chain_subject_company_signal",
        "buy_requires_supply_chain_confirmation",
    ):
        if payload.get(field) is not True:
            raise ValueError(f"focused research policy {field} must remain enabled")
    for field in ("execution_authorized", "promotion_authorized"):
        if payload.get(field) is not False:
            raise ValueError(f"focused research policy {field} must remain disabled")
    for field in (
        "score_may_gate_recommendation",
        "score_may_size_position",
    ):
        if payload.get(field) is not False:
            raise ValueError(f"focused research policy {field} must remain disabled")
    weight_limits = [
        _finite_number(payload.get(field), field=field)
        for field in (
            "maximum_initial_weight_percent",
            "maximum_target_weight_percent",
            "maximum_single_name_weight_percent",
        )
    ]
    if not 0 < weight_limits[0] <= weight_limits[1] <= weight_limits[2] <= 25:
        raise ValueError("focused research position weight limits are invalid")
    event_limit = _finite_number(
        payload.get("maximum_earnings_event_weight_percent"),
        field="maximum_earnings_event_weight_percent",
    )
    if not 0 < event_limit <= weight_limits[2]:
        raise ValueError("focused research earnings event weight limit is invalid")
    reward_to_risk = _finite_number(
        payload.get("minimum_reward_to_risk"), field="minimum_reward_to_risk"
    )
    if not 0 < reward_to_risk <= 10:
        raise ValueError("focused research minimum_reward_to_risk is invalid")
    maximum_shock = _finite_number(
        payload.get("maximum_driver_sensitivity_shock_percent"),
        field="maximum_driver_sensitivity_shock_percent",
    )
    if not 0 < maximum_shock <= 100:
        raise ValueError(
            "focused research maximum_driver_sensitivity_shock_percent is invalid"
        )
    if (
        int(payload["minimum_supply_chain_supporting_entities"])
        > int(payload["minimum_supply_chain_external_entities"])
    ):
        raise ValueError(
            "focused research supply-chain support minimum exceeds coverage minimum"
        )
    return payload


def _source_registry(
    values: Any,
    *,
    as_of: date,
    policy: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list) or not values:
        raise ValueError("sources must be a non-empty list")
    registry: dict[str, dict[str, Any]] = {}
    allowed_types = set(policy["allowed_source_types"])
    for index, raw in enumerate(values):
        if not isinstance(raw, dict):
            raise ValueError("source must be an object")
        source_id = _bounded_text(raw.get("source_id"), field="source_id", maximum=120)
        if source_id in registry:
            raise ValueError("source_id must be unique")
        source_type = raw.get("source_type")
        if source_type not in allowed_types:
            raise ValueError(f"source {source_id} has an unsupported source_type")
        observed_at = date.fromisoformat(str(raw.get("observed_at")))
        if observed_at > as_of:
            raise ValueError(f"source {source_id} is from the future")
        maximum_age = (
            int(policy["maximum_consensus_age_days"])
            if source_type == "consensus_dataset"
            else int(policy["maximum_source_age_days"])
        )
        if (as_of - observed_at).days > maximum_age:
            raise ValueError(f"source {source_id} is stale")
        organization = _bounded_text(
            raw.get("organization"), field="source organization", maximum=160
        )
        locator = _bounded_text(raw.get("locator"), field="source locator", maximum=500)
        registry[source_id] = {
            "source_id": source_id,
            "source_type": source_type,
            "organization": organization,
            "observed_at": observed_at.isoformat(),
            "locator": locator,
        }
    organizations = {item["organization"] for item in registry.values()}
    if len(organizations) < int(policy["minimum_distinct_source_organizations"]):
        raise ValueError("focused research has too few distinct source organizations")
    return registry


def _source_ids(
    values: Any,
    *,
    field: str,
    registry: dict[str, dict[str, Any]],
    required_type: str | None = None,
) -> list[str]:
    if (
        not isinstance(values, list)
        or not values
        or len(values) != len(set(values))
        or any(not isinstance(item, str) for item in values)
    ):
        raise ValueError(f"{field} source_ids are invalid")
    missing = sorted(set(values) - set(registry))
    if missing:
        raise ValueError(f"{field} references unknown sources: {', '.join(missing)}")
    if required_type and not any(
        registry[source_id]["source_type"] == required_type for source_id in values
    ):
        raise ValueError(f"{field} requires a {required_type} source")
    return sorted(values)


def _estimate(
    raw: Any,
    *,
    field: str,
    registry: dict[str, dict[str, Any]],
    required_source_type: str | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must be an object")
    return {
        "value": _finite_number(raw.get("value"), field=f"{field}.value"),
        "source_ids": _source_ids(
            raw.get("source_ids"),
            field=field,
            registry=registry,
            required_type=required_source_type,
        ),
        "methodology": _bounded_text(
            raw.get("methodology"), field=f"{field}.methodology", maximum=800
        ),
    }


def _text_list(
    raw: Any,
    *,
    field: str,
    minimum: int = 1,
    maximum: int = 12,
) -> list[str]:
    if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
        raise ValueError(f"{field} must contain between {minimum} and {maximum} items")
    return [
        _bounded_text(item, field=field, maximum=600)
        for item in raw
    ]


def _percentage(
    value: Any,
    *,
    field: str,
    minimum: float = 0.0,
    maximum: float = 100.0,
    minimum_inclusive: bool = True,
) -> float:
    result = _finite_number(value, field=field)
    lower_ok = result >= minimum if minimum_inclusive else result > minimum
    if not lower_ok or result > maximum:
        raise ValueError(f"{field} is outside the permitted percentage range")
    return result


def _calculate_driver_scenario(model: dict[str, Any]) -> dict[str, float | None]:
    revenue = sum(item["revenue_usd_millions"] for item in model["revenue_drivers"])
    gross_profit = sum(
        item["gross_profit_usd_millions"] for item in model["revenue_drivers"]
    )
    gross_margin = gross_profit / revenue * 100
    opex = model["operating_expenses"]
    variable_opex = revenue * opex["variable_percent_of_revenue"] / 100
    total_opex = opex["fixed_usd_millions"] + variable_opex
    operating_income = gross_profit - total_opex
    operating_margin = operating_income / revenue * 100
    below_line = model["below_the_line"]
    pretax_income = (
        operating_income
        - below_line["net_interest_expense_usd_millions"]
        + below_line["other_non_operating_income_usd_millions"]
    )
    tax_expense = pretax_income * below_line["tax_rate_percent"] / 100
    net_income = pretax_income - tax_expense
    eps = net_income / below_line["diluted_shares_millions"]
    cash = model["cash_flow"]
    operating_cash_flow = (
        net_income
        + cash["depreciation_amortization_usd_millions"]
        + cash["stock_based_compensation_usd_millions"]
        - cash["change_in_net_working_capital_usd_millions"]
        + cash["other_operating_cash_adjustments_usd_millions"]
    )
    capex = (
        cash["maintenance_capex_usd_millions"]
        + cash["growth_capex_usd_millions"]
    )
    free_cash_flow = operating_cash_flow - capex
    capital = model["capital_efficiency"]
    prior_revenue = capital["prior_period_revenue_usd_millions"]
    incremental_revenue = revenue - prior_revenue
    incremental_operating_income = (
        operating_income - capital["prior_period_operating_income_usd_millions"]
    )
    incremental_invested_capital = (
        capital["ending_invested_capital_usd_millions"]
        - capital["prior_period_invested_capital_usd_millions"]
    )
    normalized_after_tax_factor = 1 - below_line["tax_rate_percent"] / 100
    current_nopat = operating_income * normalized_after_tax_factor
    incremental_nopat = incremental_operating_income * normalized_after_tax_factor
    incremental_roic = incremental_nopat / incremental_invested_capital * 100
    hurdle_rate = capital["hurdle_rate_percent"]
    value_creation_spread = incremental_roic - hurdle_rate
    incremental_economic_profit = incremental_nopat - (
        incremental_invested_capital * hurdle_rate / 100
    )

    def ratio(numerator: float, denominator: float) -> float | None:
        return (
            numerator / denominator
            if not math.isclose(denominator, 0.0, abs_tol=1e-12)
            else None
        )

    incremental_operating_margin = ratio(
        incremental_operating_income, incremental_revenue
    )
    incremental_capital_turnover = ratio(
        incremental_revenue, incremental_invested_capital
    )
    growth_capex = cash["growth_capex_usd_millions"]
    growth_capex_revenue_multiple = ratio(incremental_revenue, growth_capex)
    growth_capex_operating_income_multiple = ratio(
        incremental_operating_income, growth_capex
    )
    growth_capex_nopat_multiple = ratio(incremental_nopat, growth_capex)
    growth_capex_payback_years = (
        growth_capex / incremental_nopat if incremental_nopat > 0 else None
    )
    reinvestment_rate = ratio(incremental_invested_capital, current_nopat)
    return {
        "revenue_usd_millions": revenue,
        "gross_profit_usd_millions": gross_profit,
        "gross_margin_percent": gross_margin,
        "variable_operating_expense_usd_millions": variable_opex,
        "operating_expense_usd_millions": total_opex,
        "operating_income_usd_millions": operating_income,
        "operating_margin_percent": operating_margin,
        "pretax_income_usd_millions": pretax_income,
        "tax_expense_usd_millions": tax_expense,
        "net_income_usd_millions": net_income,
        "eps_usd": eps,
        "operating_cash_flow_usd_millions": operating_cash_flow,
        "capex_usd_millions": capex,
        "free_cash_flow_usd_millions": free_cash_flow,
        "incremental_revenue_usd_millions": incremental_revenue,
        "incremental_operating_income_usd_millions": incremental_operating_income,
        "current_nopat_usd_millions": current_nopat,
        "incremental_nopat_usd_millions": incremental_nopat,
        "incremental_invested_capital_usd_millions": incremental_invested_capital,
        "incremental_roic_percent": incremental_roic,
        "hurdle_rate_percent": hurdle_rate,
        "value_creation_spread_basis_points": value_creation_spread * 100,
        "incremental_economic_profit_usd_millions": incremental_economic_profit,
        "incremental_operating_margin_percent": (
            incremental_operating_margin * 100
            if incremental_operating_margin is not None
            else None
        ),
        "incremental_capital_turnover_ratio": incremental_capital_turnover,
        "growth_capex_revenue_multiple": growth_capex_revenue_multiple,
        "growth_capex_operating_income_multiple": (
            growth_capex_operating_income_multiple
        ),
        "growth_capex_nopat_multiple": growth_capex_nopat_multiple,
        "growth_capex_payback_years": growth_capex_payback_years,
        "reinvestment_rate_percent": (
            reinvestment_rate * 100 if reinvestment_rate is not None else None
        ),
    }


def _driver_scenario(
    raw: Any,
    *,
    registry: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("earnings model scenario must be an object")
    name = raw.get("name")
    if name not in {"bear", "base", "bull"}:
        raise ValueError("earnings model has an invalid scenario name")
    probability = _finite_number(raw.get("probability"), field=f"{name}.probability")
    if not 0 < probability < 1:
        raise ValueError(f"{name} probability is invalid")
    raw_drivers = raw.get("revenue_drivers")
    minimum_drivers = int(policy["minimum_revenue_drivers"])
    if not isinstance(raw_drivers, list) or not minimum_drivers <= len(raw_drivers) <= 20:
        raise ValueError(
            f"{name} must contain between {minimum_drivers} and 20 revenue drivers"
        )
    drivers: list[dict[str, Any]] = []
    driver_ids: set[str] = set()
    for item in raw_drivers:
        if not isinstance(item, dict):
            raise ValueError(f"{name} revenue driver must be an object")
        driver_id = _bounded_text(
            item.get("driver_id"), field=f"{name}.driver_id", maximum=80
        )
        if driver_id in driver_ids:
            raise ValueError(f"{name} revenue driver ids must be unique")
        driver_ids.add(driver_id)
        driver_value = _finite_number(
            item.get("driver_value"), field=f"{name}.{driver_id}.driver_value"
        )
        conversion = _finite_number(
            item.get("revenue_conversion_factor"),
            field=f"{name}.{driver_id}.revenue_conversion_factor",
        )
        if driver_value <= 0 or conversion <= 0:
            raise ValueError(f"{name} revenue driver values must be positive")
        share = _percentage(
            item.get("company_share_percent"),
            field=f"{name}.{driver_id}.company_share_percent",
            minimum_inclusive=False,
        )
        timing = _percentage(
            item.get("timing_conversion_percent"),
            field=f"{name}.{driver_id}.timing_conversion_percent",
            minimum_inclusive=False,
        )
        segment_margin = _percentage(
            item.get("gross_margin_percent"),
            field=f"{name}.{driver_id}.gross_margin_percent",
            minimum_inclusive=False,
        )
        driver_unit = _bounded_text(
            item.get("driver_unit"),
            field=f"{name}.{driver_id}.driver_unit",
            maximum=80,
        )
        if driver_unit not in set(policy["allowed_driver_units"]):
            raise ValueError(f"{name}.{driver_id}.driver_unit is unsupported")
        revenue = driver_value * share / 100 * conversion * timing / 100
        drivers.append(
            {
                "driver_id": driver_id,
                "segment_name": _bounded_text(
                    item.get("segment_name"),
                    field=f"{name}.{driver_id}.segment_name",
                    maximum=160,
                ),
                "economic_driver": _bounded_text(
                    item.get("economic_driver"),
                    field=f"{name}.{driver_id}.economic_driver",
                    maximum=240,
                ),
                "driver_value": driver_value,
                "driver_unit": driver_unit,
                "company_share_percent": share,
                "revenue_conversion_factor": conversion,
                "timing_conversion_percent": timing,
                "gross_margin_percent": segment_margin,
                "revenue_usd_millions": revenue,
                "gross_profit_usd_millions": revenue * segment_margin / 100,
                "source_ids": _source_ids(
                    item.get("source_ids"),
                    field=f"{name}.{driver_id}",
                    registry=registry,
                ),
                "methodology": _bounded_text(
                    item.get("methodology"),
                    field=f"{name}.{driver_id}.methodology",
                    maximum=800,
                ),
            }
        )

    def sourced_model(section: str) -> tuple[dict[str, Any], list[str], str]:
        value = raw.get(section)
        if not isinstance(value, dict):
            raise ValueError(f"{name}.{section} must be an object")
        return (
            value,
            _source_ids(
                value.get("source_ids"),
                field=f"{name}.{section}",
                registry=registry,
            ),
            _bounded_text(
                value.get("methodology"), field=f"{name}.{section}.methodology"
            ),
        )

    opex_raw, opex_sources, opex_method = sourced_model("operating_expenses")
    fixed_opex = _finite_number(
        opex_raw.get("fixed_usd_millions"), field=f"{name}.fixed_operating_expense"
    )
    if fixed_opex < 0:
        raise ValueError(f"{name} fixed operating expense cannot be negative")
    operating_expenses = {
        "fixed_usd_millions": fixed_opex,
        "variable_percent_of_revenue": _percentage(
            opex_raw.get("variable_percent_of_revenue"),
            field=f"{name}.variable_operating_expense_percent",
        ),
        "source_ids": opex_sources,
        "methodology": opex_method,
    }

    below_raw, below_sources, below_method = sourced_model("below_the_line")
    diluted_shares = _finite_number(
        below_raw.get("diluted_shares_millions"), field=f"{name}.diluted_shares"
    )
    if diluted_shares <= 0:
        raise ValueError(f"{name} diluted shares must be positive")
    below_the_line = {
        "net_interest_expense_usd_millions": _finite_number(
            below_raw.get("net_interest_expense_usd_millions"),
            field=f"{name}.net_interest_expense",
        ),
        "other_non_operating_income_usd_millions": _finite_number(
            below_raw.get("other_non_operating_income_usd_millions"),
            field=f"{name}.other_non_operating_income",
        ),
        "tax_rate_percent": _percentage(
            below_raw.get("tax_rate_percent"), field=f"{name}.tax_rate"
        ),
        "diluted_shares_millions": diluted_shares,
        "source_ids": below_sources,
        "methodology": below_method,
    }

    cash_raw, cash_sources, cash_method = sourced_model("cash_flow")
    cash_flow: dict[str, Any] = {
        field: _finite_number(cash_raw.get(field), field=f"{name}.{field}")
        for field in (
            "depreciation_amortization_usd_millions",
            "stock_based_compensation_usd_millions",
            "change_in_net_working_capital_usd_millions",
            "other_operating_cash_adjustments_usd_millions",
            "maintenance_capex_usd_millions",
            "growth_capex_usd_millions",
        )
    }
    for field in (
        "depreciation_amortization_usd_millions",
        "stock_based_compensation_usd_millions",
        "maintenance_capex_usd_millions",
        "growth_capex_usd_millions",
    ):
        if cash_flow[field] < 0:
            raise ValueError(f"{name}.{field} cannot be negative")
    cash_flow.update({"source_ids": cash_sources, "methodology": cash_method})

    capital_raw, capital_sources, capital_method = sourced_model("capital_efficiency")
    capital_efficiency = {
        field: _finite_number(capital_raw.get(field), field=f"{name}.{field}")
        for field in (
            "prior_period_revenue_usd_millions",
            "prior_period_operating_income_usd_millions",
            "prior_period_invested_capital_usd_millions",
            "ending_invested_capital_usd_millions",
            "hurdle_rate_percent",
            "acquisition_investment_usd_millions",
            "other_invested_capital_change_usd_millions",
        )
    }
    capital_efficiency["prior_period"] = _bounded_text(
        capital_raw.get("prior_period"),
        field=f"{name}.capital_efficiency.prior_period",
        maximum=40,
    )
    measurement_period_months = capital_raw.get("measurement_period_months")
    if (
        isinstance(measurement_period_months, bool)
        or not isinstance(measurement_period_months, int)
        or not 1 <= measurement_period_months <= 60
    ):
        raise ValueError(f"{name}.measurement_period_months is invalid")
    capital_efficiency["measurement_period_months"] = measurement_period_months
    investment_lag_months = capital_raw.get("investment_lag_months")
    if (
        isinstance(investment_lag_months, bool)
        or not isinstance(investment_lag_months, int)
        or not 0 <= investment_lag_months <= 120
    ):
        raise ValueError(f"{name}.investment_lag_months is invalid")
    capital_efficiency["investment_lag_months"] = investment_lag_months
    if capital_efficiency["prior_period_revenue_usd_millions"] <= 0:
        raise ValueError(f"{name} prior-period revenue must be positive")
    if not 0 < capital_efficiency["hurdle_rate_percent"] <= 50:
        raise ValueError(f"{name} hurdle rate is invalid")
    if capital_efficiency["acquisition_investment_usd_millions"] < 0:
        raise ValueError(f"{name} acquisition investment cannot be negative")
    if capital_efficiency["prior_period_invested_capital_usd_millions"] <= 0 or (
        capital_efficiency["ending_invested_capital_usd_millions"]
        <= capital_efficiency["prior_period_invested_capital_usd_millions"]
    ):
        raise ValueError(f"{name} requires positive incremental invested capital")
    calculated_ending_invested_capital = (
        capital_efficiency["prior_period_invested_capital_usd_millions"]
        + cash_flow["maintenance_capex_usd_millions"]
        + cash_flow["growth_capex_usd_millions"]
        - cash_flow["depreciation_amortization_usd_millions"]
        + cash_flow["change_in_net_working_capital_usd_millions"]
        + capital_efficiency["acquisition_investment_usd_millions"]
        + capital_efficiency["other_invested_capital_change_usd_millions"]
    )
    if not math.isclose(
        capital_efficiency["ending_invested_capital_usd_millions"],
        calculated_ending_invested_capital,
        rel_tol=1e-9,
        abs_tol=1e-8,
    ):
        raise ValueError(f"{name} invested capital bridge does not reconcile")
    capital_efficiency.update(
        {"source_ids": capital_sources, "methodology": capital_method}
    )
    model = {
        "revenue_drivers": sorted(drivers, key=lambda item: item["driver_id"]),
        "operating_expenses": operating_expenses,
        "below_the_line": below_the_line,
        "cash_flow": cash_flow,
        "capital_efficiency": capital_efficiency,
    }
    calculated = _calculate_driver_scenario(model)
    if calculated["revenue_usd_millions"] <= 0 or calculated["eps_usd"] <= 0:
        raise ValueError(f"{name} calculated revenue and EPS must be positive")
    if calculated["incremental_nopat_usd_millions"] <= 0:
        investment_state = "negative_incremental_nopat"
    elif calculated["value_creation_spread_basis_points"] > 0:
        investment_state = "value_creating_above_hurdle"
    else:
        investment_state = "positive_but_below_hurdle"
    invested_capital_bridge = {
        "beginning_invested_capital_usd_millions": capital_efficiency[
            "prior_period_invested_capital_usd_millions"
        ],
        "maintenance_capex_usd_millions": cash_flow[
            "maintenance_capex_usd_millions"
        ],
        "growth_capex_usd_millions": cash_flow["growth_capex_usd_millions"],
        "less_depreciation_amortization_usd_millions": cash_flow[
            "depreciation_amortization_usd_millions"
        ],
        "net_working_capital_investment_usd_millions": cash_flow[
            "change_in_net_working_capital_usd_millions"
        ],
        "acquisition_investment_usd_millions": capital_efficiency[
            "acquisition_investment_usd_millions"
        ],
        "other_invested_capital_change_usd_millions": capital_efficiency[
            "other_invested_capital_change_usd_millions"
        ],
        "calculated_ending_invested_capital_usd_millions": (
            calculated_ending_invested_capital
        ),
        "reported_ending_invested_capital_usd_millions": capital_efficiency[
            "ending_invested_capital_usd_millions"
        ],
        "reconciliation_difference_usd_millions": (
            capital_efficiency["ending_invested_capital_usd_millions"]
            - calculated_ending_invested_capital
        ),
    }
    return {
        "name": name,
        "probability": probability,
        **model,
        "income_statement": {
            key: calculated[key]
            for key in (
                "revenue_usd_millions",
                "gross_profit_usd_millions",
                "gross_margin_percent",
                "variable_operating_expense_usd_millions",
                "operating_expense_usd_millions",
                "operating_income_usd_millions",
                "operating_margin_percent",
                "pretax_income_usd_millions",
                "tax_expense_usd_millions",
                "net_income_usd_millions",
                "eps_usd",
            )
        },
        "cash_flow_bridge": {
            key: calculated[key]
            for key in (
                "operating_cash_flow_usd_millions",
                "capex_usd_millions",
                "free_cash_flow_usd_millions",
            )
        },
        "incremental_returns": {
            key: calculated[key]
            for key in (
                "incremental_revenue_usd_millions",
                "incremental_operating_income_usd_millions",
                "current_nopat_usd_millions",
                "incremental_nopat_usd_millions",
                "incremental_invested_capital_usd_millions",
                "incremental_roic_percent",
                "hurdle_rate_percent",
                "value_creation_spread_basis_points",
                "incremental_economic_profit_usd_millions",
                "incremental_operating_margin_percent",
                "incremental_capital_turnover_ratio",
                "growth_capex_revenue_multiple",
                "growth_capex_operating_income_multiple",
                "growth_capex_nopat_multiple",
                "growth_capex_payback_years",
                "reinvestment_rate_percent",
            )
        },
        "incremental_economics": {
            "state": investment_state,
            "prior_period": capital_efficiency["prior_period"],
            "measurement_period_months": measurement_period_months,
            "investment_lag_months": investment_lag_months,
            "invested_capital_bridge": invested_capital_bridge,
            **{
                key: calculated[key]
                for key in (
                    "incremental_revenue_usd_millions",
                    "incremental_operating_income_usd_millions",
                    "incremental_nopat_usd_millions",
                    "incremental_invested_capital_usd_millions",
                    "incremental_roic_percent",
                    "hurdle_rate_percent",
                    "value_creation_spread_basis_points",
                    "incremental_economic_profit_usd_millions",
                    "incremental_operating_margin_percent",
                    "incremental_capital_turnover_ratio",
                    "growth_capex_revenue_multiple",
                    "growth_capex_operating_income_multiple",
                    "growth_capex_nopat_multiple",
                    "growth_capex_payback_years",
                    "reinvestment_rate_percent",
                )
            },
        },
        **calculated,
        "key_assumptions": _text_list(
            raw.get("key_assumptions"), field=f"{name}.key_assumptions", minimum=2
        ),
        "source_ids": _source_ids(
            raw.get("source_ids"), field=f"{name}.earnings_model", registry=registry
        ),
        "methodology": _bounded_text(
            raw.get("methodology"), field=f"{name}.earnings_model.methodology"
        ),
    }


def _earnings_model(
    raw: Any,
    *,
    registry: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("earnings_model must be an object")
    forecast_period = _bounded_text(
        raw.get("forecast_period"), field="earnings_model.forecast_period", maximum=40
    )
    market_implied_eps = _finite_number(
        raw.get("market_implied_eps_usd"), field="market_implied_eps_usd"
    )
    consensus_eps = _finite_number(
        raw.get("consensus_eps_usd"), field="consensus_eps_usd"
    )
    if market_implied_eps <= 0 or consensus_eps <= 0:
        raise ValueError("earnings model EPS anchors must be positive")
    market_implied_source_ids = _source_ids(
        raw.get("market_implied_eps_source_ids"),
        field="market_implied_eps",
        registry=registry,
        required_type="market_price",
    )
    consensus_source_ids = _source_ids(
        raw.get("consensus_eps_source_ids"),
        field="consensus_eps",
        registry=registry,
        required_type="consensus_dataset",
    )
    raw_scenarios = raw.get("scenarios")
    if not isinstance(raw_scenarios, list) or len(raw_scenarios) != 3:
        raise ValueError("earnings_model must contain bear, base, and bull")
    scenarios: dict[str, dict[str, Any]] = {}
    for item in raw_scenarios:
        scenario = _driver_scenario(item, registry=registry, policy=policy)
        name = str(scenario["name"])
        if name in scenarios:
            raise ValueError("earnings model scenario names must be unique")
        scenarios[name] = scenario
    if set(scenarios) != {"bear", "base", "bull"}:
        raise ValueError("earnings_model must contain bear, base, and bull")
    if not math.isclose(sum(item["probability"] for item in scenarios.values()), 1.0, abs_tol=1e-9):
        raise ValueError("earnings model probabilities must sum to one")
    if not scenarios["bear"]["eps_usd"] < scenarios["base"]["eps_usd"] < scenarios["bull"]["eps_usd"]:
        raise ValueError("earnings EPS must increase from bear to bull")
    driver_sets = [
        {item["driver_id"] for item in scenarios[name]["revenue_drivers"]}
        for name in ("bear", "base", "bull")
    ]
    if not driver_sets[0] == driver_sets[1] == driver_sets[2]:
        raise ValueError("earnings scenarios must use the same revenue driver ids")
    ordered = [scenarios[name] for name in ("bear", "base", "bull")]
    base_eps = scenarios["base"]["eps_usd"]
    sensitivity_raw = raw.get("sensitivity_cases")
    minimum_sensitivities = int(policy["minimum_sensitivity_cases"])
    if (
        not isinstance(sensitivity_raw, list)
        or not minimum_sensitivities <= len(sensitivity_raw) <= 20
    ):
        raise ValueError(
            "earnings_model sensitivity cases do not meet the policy minimum"
        )
    sensitivity_cases: list[dict[str, Any]] = []
    sensitivity_ids: set[str] = set()
    base = scenarios["base"]
    base_drivers = {item["driver_id"]: item for item in base["revenue_drivers"]}
    maximum_shock = float(policy["maximum_driver_sensitivity_shock_percent"])
    for case in sensitivity_raw:
        if not isinstance(case, dict):
            raise ValueError("earnings sensitivity case must be an object")
        sensitivity_id = _bounded_text(
            case.get("sensitivity_id"), field="sensitivity_id", maximum=80
        )
        if sensitivity_id in sensitivity_ids:
            raise ValueError("earnings sensitivity ids must be unique")
        sensitivity_ids.add(sensitivity_id)
        shocks_raw = case.get("driver_shocks")
        if not isinstance(shocks_raw, list) or not shocks_raw:
            raise ValueError(f"sensitivity {sensitivity_id} requires driver shocks")
        shocks: dict[str, float] = {}
        for shock_raw in shocks_raw:
            if not isinstance(shock_raw, dict):
                raise ValueError(f"sensitivity {sensitivity_id} shock must be an object")
            driver_id = _bounded_text(
                shock_raw.get("driver_id"), field="sensitivity.driver_id", maximum=80
            )
            if driver_id not in base_drivers or driver_id in shocks:
                raise ValueError(f"sensitivity {sensitivity_id} has an invalid driver id")
            shock = _finite_number(
                shock_raw.get("shock_percent"), field=f"{sensitivity_id}.shock_percent"
            )
            if shock == 0 or abs(shock) > maximum_shock:
                raise ValueError(f"sensitivity {sensitivity_id} shock is outside policy")
            shocks[driver_id] = shock
        shocked_drivers = []
        for driver in base["revenue_drivers"]:
            shocked = dict(driver)
            shock = shocks.get(driver["driver_id"], 0.0)
            shocked["driver_value"] = driver["driver_value"] * (1 + shock / 100)
            shocked["revenue_usd_millions"] = (
                shocked["driver_value"]
                * shocked["company_share_percent"]
                / 100
                * shocked["revenue_conversion_factor"]
                * shocked["timing_conversion_percent"]
                / 100
            )
            shocked["gross_profit_usd_millions"] = (
                shocked["revenue_usd_millions"]
                * shocked["gross_margin_percent"]
                / 100
            )
            shocked_drivers.append(shocked)
        shocked_model = {
            "revenue_drivers": shocked_drivers,
            "operating_expenses": base["operating_expenses"],
            "below_the_line": base["below_the_line"],
            "cash_flow": base["cash_flow"],
            "capital_efficiency": base["capital_efficiency"],
        }
        shocked = _calculate_driver_scenario(shocked_model)

        def percent_change(field: str) -> float | None:
            denominator = float(base[field])
            return (
                (float(shocked[field]) / denominator - 1) * 100
                if not math.isclose(denominator, 0.0, abs_tol=1e-12)
                else None
            )

        sensitivity_cases.append(
            {
                "sensitivity_id": sensitivity_id,
                "label": _bounded_text(case.get("label"), field="sensitivity.label"),
                "base_scenario": "base",
                "driver_shocks": [
                    {
                        "driver_id": driver_id,
                        "shock_percent": shocks[driver_id],
                        "base_driver_value": base_drivers[driver_id]["driver_value"],
                        "shocked_driver_value": base_drivers[driver_id]["driver_value"]
                        * (1 + shocks[driver_id] / 100),
                    }
                    for driver_id in sorted(shocks)
                ],
                "results": {
                    "revenue_change_percent": percent_change("revenue_usd_millions"),
                    "gross_margin_change_basis_points": (
                        shocked["gross_margin_percent"] - base["gross_margin_percent"]
                    )
                    * 100,
                    "operating_income_change_percent": percent_change(
                        "operating_income_usd_millions"
                    ),
                    "operating_margin_change_basis_points": (
                        shocked["operating_margin_percent"]
                        - base["operating_margin_percent"]
                    )
                    * 100,
                    "eps_change_percent": percent_change("eps_usd"),
                    "operating_cash_flow_change_percent": percent_change(
                        "operating_cash_flow_usd_millions"
                    ),
                    "free_cash_flow_change_percent": percent_change(
                        "free_cash_flow_usd_millions"
                    ),
                    "incremental_roic_change_basis_points": (
                        shocked["incremental_roic_percent"]
                        - base["incremental_roic_percent"]
                    )
                    * 100,
                    "base_eps_usd": base["eps_usd"],
                    "shocked_eps_usd": shocked["eps_usd"],
                    "base_free_cash_flow_usd_millions": base[
                        "free_cash_flow_usd_millions"
                    ],
                    "shocked_free_cash_flow_usd_millions": shocked[
                        "free_cash_flow_usd_millions"
                    ],
                },
                "source_ids": _source_ids(
                    case.get("source_ids"),
                    field=f"sensitivity {sensitivity_id}",
                    registry=registry,
                ),
                "rationale": _bounded_text(
                    case.get("rationale"), field=f"sensitivity {sensitivity_id}.rationale"
                ),
            }
        )
    return {
        "forecast_period": forecast_period,
        "market_implied_eps_usd": market_implied_eps,
        "market_implied_eps_source_ids": market_implied_source_ids,
        "market_implied_eps_methodology": _bounded_text(
            raw.get("market_implied_eps_methodology"),
            field="market_implied_eps_methodology",
        ),
        "consensus_eps_usd": consensus_eps,
        "consensus_eps_source_ids": consensus_source_ids,
        "consensus_eps_methodology": _bounded_text(
            raw.get("consensus_eps_methodology"), field="consensus_eps_methodology"
        ),
        "scenarios": ordered,
        "sensitivity_cases": sorted(
            sensitivity_cases, key=lambda item: item["sensitivity_id"]
        ),
        "model_equations": {
            "segment_revenue": "driver value × company share × revenue conversion factor × timing conversion",
            "operating_income": "segment gross profit − fixed operating expense − variable operating expense",
            "eps": "(operating income − net interest expense + other non-operating income − tax) ÷ diluted shares",
            "operating_cash_flow": "net income + D&A + stock compensation − change in net working capital + other operating cash adjustments",
            "free_cash_flow": "operating cash flow − maintenance CAPEX − growth CAPEX",
            "invested_capital_bridge": "beginning invested capital + maintenance CAPEX + growth CAPEX − D&A + net working capital investment + acquisitions + other invested-capital change = ending invested capital",
            "incremental_roic": "(current operating income − prior operating income) × (1 − normalized tax rate) ÷ (ending invested capital − beginning invested capital)",
            "incremental_economic_profit": "incremental NOPAT − incremental invested capital × hurdle rate",
            "growth_capex_productivity": "incremental revenue or operating profit or NOPAT ÷ growth CAPEX",
        },
        "base_vs_consensus_percent": (base_eps / consensus_eps - 1) * 100,
        "base_vs_market_implied_percent": (base_eps / market_implied_eps - 1) * 100,
        "probability_weighted_eps_usd": sum(
            item["probability"] * item["eps_usd"] for item in ordered
        ),
    }


def _growth_percent(current: float, prior: float) -> float | None:
    if math.isclose(prior, 0.0, abs_tol=1e-12):
        return None
    return (current / prior - 1) * 100


def _earnings_quality(
    raw: Any,
    *,
    registry: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Build a sourced, fully reconciled earnings-quality analysis."""

    if not isinstance(raw, dict):
        raise ValueError("earnings_quality must be an object")
    periods_raw = raw.get("periods")
    if not isinstance(periods_raw, dict):
        raise ValueError("earnings_quality.periods must be an object")
    periods = {
        "prior_period": _bounded_text(
            periods_raw.get("prior_period"),
            field="earnings_quality.prior_period",
            maximum=40,
        ),
        "current_period": _bounded_text(
            periods_raw.get("current_period"),
            field="earnings_quality.current_period",
            maximum=40,
        ),
    }
    if periods["prior_period"] == periods["current_period"]:
        raise ValueError("earnings quality periods must be distinct")

    def sourced_group(name: str) -> tuple[dict[str, Any], list[str], str]:
        value = raw.get(name)
        if not isinstance(value, dict):
            raise ValueError(f"earnings_quality.{name} must be an object")
        return (
            value,
            _source_ids(
                value.get("source_ids"),
                field=f"earnings_quality.{name}",
                registry=registry,
            ),
            _bounded_text(
                value.get("methodology"),
                field=f"earnings_quality.{name}.methodology",
                maximum=1000,
            ),
        )

    def period_values(
        group_name: str,
        value: dict[str, Any],
        fields: tuple[str, ...],
        *,
        nonnegative: set[str] | None = None,
        positive: set[str] | None = None,
    ) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        for period_key in ("prior", "current"):
            period_raw = value.get(period_key)
            if not isinstance(period_raw, dict):
                raise ValueError(
                    f"earnings_quality.{group_name}.{period_key} must be an object"
                )
            parsed = {
                field: _finite_number(
                    period_raw.get(field),
                    field=f"earnings_quality.{group_name}.{period_key}.{field}",
                )
                for field in fields
            }
            for field in nonnegative or set():
                if parsed[field] < 0:
                    raise ValueError(
                        f"earnings_quality.{group_name}.{period_key}.{field} cannot be negative"
                    )
            for field in positive or set():
                if parsed[field] <= 0:
                    raise ValueError(
                        f"earnings_quality.{group_name}.{period_key}.{field} must be positive"
                    )
            result[period_key] = parsed
        return result

    balance_raw, balance_sources, balance_method = sourced_group("balance_sheet")
    balance_fields = (
        "revenue_usd_millions",
        "accounts_receivable_usd_millions",
        "inventory_usd_millions",
        "contract_liabilities_usd_millions",
        "deferred_revenue_usd_millions",
    )
    balance = period_values(
        "balance_sheet",
        balance_raw,
        balance_fields,
        nonnegative=set(balance_fields),
        positive={"revenue_usd_millions"},
    )
    balance.update({"source_ids": balance_sources, "methodology": balance_method})

    cash_raw, cash_sources, cash_method = sourced_group("cash_conversion")
    cash = period_values(
        "cash_conversion",
        cash_raw,
        (
            "operating_cash_flow_usd_millions",
            "total_assets_usd_millions",
            "working_capital_contribution_usd_millions",
        ),
        positive={"total_assets_usd_millions"},
    )
    cash.update({"source_ids": cash_sources, "methodology": cash_method})

    capital_raw, capital_sources, capital_method = sourced_group("capital_investment")
    capital = period_values(
        "capital_investment",
        capital_raw,
        (
            "depreciation_amortization_usd_millions",
            "capex_usd_millions",
        ),
        nonnegative={
            "depreciation_amortization_usd_millions",
            "capex_usd_millions",
        },
        positive={"capex_usd_millions"},
    )
    capital.update({"source_ids": capital_sources, "methodology": capital_method})

    bridge_raw, bridge_sources, bridge_method = sourced_group("earnings_bridge")
    bridge_fields = (
        "core_operating_income_usd_millions",
        "reported_gaap_operating_income_usd_millions",
        "recurring_non_operating_income_usd_millions",
        "normalized_tax_rate_percent",
        "reported_gaap_pretax_income_usd_millions",
        "reported_gaap_net_income_usd_millions",
        "reported_gaap_eps_usd",
        "reported_non_gaap_net_income_usd_millions",
        "reported_non_gaap_eps_usd",
        "diluted_shares_millions",
        "buyback_attributable_weighted_average_share_reduction_millions",
    )
    bridge = period_values(
        "earnings_bridge",
        bridge_raw,
        bridge_fields,
        nonnegative={"buyback_attributable_weighted_average_share_reduction_millions"},
        positive={"diluted_shares_millions"},
    )
    for period_key in ("prior", "current"):
        bridge[period_key]["normalized_tax_rate_percent"] = _percentage(
            bridge[period_key]["normalized_tax_rate_percent"],
            field=f"earnings_quality.earnings_bridge.{period_key}.normalized_tax_rate_percent",
        )
    bridge.update({"source_ids": bridge_sources, "methodology": bridge_method})

    required_adjustments = set(policy["required_earnings_quality_adjustment_ids"])
    adjustments_raw = raw.get("adjustments")
    if not isinstance(adjustments_raw, list) or len(adjustments_raw) != len(
        required_adjustments
    ):
        raise ValueError("earnings quality adjustments are incomplete")
    adjustments: list[dict[str, Any]] = []
    adjustment_ids: set[str] = set()
    for item in adjustments_raw:
        if not isinstance(item, dict):
            raise ValueError("earnings quality adjustment must be an object")
        adjustment_id = _bounded_text(
            item.get("adjustment_id"),
            field="earnings_quality.adjustment_id",
            maximum=80,
        )
        if adjustment_id in adjustment_ids or adjustment_id not in required_adjustments:
            raise ValueError("earnings quality adjustment id is invalid")
        adjustment_ids.add(adjustment_id)
        location = item.get("income_statement_location")
        if location not in {"operating", "non_operating", "tax"}:
            raise ValueError(
                f"earnings quality adjustment {adjustment_id} location is invalid"
            )
        parsed = {
            field: _finite_number(
                item.get(field), field=f"earnings_quality.{adjustment_id}.{field}"
            )
            for field in (
                "prior_pretax_impact_usd_millions",
                "current_pretax_impact_usd_millions",
                "prior_after_tax_impact_usd_millions",
                "current_after_tax_impact_usd_millions",
            )
        }
        if location == "tax" and (
            not math.isclose(
                parsed["prior_pretax_impact_usd_millions"], 0.0, abs_tol=1e-12
            )
            or not math.isclose(
                parsed["current_pretax_impact_usd_millions"], 0.0, abs_tol=1e-12
            )
        ):
            raise ValueError("tax adjustments cannot have a pretax impact")
        if adjustment_id == "stock_based_compensation" and any(
            parsed[field] > 0
            for field in (
                "prior_pretax_impact_usd_millions",
                "current_pretax_impact_usd_millions",
                "prior_after_tax_impact_usd_millions",
                "current_after_tax_impact_usd_millions",
            )
        ):
            raise ValueError("stock based compensation must be represented as an expense")
        if adjustment_id == "tax_benefit" and any(
            parsed[field] < 0
            for field in (
                "prior_after_tax_impact_usd_millions",
                "current_after_tax_impact_usd_millions",
            )
        ):
            raise ValueError("tax benefit must be represented as a positive benefit")
        if adjustment_id == "one_off_gain" and any(
            parsed[field] < 0
            for field in parsed
        ):
            raise ValueError("one-off gain must be represented as a positive gain")
        adjustments.append(
            {
                "adjustment_id": adjustment_id,
                "label": _bounded_text(
                    item.get("label"), field=f"earnings_quality.{adjustment_id}.label"
                ),
                "income_statement_location": location,
                **parsed,
                "source_ids": _source_ids(
                    item.get("source_ids"),
                    field=f"earnings_quality.{adjustment_id}",
                    registry=registry,
                ),
                "methodology": _bounded_text(
                    item.get("methodology"),
                    field=f"earnings_quality.{adjustment_id}.methodology",
                    maximum=800,
                ),
            }
        )
    if adjustment_ids != required_adjustments:
        raise ValueError("earnings quality adjustments are incomplete")
    adjustments.sort(key=lambda item: item["adjustment_id"])

    def require_close(actual: float, expected: float, field: str) -> None:
        if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError(f"earnings quality {field} does not reconcile")

    adjustment_by_id = {item["adjustment_id"]: item for item in adjustments}
    reconciled_periods: dict[str, dict[str, float]] = {}
    for period_key in ("prior", "current"):
        values = bridge[period_key]
        prefix = "prior" if period_key == "prior" else "current"
        core_pretax = (
            values["core_operating_income_usd_millions"]
            + values["recurring_non_operating_income_usd_millions"]
        )
        core_net_income = core_pretax * (
            1 - values["normalized_tax_rate_percent"] / 100
        )
        operating_adjustments = sum(
            item[f"{prefix}_pretax_impact_usd_millions"]
            for item in adjustments
            if item["income_statement_location"] == "operating"
        )
        pretax_adjustments = sum(
            item[f"{prefix}_pretax_impact_usd_millions"]
            for item in adjustments
            if item["income_statement_location"] in {"operating", "non_operating"}
        )
        after_tax_adjustments = sum(
            item[f"{prefix}_after_tax_impact_usd_millions"]
            for item in adjustments
        )
        expected_gaap_operating_income = (
            values["core_operating_income_usd_millions"] + operating_adjustments
        )
        expected_gaap_pretax_income = core_pretax + pretax_adjustments
        expected_gaap_net_income = core_net_income + after_tax_adjustments
        expected_gaap_eps = (
            expected_gaap_net_income / values["diluted_shares_millions"]
        )
        expected_non_gaap_eps = core_net_income / values["diluted_shares_millions"]
        require_close(
            values["reported_gaap_operating_income_usd_millions"],
            expected_gaap_operating_income,
            f"{period_key}.reported_gaap_operating_income",
        )
        require_close(
            values["reported_gaap_pretax_income_usd_millions"],
            expected_gaap_pretax_income,
            f"{period_key}.reported_gaap_pretax_income",
        )
        require_close(
            values["reported_gaap_net_income_usd_millions"],
            expected_gaap_net_income,
            f"{period_key}.reported_gaap_net_income",
        )
        require_close(
            values["reported_gaap_eps_usd"],
            expected_gaap_eps,
            f"{period_key}.reported_gaap_eps",
        )
        require_close(
            values["reported_non_gaap_net_income_usd_millions"],
            core_net_income,
            f"{period_key}.reported_non_gaap_net_income",
        )
        require_close(
            values["reported_non_gaap_eps_usd"],
            expected_non_gaap_eps,
            f"{period_key}.reported_non_gaap_eps",
        )
        reconciled_periods[period_key] = {
            "core_pretax_income_usd_millions": core_pretax,
            "core_net_income_usd_millions": core_net_income,
            "operating_adjustments_usd_millions": operating_adjustments,
            "pretax_adjustments_usd_millions": pretax_adjustments,
            "after_tax_adjustments_usd_millions": after_tax_adjustments,
            "gaap_operating_income_usd_millions": expected_gaap_operating_income,
            "gaap_pretax_income_usd_millions": expected_gaap_pretax_income,
            "gaap_net_income_usd_millions": expected_gaap_net_income,
            "gaap_eps_usd": expected_gaap_eps,
            "non_gaap_net_income_usd_millions": core_net_income,
            "non_gaap_eps_usd": expected_non_gaap_eps,
        }

    prior_balance = balance["prior"]
    current_balance = balance["current"]
    revenue_growth = _growth_percent(
        current_balance["revenue_usd_millions"],
        prior_balance["revenue_usd_millions"],
    )

    def balance_growth(field: str) -> dict[str, float | None]:
        growth = _growth_percent(current_balance[field], prior_balance[field])
        return {
            "growth_percent": growth,
            "minus_revenue_growth_percentage_points": (
                growth - revenue_growth
                if growth is not None and revenue_growth is not None
                else None
            ),
            "absolute_change_usd_millions": (
                current_balance[field] - prior_balance[field]
            ),
        }

    current_cash = cash["current"]
    current_gaap_net_income = reconciled_periods["current"][
        "gaap_net_income_usd_millions"
    ]
    average_assets = (
        cash["prior"]["total_assets_usd_millions"]
        + current_cash["total_assets_usd_millions"]
    ) / 2
    accruals = current_gaap_net_income - current_cash[
        "operating_cash_flow_usd_millions"
    ]

    current_capital = capital["current"]
    prior_bridge = bridge["prior"]
    current_bridge = bridge["current"]
    prior_eps = reconciled_periods["prior"]["gaap_eps_usd"]
    current_eps = reconciled_periods["current"]["gaap_eps_usd"]
    eps_growth = _growth_percent(current_eps, prior_eps)
    attribution_components: list[dict[str, Any]] = []
    if eps_growth is not None:
        denominator = prior_bridge["diluted_shares_millions"] * prior_eps

        def contribution(amount_usd_millions: float) -> float:
            return amount_usd_millions / denominator * 100

        prior_tax = prior_bridge["normalized_tax_rate_percent"] / 100
        current_tax = current_bridge["normalized_tax_rate_percent"] / 100
        current_core_pretax = reconciled_periods["current"][
            "core_pretax_income_usd_millions"
        ]
        attribution_components.extend(
            [
                {
                    "driver_id": "core_operating_income_growth",
                    "label": "Core operating income growth",
                    "contribution_percentage_points": contribution(
                        (
                            current_bridge["core_operating_income_usd_millions"]
                            - prior_bridge["core_operating_income_usd_millions"]
                        )
                        * (1 - prior_tax)
                    ),
                },
                {
                    "driver_id": "recurring_non_operating_change",
                    "label": "Recurring non-operating change",
                    "contribution_percentage_points": contribution(
                        (
                            current_bridge[
                                "recurring_non_operating_income_usd_millions"
                            ]
                            - prior_bridge[
                                "recurring_non_operating_income_usd_millions"
                            ]
                        )
                        * (1 - prior_tax)
                    ),
                },
                {
                    "driver_id": "normalized_tax_rate_change",
                    "label": "Normalized tax-rate change",
                    "contribution_percentage_points": contribution(
                        current_core_pretax * (prior_tax - current_tax)
                    ),
                },
            ]
        )
        for adjustment in adjustments:
            attribution_components.append(
                {
                    "driver_id": adjustment["adjustment_id"],
                    "label": adjustment["label"],
                    "contribution_percentage_points": contribution(
                        adjustment["current_after_tax_impact_usd_millions"]
                        - adjustment["prior_after_tax_impact_usd_millions"]
                    ),
                }
            )
        total_share_count_effect = (
            current_gaap_net_income
            * (
                1 / current_bridge["diluted_shares_millions"]
                - 1 / prior_bridge["diluted_shares_millions"]
            )
            / prior_eps
            * 100
        )
        buyback_reduction = current_bridge[
            "buyback_attributable_weighted_average_share_reduction_millions"
        ]
        shares_without_buyback = (
            current_bridge["diluted_shares_millions"] + buyback_reduction
        )
        buyback_effect = (
            current_gaap_net_income
            * (
                1 / current_bridge["diluted_shares_millions"]
                - 1 / shares_without_buyback
            )
            / prior_eps
            * 100
        )
        attribution_components.extend(
            [
                {
                    "driver_id": "share_repurchase",
                    "label": "Share-repurchase EPS effect",
                    "contribution_percentage_points": buyback_effect,
                },
                {
                    "driver_id": "other_share_count_change",
                    "label": "Other net share-count change",
                    "contribution_percentage_points": (
                        total_share_count_effect - buyback_effect
                    ),
                },
            ]
        )
        require_close(
            sum(item["contribution_percentage_points"] for item in attribution_components),
            eps_growth,
            "EPS growth attribution",
        )
        attribution_state = "reconciled"
    else:
        attribution_state = "not_meaningful_prior_eps_zero"

    adjustment_metrics = []
    for adjustment in adjustments:
        prior_pretax = abs(adjustment["prior_pretax_impact_usd_millions"])
        current_pretax = abs(adjustment["current_pretax_impact_usd_millions"])
        adjustment_metrics.append(
            {
                **adjustment,
                "absolute_pretax_amount_growth_percent": _growth_percent(
                    current_pretax, prior_pretax
                ),
                "current_absolute_pretax_percent_of_revenue": (
                    current_pretax / current_balance["revenue_usd_millions"] * 100
                ),
            }
        )
    stock_comp = adjustment_by_id["stock_based_compensation"]
    gaap_non_gaap_gap = (
        _growth_percent(
            reconciled_periods["current"]["non_gaap_eps_usd"],
            reconciled_periods["current"]["gaap_eps_usd"],
        )
    )
    buyback_component = next(
        (
            item["contribution_percentage_points"]
            for item in attribution_components
            if item["driver_id"] == "share_repurchase"
        ),
        None,
    )
    buyback_share_of_growth = (
        buyback_component / eps_growth * 100
        if buyback_component is not None
        and eps_growth is not None
        and not math.isclose(eps_growth, 0.0, abs_tol=1e-12)
        else None
    )

    def comparison(value: float | None, reference: float = 0.0) -> str:
        if value is None:
            return "not_meaningful"
        if math.isclose(value, reference, abs_tol=1e-12):
            return "in_line"
        return "above" if value > reference else "below"

    receivables = balance_growth("accounts_receivable_usd_millions")
    inventory = balance_growth("inventory_usd_millions")
    contract_liabilities = balance_growth("contract_liabilities_usd_millions")
    deferred_revenue = balance_growth("deferred_revenue_usd_millions")
    cash_conversion_percent = (
        current_cash["operating_cash_flow_usd_millions"]
        / current_gaap_net_income
        * 100
        if not math.isclose(current_gaap_net_income, 0.0, abs_tol=1e-12)
        else None
    )
    working_capital_percent_of_ocf = (
        current_cash["working_capital_contribution_usd_millions"]
        / current_cash["operating_cash_flow_usd_millions"]
        * 100
        if not math.isclose(
            current_cash["operating_cash_flow_usd_millions"], 0.0, abs_tol=1e-12
        )
        else None
    )
    return {
        **periods,
        "balance_sheet_inputs": balance,
        "cash_conversion_inputs": cash,
        "capital_investment_inputs": capital,
        "earnings_bridge_inputs": bridge,
        "adjustments": adjustment_metrics,
        "balance_sheet_growth": {
            "revenue_growth_percent": revenue_growth,
            "accounts_receivable": receivables,
            "inventory": inventory,
            "contract_liabilities": contract_liabilities,
            "deferred_revenue": deferred_revenue,
        },
        "accruals_and_cash_conversion": {
            "accruals_usd_millions": accruals,
            "accrual_ratio_percent_of_average_assets": accruals / average_assets * 100,
            "cash_conversion_percent": cash_conversion_percent,
            "working_capital_contribution_usd_millions": current_cash[
                "working_capital_contribution_usd_millions"
            ],
            "working_capital_contribution_percent_of_ocf": (
                working_capital_percent_of_ocf
            ),
        },
        "stock_based_compensation": {
            "prior_expense_usd_millions": abs(
                stock_comp["prior_pretax_impact_usd_millions"]
            ),
            "current_expense_usd_millions": abs(
                stock_comp["current_pretax_impact_usd_millions"]
            ),
            "growth_percent": _growth_percent(
                abs(stock_comp["current_pretax_impact_usd_millions"]),
                abs(stock_comp["prior_pretax_impact_usd_millions"]),
            ),
            "percent_of_revenue": abs(
                stock_comp["current_pretax_impact_usd_millions"]
            )
            / current_balance["revenue_usd_millions"]
            * 100,
        },
        "gaap_to_non_gaap_reconciliation": {
            "periods": reconciled_periods,
            "current_non_gaap_eps_premium_percent": gaap_non_gaap_gap,
            "equation": "GAAP net income − signed after-tax adjustments = non-GAAP core net income",
        },
        "capital_intensity": {
            "prior_depreciation_to_capex_percent": capital["prior"][
                "depreciation_amortization_usd_millions"
            ]
            / capital["prior"]["capex_usd_millions"]
            * 100,
            "current_depreciation_to_capex_percent": current_capital[
                "depreciation_amortization_usd_millions"
            ]
            / current_capital["capex_usd_millions"]
            * 100,
            "depreciation_growth_percent": _growth_percent(
                current_capital["depreciation_amortization_usd_millions"],
                capital["prior"]["depreciation_amortization_usd_millions"],
            ),
            "capex_growth_percent": _growth_percent(
                current_capital["capex_usd_millions"],
                capital["prior"]["capex_usd_millions"],
            ),
        },
        "eps_growth_attribution": {
            "state": attribution_state,
            "reported_eps_growth_percent": eps_growth,
            "components": attribution_components,
            "reconciled_total_percentage_points": (
                sum(
                    item["contribution_percentage_points"]
                    for item in attribution_components
                )
                if attribution_components
                else None
            ),
            "share_repurchase_contribution_percentage_points": buyback_component,
            "share_repurchase_share_of_eps_growth_percent": buyback_share_of_growth,
            "equation": "core operating income + recurring non-operating + normalized tax + signed adjustments + share repurchase + other share-count change",
        },
        "directional_diagnostics": {
            "receivables_growth_vs_revenue": comparison(
                receivables["minus_revenue_growth_percentage_points"]
            ),
            "inventory_growth_vs_revenue": comparison(
                inventory["minus_revenue_growth_percentage_points"]
            ),
            "contract_liabilities_growth_vs_revenue": comparison(
                contract_liabilities["minus_revenue_growth_percentage_points"]
            ),
            "deferred_revenue_growth_vs_revenue": comparison(
                deferred_revenue["minus_revenue_growth_percentage_points"]
            ),
            "cash_conversion_vs_net_income": comparison(
                cash_conversion_percent, 100.0
            ),
            "working_capital_cash_contribution": comparison(
                current_cash["working_capital_contribution_usd_millions"]
            ),
            "accruals": comparison(accruals),
        },
        "methodology": _bounded_text(
            raw.get("methodology"), field="earnings_quality.methodology", maximum=1200
        ),
        "source_ids": _source_ids(
            raw.get("source_ids"), field="earnings_quality", registry=registry
        ),
    }


def _supply_chain_read_through(
    raw: Any,
    *,
    symbol: str,
    registry: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("supply_chain_read_through must be an object")
    hypothesis_raw = raw.get("hypothesis")
    if not isinstance(hypothesis_raw, dict):
        raise ValueError("supply-chain hypothesis must be an object")
    expected_direction = hypothesis_raw.get("expected_direction")
    if expected_direction not in {"increase", "decrease"}:
        raise ValueError("supply-chain hypothesis direction is invalid")
    hypothesis = {
        "hypothesis_id": _bounded_text(
            hypothesis_raw.get("hypothesis_id"),
            field="supply_chain.hypothesis_id",
            maximum=80,
        ),
        "statement": _bounded_text(
            hypothesis_raw.get("statement"),
            field="supply_chain.hypothesis.statement",
        ),
        "subject_metric": _bounded_text(
            hypothesis_raw.get("subject_metric"),
            field="supply_chain.hypothesis.subject_metric",
            maximum=160,
        ),
        "forecast_period": _bounded_text(
            hypothesis_raw.get("forecast_period"),
            field="supply_chain.hypothesis.forecast_period",
            maximum=80,
        ),
        "expected_direction": expected_direction,
        "falsification_criteria": _text_list(
            hypothesis_raw.get("falsification_criteria"),
            field="supply_chain.hypothesis.falsification_criteria",
            minimum=1,
            maximum=8,
        ),
    }

    entities_raw = raw.get("entities")
    minimum_external = int(policy["minimum_supply_chain_external_entities"])
    if (
        not isinstance(entities_raw, list)
        or not minimum_external + 1 <= len(entities_raw) <= 30
    ):
        raise ValueError("supply-chain entity coverage is incomplete")
    known_organizations = {item["organization"] for item in registry.values()}
    entities: dict[str, dict[str, Any]] = {}
    for item in entities_raw:
        if not isinstance(item, dict):
            raise ValueError("supply-chain entity must be an object")
        entity_id = _bounded_text(
            item.get("entity_id"), field="supply_chain.entity_id", maximum=80
        )
        if entity_id in entities:
            raise ValueError("supply-chain entity ids must be unique")
        role = item.get("role")
        if role not in SUPPLY_CHAIN_ENTITY_ROLES:
            raise ValueError(f"supply-chain entity {entity_id} has an invalid role")
        source_organization = _bounded_text(
            item.get("source_organization"),
            field=f"supply_chain.{entity_id}.source_organization",
            maximum=160,
        )
        if source_organization not in known_organizations:
            raise ValueError(
                f"supply-chain entity {entity_id} has no registered source organization"
            )
        entities[entity_id] = {
            "entity_id": entity_id,
            "name": _bounded_text(
                item.get("name"), field=f"supply_chain.{entity_id}.name", maximum=160
            ),
            "identifier": _bounded_text(
                item.get("identifier"),
                field=f"supply_chain.{entity_id}.identifier",
                maximum=40,
            ).upper(),
            "role": role,
            "source_organization": source_organization,
        }
    subject_entities = [
        item for item in entities.values() if item["role"] == "subject_company"
    ]
    if (
        len(subject_entities) != 1
        or subject_entities[0]["identifier"] != symbol
    ):
        raise ValueError("supply-chain graph requires exactly one matching subject company")
    subject_id = subject_entities[0]["entity_id"]
    external_ids = set(entities) - {subject_id}
    if len(external_ids) < minimum_external:
        raise ValueError("supply-chain graph has too few external entities")
    external_roles = {entities[item]["role"] for item in external_ids}
    if len(external_roles) < int(policy["minimum_supply_chain_external_roles"]):
        raise ValueError("supply-chain graph has too little role coverage")

    relationships_raw = raw.get("relationships")
    minimum_relationships = int(policy["minimum_supply_chain_relationships"])
    if (
        not isinstance(relationships_raw, list)
        or not minimum_relationships <= len(relationships_raw) <= 60
    ):
        raise ValueError("supply-chain relationships are incomplete")
    relationships: dict[str, dict[str, Any]] = {}
    adjacency = {entity_id: set() for entity_id in entities}
    for item in relationships_raw:
        if not isinstance(item, dict):
            raise ValueError("supply-chain relationship must be an object")
        relationship_id = _bounded_text(
            item.get("relationship_id"),
            field="supply_chain.relationship_id",
            maximum=80,
        )
        if relationship_id in relationships:
            raise ValueError("supply-chain relationship ids must be unique")
        from_entity = item.get("from_entity_id")
        to_entity = item.get("to_entity_id")
        if (
            from_entity not in entities
            or to_entity not in entities
            or from_entity == to_entity
        ):
            raise ValueError(f"supply-chain relationship {relationship_id} is invalid")
        relationship_type = item.get("relationship_type")
        if relationship_type not in SUPPLY_CHAIN_RELATIONSHIP_TYPES:
            raise ValueError(
                f"supply-chain relationship {relationship_id} has an invalid type"
            )
        required_from_role = {
            "customer_of": "customer",
            "supplier_to": "supplier",
            "competes_with": "competitor",
            "peer_of": "industry_peer",
        }[relationship_type]
        if (
            entities[from_entity]["role"] != required_from_role
            or entities[to_entity]["role"] != "subject_company"
        ):
            raise ValueError(
                f"supply-chain relationship {relationship_id} conflicts with entity roles"
            )
        expected_lag_months = item.get("expected_lag_months")
        if (
            isinstance(expected_lag_months, bool)
            or not isinstance(expected_lag_months, int)
            or not 0 <= expected_lag_months <= 60
        ):
            raise ValueError(
                f"supply-chain relationship {relationship_id} has an invalid lag"
            )
        relationships[relationship_id] = {
            "relationship_id": relationship_id,
            "from_entity_id": from_entity,
            "to_entity_id": to_entity,
            "relationship_type": relationship_type,
            "upstream_metric": _bounded_text(
                item.get("upstream_metric"),
                field=f"supply_chain.{relationship_id}.upstream_metric",
                maximum=160,
            ),
            "subject_metric": _bounded_text(
                item.get("subject_metric"),
                field=f"supply_chain.{relationship_id}.subject_metric",
                maximum=160,
            ),
            "expected_lag_months": expected_lag_months,
            "mechanism": _bounded_text(
                item.get("mechanism"),
                field=f"supply_chain.{relationship_id}.mechanism",
                maximum=800,
            ),
            "source_ids": _source_ids(
                item.get("source_ids"),
                field=f"supply_chain relationship {relationship_id}",
                registry=registry,
            ),
        }
        adjacency[from_entity].add(to_entity)
        adjacency[to_entity].add(from_entity)
    reachable = {subject_id}
    frontier = [subject_id]
    while frontier:
        current = frontier.pop()
        for connected in adjacency[current] - reachable:
            reachable.add(connected)
            frontier.append(connected)
    if reachable != set(entities):
        raise ValueError("supply-chain graph contains entities disconnected from subject")

    signals_raw = raw.get("signals")
    if not isinstance(signals_raw, list) or not minimum_external + 1 <= len(
        signals_raw
    ) <= 100:
        raise ValueError("supply-chain signals are incomplete")
    allowed_units = set(policy["allowed_units"])
    signals: list[dict[str, Any]] = []
    signal_ids: set[str] = set()
    entities_with_signals: set[str] = set()
    primary_organizations: set[str] = set()
    supporting_entities: set[str] = set()
    supporting_organizations: set[str] = set()
    contradicting_entities: set[str] = set()
    classification_counts = {
        "supporting": 0,
        "contradicting": 0,
        "inconclusive": 0,
    }
    external_classification_counts = {
        "supporting": 0,
        "contradicting": 0,
        "inconclusive": 0,
    }
    for item in signals_raw:
        if not isinstance(item, dict):
            raise ValueError("supply-chain signal must be an object")
        signal_id = _bounded_text(
            item.get("signal_id"), field="supply_chain.signal_id", maximum=100
        )
        if signal_id in signal_ids:
            raise ValueError("supply-chain signal ids must be unique")
        signal_ids.add(signal_id)
        entity_id = item.get("entity_id")
        if entity_id not in entities:
            raise ValueError(f"supply-chain signal {signal_id} has an invalid entity")
        relationship_ids = item.get("relationship_ids")
        if (
            not isinstance(relationship_ids, list)
            or not relationship_ids
            or len(relationship_ids) != len(set(relationship_ids))
            or any(value not in relationships for value in relationship_ids)
        ):
            raise ValueError(
                f"supply-chain signal {signal_id} has invalid relationship links"
            )
        if not any(
            entity_id
            in {
                relationships[value]["from_entity_id"],
                relationships[value]["to_entity_id"],
            }
            for value in relationship_ids
        ):
            raise ValueError(
                f"supply-chain signal {signal_id} is not linked to its entity"
            )
        unit = item.get("unit")
        if unit not in allowed_units:
            raise ValueError(f"supply-chain signal {signal_id} has an invalid unit")
        prior_value = _finite_number(
            item.get("prior_value"), field=f"supply_chain.{signal_id}.prior_value"
        )
        current_value = _finite_number(
            item.get("current_value"),
            field=f"supply_chain.{signal_id}.current_value",
        )
        absolute_change = current_value - prior_value
        reported_direction = (
            "flat"
            if math.isclose(absolute_change, 0.0, abs_tol=1e-12)
            else "increase" if absolute_change > 0 else "decrease"
        )
        subject_direction = item.get("subject_metric_direction")
        if subject_direction not in {"increase", "decrease", "flat", "mixed"}:
            raise ValueError(
                f"supply-chain signal {signal_id} has an invalid subject direction"
            )
        if subject_direction == expected_direction:
            classification = "supporting"
        elif {subject_direction, expected_direction} == {"increase", "decrease"}:
            classification = "contradicting"
        else:
            classification = "inconclusive"
        if entity_id == subject_id and subject_direction != reported_direction:
            raise ValueError(
                f"supply-chain subject signal {signal_id} direction does not reconcile"
            )
        source_ids = _source_ids(
            item.get("source_ids"),
            field=f"supply_chain signal {signal_id}",
            registry=registry,
        )
        entity_organization = entities[entity_id]["source_organization"]
        primary_source_ids = [
            source_id
            for source_id in source_ids
            if registry[source_id]["organization"] == entity_organization
            and registry[source_id]["source_type"] in PRIMARY_COMPANY_SOURCE_TYPES
        ]
        if not primary_source_ids:
            raise ValueError(
                f"supply-chain signal {signal_id} lacks its entity's primary source"
            )
        entities_with_signals.add(entity_id)
        if entity_id != subject_id:
            primary_organizations.add(entity_organization)
            external_classification_counts[classification] += 1
            if classification == "supporting":
                supporting_entities.add(entity_id)
                supporting_organizations.add(entity_organization)
            elif classification == "contradicting":
                contradicting_entities.add(entity_id)
        classification_counts[classification] += 1
        signals.append(
            {
                "signal_id": signal_id,
                "entity_id": entity_id,
                "relationship_ids": sorted(relationship_ids),
                "metric": _bounded_text(
                    item.get("metric"),
                    field=f"supply_chain.{signal_id}.metric",
                    maximum=160,
                ),
                "period": _bounded_text(
                    item.get("period"),
                    field=f"supply_chain.{signal_id}.period",
                    maximum=80,
                ),
                "unit": unit,
                "prior_value": prior_value,
                "current_value": current_value,
                "absolute_change": absolute_change,
                "change_percent": _growth_percent(current_value, prior_value),
                "reported_direction": reported_direction,
                "subject_metric_direction": subject_direction,
                "classification": classification,
                "primary_source_ids": sorted(primary_source_ids),
                "source_ids": source_ids,
                "methodology": _bounded_text(
                    item.get("methodology"),
                    field=f"supply_chain.{signal_id}.methodology",
                    maximum=800,
                ),
            }
        )
    if external_ids - entities_with_signals:
        raise ValueError("every external supply-chain entity requires a signal")
    if subject_id not in entities_with_signals:
        raise ValueError("supply-chain analysis requires a subject-company signal")
    if len(primary_organizations) < minimum_external:
        raise ValueError("supply-chain evidence has too few independent primary sources")
    if external_classification_counts["contradicting"] < int(
        policy["minimum_supply_chain_counter_signals"]
    ):
        raise ValueError("supply-chain analysis requires an external counter-signal")
    minimum_supporting = int(policy["minimum_supply_chain_supporting_entities"])
    confirmed = (
        len(supporting_entities) >= minimum_supporting
        and len(supporting_organizations) >= minimum_supporting
    )
    confirmation_state = (
        "confirmed_with_counter_signals"
        if confirmed and contradicting_entities
        else "confirmed"
        if confirmed
        else "not_confirmed_with_counter_signals"
        if contradicting_entities
        else "not_confirmed"
    )
    return {
        "hypothesis": hypothesis,
        "subject_entity_id": subject_id,
        "entities": sorted(entities.values(), key=lambda item: item["entity_id"]),
        "relationships": sorted(
            relationships.values(), key=lambda item: item["relationship_id"]
        ),
        "signals": sorted(signals, key=lambda item: item["signal_id"]),
        "cross_company_confirmation": {
            "state": confirmation_state,
            "external_entity_count": len(external_ids),
            "external_role_count": len(external_roles),
            "independent_primary_organization_count": len(primary_organizations),
            "supporting_external_entity_ids": sorted(supporting_entities),
            "supporting_primary_organizations": sorted(supporting_organizations),
            "contradicting_external_entity_ids": sorted(contradicting_entities),
            "signal_counts": classification_counts,
            "external_signal_counts": external_classification_counts,
            "score_used": False,
        },
        "coverage_rationale": _bounded_text(
            raw.get("coverage_rationale"),
            field="supply_chain.coverage_rationale",
            maximum=1200,
        ),
        "methodology": _bounded_text(
            raw.get("methodology"), field="supply_chain.methodology", maximum=1200
        ),
        "source_ids": _source_ids(
            raw.get("source_ids"), field="supply_chain", registry=registry
        ),
    }


def _valuation(
    raw: Any,
    *,
    current_price: float,
    earnings: dict[str, Any],
    policy: dict[str, Any],
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("valuation must be an object")
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or len(cases_raw) != 3:
        raise ValueError("valuation must contain bear, base, and bull")
    allowed_methods = set(policy["allowed_valuation_methods"])
    scenarios: dict[str, dict[str, Any]] = {}
    earnings_by_name = {item["name"]: item for item in earnings["scenarios"]}
    for item in cases_raw:
        if not isinstance(item, dict) or item.get("name") not in {"bear", "base", "bull"}:
            raise ValueError("valuation has an invalid scenario name")
        name = str(item["name"])
        if name in scenarios:
            raise ValueError("valuation scenario names must be unique")
        probability = _finite_number(item.get("probability"), field=f"{name}.probability")
        price = _finite_number(item.get("price_target"), field=f"{name}.price_target")
        if not 0 < probability < 1 or price <= 0:
            raise ValueError(f"{name} valuation values are invalid")
        if not math.isclose(probability, earnings_by_name[name]["probability"], abs_tol=1e-9):
            raise ValueError(f"{name} valuation probability must match earnings model")
        method = item.get("method")
        if method not in allowed_methods:
            raise ValueError(f"{name} valuation method is invalid")
        scenarios[name] = {
            "name": name,
            "probability": probability,
            "price_target": price,
            "method": method,
            "equation": _bounded_text(item.get("equation"), field=f"{name}.equation", maximum=800),
            "assumptions": _text_list(item.get("assumptions"), field=f"{name}.assumptions", minimum=2),
            "source_ids": _source_ids(
                item.get("source_ids"), field=f"{name}.valuation", registry=registry
            ),
        }
    if set(scenarios) != {"bear", "base", "bull"}:
        raise ValueError("valuation must contain bear, base, and bull")
    if not math.isclose(
        sum(item["probability"] for item in scenarios.values()), 1.0, abs_tol=1e-9
    ):
        raise ValueError("valuation probabilities must sum to one")
    if not (
        scenarios["bear"]["price_target"]
        < scenarios["base"]["price_target"]
        < scenarios["bull"]["price_target"]
    ):
        raise ValueError("valuation prices must increase from bear to bull")
    ordered = [scenarios[name] for name in ("bear", "base", "bull")]
    expected_price = sum(
        item["probability"] * item["price_target"] for item in ordered
    )
    result = {
        "framework": _bounded_text(raw.get("framework"), field="valuation.framework"),
        "scenarios": ordered,
        "probability_weighted_price": expected_price,
        "probability_weighted_return": expected_price / current_price - 1,
        "bear_case_return": scenarios["bear"]["price_target"] / current_price - 1,
        "base_case_return": scenarios["base"]["price_target"] / current_price - 1,
        "bull_case_return": scenarios["bull"]["price_target"] / current_price - 1,
    }
    downside = abs(min(result["bear_case_return"], 0))
    result["reward_to_risk"] = (
        result["base_case_return"] / downside if downside > 0 else None
    )
    return result


def _risk_evidence(raw: Any, *, registry: dict[str, dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("risk_disconfirming_evidence must be an object")
    risks_raw = raw.get("risks")
    if not isinstance(risks_raw, list) or len(risks_raw) < int(policy["minimum_risks"]):
        raise ValueError("focused research has too few explicit risks")
    risks = []
    risk_ids: set[str] = set()
    for item in risks_raw:
        if not isinstance(item, dict):
            raise ValueError("risk must be an object")
        risk_id = _bounded_text(item.get("risk_id"), field="risk_id", maximum=80)
        if risk_id in risk_ids:
            raise ValueError("risk_id must be unique")
        probability = item.get("probability")
        if probability not in {"low", "medium", "high"}:
            raise ValueError(f"risk {risk_id} probability is invalid")
        risks.append({
            "risk_id": risk_id,
            "statement": _bounded_text(item.get("statement"), field="risk.statement"),
            "probability": probability,
            "impact": _bounded_text(item.get("impact"), field="risk.impact"),
            "monitor": _bounded_text(item.get("monitor"), field="risk.monitor"),
            "source_ids": _source_ids(item.get("source_ids"), field="risk", registry=registry),
        })
        risk_ids.add(risk_id)
    contrary_raw = raw.get("contrary_evidence")
    if not isinstance(contrary_raw, list) or len(contrary_raw) < int(policy["minimum_contrary_evidence"]):
        raise ValueError("focused research has too little contrary evidence")
    contrary = []
    evidence_ids: set[str] = set()
    for item in contrary_raw:
        if not isinstance(item, dict):
            raise ValueError("contrary evidence must be an object")
        evidence_id = _bounded_text(item.get("evidence_id"), field="evidence_id", maximum=80)
        if evidence_id in evidence_ids:
            raise ValueError("evidence_id must be unique")
        contrary.append({
            "evidence_id": evidence_id,
            "observation": _bounded_text(item.get("observation"), field="contrary_evidence.observation"),
            "thesis_impact": _bounded_text(item.get("thesis_impact"), field="contrary_evidence.thesis_impact"),
            "source_ids": _source_ids(item.get("source_ids"), field="contrary_evidence", registry=registry),
        })
        evidence_ids.add(evidence_id)
    return {
        "risks": sorted(risks, key=lambda item: item["risk_id"]),
        "contrary_evidence": sorted(contrary, key=lambda item: item["evidence_id"]),
        "monitoring_plan": _bounded_text(raw.get("monitoring_plan"), field="monitoring_plan"),
    }


def _position_construction(
    raw: Any, *, policy: dict[str, Any], recommendation: str
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("position_construction must be an object")
    weights = {
        field: _finite_number(raw.get(field), field=f"position_construction.{field}")
        for field in (
            "initial_weight_percent",
            "target_weight_percent",
            "maximum_weight_percent",
            "earnings_event_weight_percent",
        )
    }
    if recommendation == "buy":
        if not 0 < weights["initial_weight_percent"] <= weights["target_weight_percent"] <= weights["maximum_weight_percent"]:
            raise ValueError("buy position weights must increase from initial to maximum")
    elif any(value != 0 for value in weights.values()):
        raise ValueError("non-buy research must specify a zero position")
    if weights["initial_weight_percent"] > float(policy["maximum_initial_weight_percent"]):
        raise ValueError("initial position weight exceeds policy")
    if weights["target_weight_percent"] > float(policy["maximum_target_weight_percent"]):
        raise ValueError("target position weight exceeds policy")
    if weights["maximum_weight_percent"] > float(policy["maximum_single_name_weight_percent"]):
        raise ValueError("maximum position weight exceeds policy")
    if weights["earnings_event_weight_percent"] > min(
        weights["maximum_weight_percent"], float(policy["maximum_earnings_event_weight_percent"])
    ):
        raise ValueError("earnings event weight exceeds policy")
    entry_low = _finite_number(raw.get("entry_price_low"), field="entry_price_low")
    entry_high = _finite_number(raw.get("entry_price_high"), field="entry_price_high")
    risk_budget = _finite_number(raw.get("risk_budget_bps"), field="risk_budget_bps")
    if not 0 < entry_low <= entry_high or not 0 <= risk_budget <= 1000:
        raise ValueError("position entry range or risk budget is invalid")
    if recommendation == "buy" and risk_budget == 0:
        raise ValueError("buy research requires a positive risk budget")
    return {
        **weights,
        "entry_price_low": entry_low,
        "entry_price_high": entry_high,
        "risk_budget_bps": risk_budget,
        "sizing_rationale": _bounded_text(raw.get("sizing_rationale"), field="sizing_rationale"),
        "add_conditions": _text_list(raw.get("add_conditions"), field="add_conditions"),
        "reduce_conditions": _text_list(raw.get("reduce_conditions"), field="reduce_conditions"),
        "exit_conditions": _text_list(raw.get("exit_conditions"), field="exit_conditions"),
        "execution_authorized": False,
    }


def _score_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("score_summary must be an object")
    conviction = raw.get("investment_conviction")
    if isinstance(conviction, bool) or not isinstance(conviction, int) or not 0 <= conviction <= 100:
        raise ValueError("investment conviction must be an integer from 0 to 100")
    return {
        "investment_conviction": conviction,
        "summary": _bounded_text(raw.get("summary"), field="score_summary.summary"),
        "used_for_recommendation_gate": False,
        "used_for_position_sizing": False,
    }


def build_focused_research_dossier(
    payload: dict[str, Any],
    *,
    policy: dict[str, Any],
    code_revision: str,
) -> dict[str, Any]:
    """Build an immutable, point-in-time Variant Perception research dossier."""

    if not isinstance(payload, dict):
        raise ValueError("focused research payload must be an object")
    if not code_revision or code_revision.lower() == "unknown":
        raise ValueError("focused research requires an immutable code revision")
    symbol = _bounded_text(payload.get("symbol"), field="symbol", maximum=20).upper()
    as_of = date.fromisoformat(str(payload.get("as_of_date")))
    current_price = _finite_number(payload.get("current_price"), field="current_price")
    if current_price <= 0:
        raise ValueError("current_price must be positive")
    currency = _bounded_text(payload.get("currency"), field="currency", maximum=8).upper()
    if currency != "USD":
        raise ValueError("focused research currently requires USD prices")
    sources = _source_registry(payload.get("sources"), as_of=as_of, policy=policy)
    price_source_ids = _source_ids(
        payload.get("price_source_ids"),
        field="current_price",
        registry=sources,
        required_type="market_price",
    )

    thesis_raw = payload.get("investment_thesis")
    if not isinstance(thesis_raw, dict):
        raise ValueError("investment_thesis must be an object")
    recommendation = thesis_raw.get("recommendation")
    if recommendation not in {"buy", "watch", "avoid", "no_view"}:
        raise ValueError("focused research recommendation is invalid")
    horizon_months = thesis_raw.get("time_horizon_months")
    if isinstance(horizon_months, bool) or not isinstance(horizon_months, int) or not 1 <= horizon_months <= 60:
        raise ValueError("investment thesis time horizon is invalid")
    investment_thesis = {
        "statement": _bounded_text(thesis_raw.get("statement"), field="investment_thesis.statement"),
        "time_horizon_months": horizon_months,
        "pillars": _text_list(
            thesis_raw.get("pillars"),
            field="investment_thesis.pillars",
            minimum=int(policy["minimum_thesis_pillars"]),
            maximum=8,
        ),
        "recommendation": recommendation,
    }
    variant_raw = payload.get("variant_view")
    if not isinstance(variant_raw, dict):
        raise ValueError("variant_view must be an object")

    catalysts_raw = payload.get("catalyst_path")
    if not isinstance(catalysts_raw, list) or not catalysts_raw:
        raise ValueError("focused research requires catalysts")
    catalysts: list[dict[str, Any]] = []
    catalyst_ids: set[str] = set()
    for raw in catalysts_raw:
        if not isinstance(raw, dict):
            raise ValueError("catalyst must be an object")
        catalyst_id = _bounded_text(raw.get("catalyst_id"), field="catalyst_id", maximum=80)
        if catalyst_id in catalyst_ids:
            raise ValueError("catalyst_id must be unique")
        window_start = date.fromisoformat(str(raw.get("window_start")))
        window_end = date.fromisoformat(str(raw.get("window_end")))
        if window_start < as_of or window_end < window_start:
            raise ValueError(f"catalyst {catalyst_id} has an invalid window")
        if (window_end - as_of).days > int(policy["maximum_catalyst_horizon_days"]):
            raise ValueError(f"catalyst {catalyst_id} is outside the allowed horizon")
        catalysts.append(
            {
                "catalyst_id": catalyst_id,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "event": _bounded_text(raw.get("event"), field="catalyst.event"),
                "observable": _bounded_text(
                    raw.get("observable"), field="catalyst.observable"
                ),
                "thesis_resolution": _bounded_text(
                    raw.get("thesis_resolution"), field="catalyst.thesis_resolution"
                ),
                "source_ids": _source_ids(
                    raw.get("source_ids"), field="catalyst", registry=sources
                ),
            }
        )
        catalyst_ids.add(catalyst_id)

    chains_raw = variant_raw.get("expectation_chains")
    if not isinstance(chains_raw, list) or len(chains_raw) < int(
        policy["minimum_variant_chains"]
    ):
        raise ValueError("focused research has too few variant chains")
    allowed_units = set(policy["allowed_units"])
    allowed_methods = set(policy["allowed_implied_methods"])
    required_categories = set(policy["required_metric_categories"])
    chains: list[dict[str, Any]] = []
    metric_ids: set[str] = set()
    for raw in chains_raw:
        if not isinstance(raw, dict):
            raise ValueError("variant chain must be an object")
        metric_id = _bounded_text(raw.get("metric_id"), field="metric_id", maximum=100)
        if metric_id in metric_ids:
            raise ValueError("metric_id must be unique")
        metric_ids.add(metric_id)
        category = raw.get("category")
        if category not in required_categories:
            raise ValueError(f"variant chain {metric_id} has an unsupported category")
        unit = raw.get("unit")
        if unit not in allowed_units:
            raise ValueError(f"variant chain {metric_id} has an unsupported unit")
        favorable_direction = raw.get("favorable_direction")
        if favorable_direction not in {"higher", "lower"}:
            raise ValueError(f"variant chain {metric_id} favorable_direction is invalid")
        consensus = _estimate(
            raw.get("market_consensus"),
            field=f"{metric_id}.market_consensus",
            registry=sources,
            required_source_type="consensus_dataset",
        )
        implied_raw = raw.get("price_implied_expectation")
        if not isinstance(implied_raw, dict) or implied_raw.get("method") not in allowed_methods:
            raise ValueError(f"variant chain {metric_id} has an invalid implied method")
        implied = _estimate(
            implied_raw,
            field=f"{metric_id}.price_implied_expectation",
            registry=sources,
            required_source_type="market_price",
        )
        model_price = _finite_number(
            implied_raw.get("model_price_at_implied"),
            field=f"{metric_id}.price_implied_expectation.model_price_at_implied",
        )
        if model_price <= 0:
            raise ValueError(f"variant chain {metric_id} has an invalid model price")
        reconciliation_error = abs(model_price / current_price - 1)
        if reconciliation_error > float(
            policy["maximum_implied_price_reconciliation_error"]
        ):
            raise ValueError(
                f"variant chain {metric_id} implied model does not reconcile to price"
            )
        assumptions = implied_raw.get("assumptions")
        if not isinstance(assumptions, list) or len(assumptions) < 2:
            raise ValueError(f"variant chain {metric_id} needs at least two implied assumptions")
        normalized_assumptions = []
        for assumption in assumptions:
            if not isinstance(assumption, dict):
                raise ValueError(f"variant chain {metric_id} assumption is invalid")
            normalized_assumptions.append(
                {
                    "name": _bounded_text(
                        assumption.get("name"), field="assumption.name", maximum=120
                    ),
                    "value": _finite_number(
                        assumption.get("value"), field="assumption.value"
                    ),
                    "unit": _bounded_text(
                        assumption.get("unit"), field="assumption.unit", maximum=40
                    ),
                    "source_ids": _source_ids(
                        assumption.get("source_ids"),
                        field="assumption",
                        registry=sources,
                    ),
                }
            )
        implied.update(
            {
                "method": implied_raw["method"],
                "equation": _bounded_text(
                    implied_raw.get("equation"),
                    field=f"{metric_id}.price_implied_expectation.equation",
                    maximum=800,
                ),
                "assumptions": normalized_assumptions,
                "price_reconciliation": {
                    "observed_price": current_price,
                    "model_price_at_implied": model_price,
                    "absolute_error": model_price - current_price,
                    "relative_error": reconciliation_error,
                },
            }
        )
        house = _estimate(
            raw.get("house_estimate"),
            field=f"{metric_id}.house_estimate",
            registry=sources,
        )
        linked_catalysts = raw.get("catalyst_ids")
        if (
            not isinstance(linked_catalysts, list)
            or not linked_catalysts
            or any(item not in catalyst_ids for item in linked_catalysts)
        ):
            raise ValueError(f"variant chain {metric_id} has invalid catalyst links")
        falsifiers_raw = raw.get("falsification_criteria")
        if not isinstance(falsifiers_raw, list) or not falsifiers_raw:
            raise ValueError(f"variant chain {metric_id} requires falsification criteria")
        implied_gap = house["value"] - implied["value"]
        consensus_gap = house["value"] - consensus["value"]
        favorable_gap = implied_gap if favorable_direction == "higher" else -implied_gap
        chains.append(
            {
                "metric_id": metric_id,
                "metric_name": _bounded_text(raw.get("metric_name"), field="metric_name"),
                "category": category,
                "horizon": _bounded_text(raw.get("horizon"), field="horizon", maximum=80),
                "unit": unit,
                "favorable_direction": favorable_direction,
                "market_consensus": consensus,
                "price_implied_expectation": implied,
                "house_estimate": house,
                "difference": {
                    "house_minus_price_implied": implied_gap,
                    "house_minus_consensus": consensus_gap,
                    "favorable_gap_vs_price_implied": favorable_gap,
                    "variant_direction": (
                        "bullish" if favorable_gap > 0 else "bearish" if favorable_gap < 0 else "neutral"
                    ),
                },
                "why_market_may_be_wrong": _bounded_text(
                    raw.get("why_market_may_be_wrong"),
                    field="why_market_may_be_wrong",
                ),
                "falsification_criteria": [
                    _bounded_text(item, field="falsification_criteria", maximum=500)
                    for item in falsifiers_raw
                ],
                "catalyst_ids": sorted(set(linked_catalysts)),
            }
        )
    categories = {item["category"] for item in chains}
    if not required_categories.issubset(categories):
        raise ValueError("variant chains do not cover every required metric category")

    earnings = _earnings_model(
        payload.get("earnings_model"), registry=sources, policy=policy
    )
    earnings_quality = _earnings_quality(
        payload.get("earnings_quality"), registry=sources, policy=policy
    )
    supply_chain = _supply_chain_read_through(
        payload.get("supply_chain_read_through"),
        symbol=symbol,
        registry=sources,
        policy=policy,
    )
    valuation = _valuation(
        payload.get("valuation"),
        current_price=current_price,
        earnings=earnings,
        policy=policy,
        registry=sources,
    )
    risk_evidence = _risk_evidence(
        payload.get("risk_disconfirming_evidence"), registry=sources, policy=policy
    )
    position = _position_construction(
        payload.get("position_construction"),
        policy=policy,
        recommendation=recommendation,
    )
    score = _score_summary(payload.get("score_summary"))
    if (
        recommendation == "buy"
        and policy["buy_requires_positive_expected_return"]
        and valuation["probability_weighted_return"] <= 0
    ):
        raise ValueError("buy recommendation requires positive expected return")
    if recommendation == "buy" and not any(
        chain["difference"]["variant_direction"] == "bullish" for chain in chains
    ):
        raise ValueError("buy recommendation requires a bullish variant chain")
    if recommendation == "buy" and (
        valuation["reward_to_risk"] is None
        or valuation["reward_to_risk"] < float(policy["minimum_reward_to_risk"])
    ):
        raise ValueError("buy recommendation has insufficient reward-to-risk")
    if recommendation == "buy" and not supply_chain[
        "cross_company_confirmation"
    ]["state"].startswith("confirmed"):
        raise ValueError("buy recommendation requires cross-company confirmation")

    research_sections = {
        "investment_thesis": investment_thesis,
        "variant_view": {
            "market_expectation_summary": _bounded_text(
                variant_raw.get("market_expectation_summary"),
                field="variant_view.market_expectation_summary",
            ),
            "our_variant_summary": _bounded_text(
                variant_raw.get("our_variant_summary"),
                field="variant_view.our_variant_summary",
            ),
            "expectation_chains": sorted(chains, key=lambda item: item["metric_id"]),
        },
        "earnings_model": earnings,
        "earnings_quality": earnings_quality,
        "supply_chain_read_through": supply_chain,
        "valuation": valuation,
        "catalyst_path": sorted(catalysts, key=lambda item: (item["window_start"], item["catalyst_id"])),
        "risk_disconfirming_evidence": risk_evidence,
        "position_construction": position,
        "score_summary": score,
    }
    core = {
        "schema_version": DOSSIER_SCHEMA,
        "symbol": symbol,
        "as_of_date": as_of.isoformat(),
        "code_revision": code_revision,
        "policy_sha256": hashlib.sha256(
            _canonical_json(policy).encode("utf-8")
        ).hexdigest(),
        "current_price": current_price,
        "currency": currency,
        "price_source_ids": price_source_ids,
        "sources": sorted(sources.values(), key=lambda item: item["source_id"]),
        "research_sections": research_sections,
        "validation": {
            "state": "passed",
            "section_order": RESEARCH_SECTION_ORDER,
            "chain_order": [
                "market_consensus",
                "price_implied_expectation",
                "house_estimate",
                "difference",
                "catalysts",
            ],
            "point_in_time": True,
            "score_is_summary_only": True,
            "distinct_source_organizations": len(
                {item["organization"] for item in sources.values()}
            ),
        },
        "promotion_authorized": False,
        "execution_authorized": False,
    }
    core["dossier_id"] = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "focused-research:"
            + hashlib.sha256(_canonical_json(core).encode("utf-8")).hexdigest(),
        )
    )
    core["content_sha256"] = hashlib.sha256(
        _canonical_json(core).encode("utf-8")
    ).hexdigest()
    return core


def validate_focused_research_dossier(
    dossier: dict[str, Any],
    *,
    as_of_date: str,
    maximum_age_days: int,
) -> None:
    """Validate a built dossier before it can gate a research recommendation."""

    if dossier.get("schema_version") != DOSSIER_SCHEMA:
        raise ValueError("unsupported focused research dossier")
    if dossier.get("validation", {}).get("state") != "passed":
        raise ValueError("focused research dossier has not passed validation")
    sections = dossier.get("research_sections")
    if not isinstance(sections, dict) or list(sections) != RESEARCH_SECTION_ORDER:
        raise ValueError("focused research dossier section order is incomplete")
    earnings = sections.get("earnings_model", {})
    scenarios_raw = earnings.get("scenarios")
    if not isinstance(scenarios_raw, list) or len(scenarios_raw) != 3:
        raise ValueError("focused research driver model scenarios are incomplete")

    def require_close(actual: Any, expected: float, field: str) -> None:
        value = _finite_number(actual, field=field)
        if not math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError(f"focused research driver model {field} does not reconcile")

    def require_optional_close(
        actual: Any, expected: float | None, field: str
    ) -> None:
        if expected is None:
            if actual is not None:
                raise ValueError(
                    f"focused research driver model {field} does not reconcile"
                )
            return
        require_close(actual, expected, field)

    scenarios: dict[str, dict[str, Any]] = {}
    driver_sets: list[set[str]] = []
    income_statement_fields = (
        "revenue_usd_millions",
        "gross_profit_usd_millions",
        "gross_margin_percent",
        "variable_operating_expense_usd_millions",
        "operating_expense_usd_millions",
        "operating_income_usd_millions",
        "operating_margin_percent",
        "pretax_income_usd_millions",
        "tax_expense_usd_millions",
        "net_income_usd_millions",
        "eps_usd",
    )
    cash_flow_fields = (
        "operating_cash_flow_usd_millions",
        "capex_usd_millions",
        "free_cash_flow_usd_millions",
    )
    incremental_return_fields = (
        "incremental_revenue_usd_millions",
        "incremental_operating_income_usd_millions",
        "current_nopat_usd_millions",
        "incremental_nopat_usd_millions",
        "incremental_invested_capital_usd_millions",
        "incremental_roic_percent",
        "hurdle_rate_percent",
        "value_creation_spread_basis_points",
        "incremental_economic_profit_usd_millions",
        "incremental_operating_margin_percent",
        "incremental_capital_turnover_ratio",
        "growth_capex_revenue_multiple",
        "growth_capex_operating_income_multiple",
        "growth_capex_nopat_multiple",
        "growth_capex_payback_years",
        "reinvestment_rate_percent",
    )
    for scenario in scenarios_raw:
        if not isinstance(scenario, dict) or scenario.get("name") not in {
            "bear",
            "base",
            "bull",
        }:
            raise ValueError("focused research driver model scenario is invalid")
        name = str(scenario["name"])
        if name in scenarios:
            raise ValueError("focused research driver model scenario is duplicated")
        raw_drivers = scenario.get("revenue_drivers")
        if not isinstance(raw_drivers, list) or not raw_drivers:
            raise ValueError("focused research revenue driver model is missing")
        calculated_drivers = []
        ids: set[str] = set()
        for driver in raw_drivers:
            if not isinstance(driver, dict):
                raise ValueError("focused research revenue driver is invalid")
            driver_id = str(driver.get("driver_id"))
            if not driver_id or driver_id in ids:
                raise ValueError("focused research revenue driver id is invalid")
            ids.add(driver_id)
            revenue = (
                _finite_number(driver.get("driver_value"), field="driver_value")
                * _finite_number(
                    driver.get("company_share_percent"), field="company_share_percent"
                )
                / 100
                * _finite_number(
                    driver.get("revenue_conversion_factor"),
                    field="revenue_conversion_factor",
                )
                * _finite_number(
                    driver.get("timing_conversion_percent"),
                    field="timing_conversion_percent",
                )
                / 100
            )
            gross_profit = revenue * _finite_number(
                driver.get("gross_margin_percent"), field="driver_gross_margin"
            ) / 100
            require_close(
                driver.get("revenue_usd_millions"),
                revenue,
                f"{name}.{driver_id}.revenue",
            )
            require_close(
                driver.get("gross_profit_usd_millions"),
                gross_profit,
                f"{name}.{driver_id}.gross_profit",
            )
            calculated_drivers.append(
                {
                    **driver,
                    "revenue_usd_millions": revenue,
                    "gross_profit_usd_millions": gross_profit,
                }
            )
        model = {
            "revenue_drivers": calculated_drivers,
            "operating_expenses": scenario.get("operating_expenses"),
            "below_the_line": scenario.get("below_the_line"),
            "cash_flow": scenario.get("cash_flow"),
            "capital_efficiency": scenario.get("capital_efficiency"),
        }
        if any(
            not isinstance(model[field], dict)
            for field in (
                "operating_expenses",
                "below_the_line",
                "cash_flow",
                "capital_efficiency",
            )
        ):
            raise ValueError("focused research financial bridge is incomplete")
        calculated = _calculate_driver_scenario(model)
        for field, expected in calculated.items():
            require_optional_close(scenario.get(field), expected, f"{name}.{field}")
        for section_name, fields in (
            ("income_statement", income_statement_fields),
            ("cash_flow_bridge", cash_flow_fields),
            ("incremental_returns", incremental_return_fields),
        ):
            section = scenario.get(section_name)
            if not isinstance(section, dict):
                raise ValueError(f"focused research {section_name} is incomplete")
            for field in fields:
                require_optional_close(
                    section.get(field), calculated[field], f"{name}.{section_name}.{field}"
                )
        incremental_economics = scenario.get("incremental_economics")
        if not isinstance(incremental_economics, dict):
            raise ValueError("focused research incremental economics is incomplete")
        for field in incremental_return_fields:
            if field == "current_nopat_usd_millions":
                continue
            require_optional_close(
                incremental_economics.get(field),
                calculated[field],
                f"{name}.incremental_economics.{field}",
            )
        expected_state = (
            "negative_incremental_nopat"
            if calculated["incremental_nopat_usd_millions"] <= 0
            else (
                "value_creating_above_hurdle"
                if calculated["value_creation_spread_basis_points"] > 0
                else "positive_but_below_hurdle"
            )
        )
        if incremental_economics.get("state") != expected_state:
            raise ValueError(
                f"focused research driver model {name}.incremental_economics.state does not reconcile"
            )
        capital_efficiency = model["capital_efficiency"]
        if incremental_economics.get("investment_lag_months") != capital_efficiency.get(
            "investment_lag_months"
        ):
            raise ValueError(
                f"focused research driver model {name}.investment_lag_months does not reconcile"
            )
        for field in ("prior_period", "measurement_period_months"):
            if incremental_economics.get(field) != capital_efficiency.get(field):
                raise ValueError(
                    f"focused research driver model {name}.{field} does not reconcile"
                )
        cash_flow = model["cash_flow"]
        calculated_ending = (
            capital_efficiency["prior_period_invested_capital_usd_millions"]
            + cash_flow["maintenance_capex_usd_millions"]
            + cash_flow["growth_capex_usd_millions"]
            - cash_flow["depreciation_amortization_usd_millions"]
            + cash_flow["change_in_net_working_capital_usd_millions"]
            + capital_efficiency["acquisition_investment_usd_millions"]
            + capital_efficiency["other_invested_capital_change_usd_millions"]
        )
        require_close(
            capital_efficiency["ending_invested_capital_usd_millions"],
            calculated_ending,
            f"{name}.invested_capital_bridge.ending_invested_capital",
        )
        expected_bridge = {
            "beginning_invested_capital_usd_millions": capital_efficiency[
                "prior_period_invested_capital_usd_millions"
            ],
            "maintenance_capex_usd_millions": cash_flow[
                "maintenance_capex_usd_millions"
            ],
            "growth_capex_usd_millions": cash_flow["growth_capex_usd_millions"],
            "less_depreciation_amortization_usd_millions": cash_flow[
                "depreciation_amortization_usd_millions"
            ],
            "net_working_capital_investment_usd_millions": cash_flow[
                "change_in_net_working_capital_usd_millions"
            ],
            "acquisition_investment_usd_millions": capital_efficiency[
                "acquisition_investment_usd_millions"
            ],
            "other_invested_capital_change_usd_millions": capital_efficiency[
                "other_invested_capital_change_usd_millions"
            ],
            "calculated_ending_invested_capital_usd_millions": calculated_ending,
            "reported_ending_invested_capital_usd_millions": capital_efficiency[
                "ending_invested_capital_usd_millions"
            ],
            "reconciliation_difference_usd_millions": (
                capital_efficiency["ending_invested_capital_usd_millions"]
                - calculated_ending
            ),
        }
        bridge = incremental_economics.get("invested_capital_bridge")
        if not isinstance(bridge, dict):
            raise ValueError("focused research invested capital bridge is incomplete")
        for field, expected in expected_bridge.items():
            require_close(
                bridge.get(field),
                expected,
                f"{name}.invested_capital_bridge.{field}",
            )
        scenarios[name] = scenario
        driver_sets.append(ids)
    if set(scenarios) != {"bear", "base", "bull"} or not (
        driver_sets[0] == driver_sets[1] == driver_sets[2]
    ):
        raise ValueError("focused research driver scenarios are not comparable")

    sensitivities = earnings.get("sensitivity_cases")
    if not isinstance(sensitivities, list) or not sensitivities:
        raise ValueError("focused research driver sensitivities are incomplete")
    base = scenarios["base"]
    base_drivers = {item["driver_id"]: item for item in base["revenue_drivers"]}
    for sensitivity in sensitivities:
        if not isinstance(sensitivity, dict):
            raise ValueError("focused research driver sensitivity is invalid")
        shocks_raw = sensitivity.get("driver_shocks")
        if not isinstance(shocks_raw, list) or not shocks_raw:
            raise ValueError("focused research driver sensitivity shock is missing")
        shocks = {
            str(item.get("driver_id")): _finite_number(
                item.get("shock_percent"), field="sensitivity.shock_percent"
            )
            for item in shocks_raw
            if isinstance(item, dict)
        }
        if len(shocks) != len(shocks_raw) or not set(shocks).issubset(base_drivers):
            raise ValueError("focused research driver sensitivity shock is invalid")
        shocked_drivers = []
        for driver_id, driver in base_drivers.items():
            shock = shocks.get(driver_id, 0.0)
            shocked_value = driver["driver_value"] * (1 + shock / 100)
            revenue = (
                shocked_value
                * driver["company_share_percent"]
                / 100
                * driver["revenue_conversion_factor"]
                * driver["timing_conversion_percent"]
                / 100
            )
            shocked_drivers.append(
                {
                    **driver,
                    "driver_value": shocked_value,
                    "revenue_usd_millions": revenue,
                    "gross_profit_usd_millions": revenue
                    * driver["gross_margin_percent"]
                    / 100,
                }
            )
        shocked = _calculate_driver_scenario(
            {
                "revenue_drivers": shocked_drivers,
                "operating_expenses": base["operating_expenses"],
                "below_the_line": base["below_the_line"],
                "cash_flow": base["cash_flow"],
                "capital_efficiency": base["capital_efficiency"],
            }
        )

        def expected_percent_change(field: str) -> float | None:
            denominator = float(base[field])
            return (
                (float(shocked[field]) / denominator - 1) * 100
                if not math.isclose(denominator, 0.0, abs_tol=1e-12)
                else None
            )

        results = sensitivity.get("results")
        if not isinstance(results, dict):
            raise ValueError("focused research driver sensitivity results are missing")
        expected_results = {
            "revenue_change_percent": expected_percent_change("revenue_usd_millions"),
            "gross_margin_change_basis_points": (
                shocked["gross_margin_percent"] - base["gross_margin_percent"]
            )
            * 100,
            "operating_income_change_percent": expected_percent_change(
                "operating_income_usd_millions"
            ),
            "operating_margin_change_basis_points": (
                shocked["operating_margin_percent"] - base["operating_margin_percent"]
            )
            * 100,
            "eps_change_percent": expected_percent_change("eps_usd"),
            "operating_cash_flow_change_percent": expected_percent_change(
                "operating_cash_flow_usd_millions"
            ),
            "free_cash_flow_change_percent": expected_percent_change(
                "free_cash_flow_usd_millions"
            ),
            "incremental_roic_change_basis_points": (
                shocked["incremental_roic_percent"] - base["incremental_roic_percent"]
            )
            * 100,
            "base_eps_usd": base["eps_usd"],
            "shocked_eps_usd": shocked["eps_usd"],
            "base_free_cash_flow_usd_millions": base[
                "free_cash_flow_usd_millions"
            ],
            "shocked_free_cash_flow_usd_millions": shocked[
                "free_cash_flow_usd_millions"
            ],
        }
        for field, expected in expected_results.items():
            if expected is None:
                if results.get(field) is not None:
                    raise ValueError(
                        f"focused research driver sensitivity {field} does not reconcile"
                    )
            else:
                require_close(results.get(field), expected, f"sensitivity.{field}")
    quality = sections.get("earnings_quality")
    if not isinstance(quality, dict):
        raise ValueError("focused research earnings quality analysis is missing")
    source_registry = {
        str(item.get("source_id")): item
        for item in dossier.get("sources", [])
        if isinstance(item, dict) and item.get("source_id")
    }
    try:
        rebuilt_quality = _earnings_quality(
            {
                "periods": {
                    "prior_period": quality.get("prior_period"),
                    "current_period": quality.get("current_period"),
                },
                "balance_sheet": quality.get("balance_sheet_inputs"),
                "cash_conversion": quality.get("cash_conversion_inputs"),
                "capital_investment": quality.get("capital_investment_inputs"),
                "earnings_bridge": quality.get("earnings_bridge_inputs"),
                "adjustments": quality.get("adjustments"),
                "methodology": quality.get("methodology"),
                "source_ids": quality.get("source_ids"),
            },
            registry=source_registry,
            policy={
                "required_earnings_quality_adjustment_ids": list(
                    EARNINGS_QUALITY_ADJUSTMENT_IDS
                )
            },
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "focused research earnings quality analysis does not reconcile"
        ) from exc
    if rebuilt_quality != quality:
        raise ValueError("focused research earnings quality analysis does not reconcile")
    supply_chain = sections.get("supply_chain_read_through")
    if not isinstance(supply_chain, dict):
        raise ValueError("focused research supply-chain read-through is missing")
    confirmation = supply_chain.get("cross_company_confirmation", {})
    try:
        rebuilt_supply_chain = _supply_chain_read_through(
            {
                "hypothesis": supply_chain.get("hypothesis"),
                "entities": supply_chain.get("entities"),
                "relationships": supply_chain.get("relationships"),
                "signals": supply_chain.get("signals"),
                "coverage_rationale": supply_chain.get("coverage_rationale"),
                "methodology": supply_chain.get("methodology"),
                "source_ids": supply_chain.get("source_ids"),
            },
            symbol=str(dossier.get("symbol")),
            registry=source_registry,
            policy={
                "minimum_supply_chain_external_entities": 3,
                "minimum_supply_chain_external_roles": 2,
                "minimum_supply_chain_supporting_entities": 3,
                "minimum_supply_chain_counter_signals": 1,
                "minimum_supply_chain_relationships": 3,
                "allowed_units": [
                    "percent",
                    "basis_points",
                    "usd",
                    "usd_millions",
                    "per_share_usd",
                    "ratio",
                    "multiple",
                    "units",
                ],
            },
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "focused research supply-chain read-through does not reconcile"
        ) from exc
    if rebuilt_supply_chain != supply_chain:
        raise ValueError(
            "focused research supply-chain read-through does not reconcile"
        )
    if confirmation.get("score_used") is not False:
        raise ValueError("focused research supply-chain score attempted to control a decision")
    score = sections.get("score_summary", {})
    if score.get("used_for_recommendation_gate") is not False or score.get(
        "used_for_position_sizing"
    ) is not False:
        raise ValueError("focused research score attempted to control a decision")
    if sections.get("position_construction", {}).get("execution_authorized") is not False:
        raise ValueError("position construction attempted to authorize execution")
    if dossier.get("execution_authorized") is not False or dossier.get(
        "promotion_authorized"
    ) is not False:
        raise ValueError("focused research dossier attempted to authorize execution")
    current = date.fromisoformat(as_of_date)
    dossier_date = date.fromisoformat(str(dossier.get("as_of_date")))
    if dossier_date > current:
        raise ValueError("focused research dossier is from the future")
    if (current - dossier_date).days > maximum_age_days:
        raise ValueError("focused research dossier is stale")
    supplied_hash = dossier.get("content_sha256")
    body = dict(dossier)
    body.pop("content_sha256", None)
    expected_hash = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
    if supplied_hash != expected_hash:
        raise ValueError("focused research dossier content hash mismatch")
    id_body = dict(body)
    supplied_id = id_body.pop("dossier_id", None)
    expected_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "focused-research:"
            + hashlib.sha256(_canonical_json(id_body).encode("utf-8")).hexdigest(),
        )
    )
    if supplied_id != expected_id:
        raise ValueError("focused research dossier identity mismatch")
    required_chain = [
        "market_consensus",
        "price_implied_expectation",
        "house_estimate",
        "difference",
        "catalysts",
    ]
    if dossier["validation"].get("chain_order") != required_chain:
        raise ValueError("focused research dossier chain is incomplete")
    if dossier["validation"].get("section_order") != RESEARCH_SECTION_ORDER:
        raise ValueError("focused research dossier section declaration is incomplete")


def render_focused_research_memo(dossier: dict[str, Any]) -> str:
    """Render the validated dossier in the required decision sequence, score last."""

    validate_focused_research_dossier(
        dossier,
        as_of_date=str(dossier["as_of_date"]),
        maximum_age_days=0,
    )
    sections = dossier["research_sections"]
    thesis = sections["investment_thesis"]
    variant = sections["variant_view"]
    earnings = sections["earnings_model"]
    quality = sections["earnings_quality"]
    supply_chain = sections["supply_chain_read_through"]
    valuation = sections["valuation"]
    risks = sections["risk_disconfirming_evidence"]
    position = sections["position_construction"]
    score = sections["score_summary"]
    earnings_cases = {item["name"]: item for item in earnings["scenarios"]}
    base_case = earnings_cases["base"]
    base_economics = base_case["incremental_economics"]
    base_capital_bridge = base_economics["invested_capital_bridge"]
    quality_growth = quality["balance_sheet_growth"]
    quality_cash = quality["accruals_and_cash_conversion"]
    quality_eps = quality["eps_growth_attribution"]
    attribution_by_id = {
        item["driver_id"]: item for item in quality_eps["components"]
    }
    supply_entities = {
        item["entity_id"]: item for item in supply_chain["entities"]
    }
    supply_confirmation = supply_chain["cross_company_confirmation"]

    def formatted_change(value: float | None) -> str:
        return "not meaningful" if value is None else f"{value:+.1f}%"

    def formatted_points(value: float | None) -> str:
        return "not meaningful" if value is None else f"{value:+.1f} pp"

    def formatted_multiple(value: float | None) -> str:
        return "not meaningful" if value is None else f"{value:.2f}x"

    def formatted_years(value: float | None) -> str:
        return "not meaningful" if value is None else f"{value:.1f} years"

    lines = [
        f"# {dossier['symbol']} Focused Research",
        "",
        f"As of: {dossier['as_of_date']} | Price: {dossier['currency']} {dossier['current_price']:.2f}",
        "",
        "## 1. Investment Thesis",
        "",
        thesis["statement"],
        f"Recommendation: {thesis['recommendation']} | Horizon: {thesis['time_horizon_months']} months",
        "",
        *[f"- {item}" for item in thesis["pillars"]],
        "",
        "## 2. Variant View",
        "",
        f"Market expectation: {variant['market_expectation_summary']}",
        f"Our view: {variant['our_variant_summary']}",
        "",
    ]
    for chain in variant["expectation_chains"]:
        lines.extend([
            f"### {chain['metric_name']} ({chain['horizon']})",
            "",
            f"Consensus {chain['market_consensus']['value']} {chain['unit']} → price-implied {chain['price_implied_expectation']['value']} → house {chain['house_estimate']['value']}",
            f"Gap vs price-implied: {chain['difference']['house_minus_price_implied']:+.2f} {chain['unit']} ({chain['difference']['variant_direction']})",
            f"Why different: {chain['why_market_may_be_wrong']}",
            f"Disconfirming test: {'; '.join(chain['falsification_criteria'])}",
            "",
        ])
    lines.extend([
        "## 3. Earnings Model",
        "",
        f"{earnings['forecast_period']} EPS — market-implied ${earnings['market_implied_eps_usd']:.2f}, consensus ${earnings['consensus_eps_usd']:.2f}, bear ${earnings_cases['bear']['eps_usd']:.2f}, base ${earnings_cases['base']['eps_usd']:.2f}, bull ${earnings_cases['bull']['eps_usd']:.2f}",
        f"Base vs consensus: {earnings['base_vs_consensus_percent']:+.1f}% | Base vs price-implied: {earnings['base_vs_market_implied_percent']:+.1f}%",
        "",
        "### Scenario model outputs",
        "",
        "| Case | Revenue ($m) | GM | Operating income ($m) | EPS | OCF ($m) | Maintenance CAPEX ($m) | Growth CAPEX ($m) | FCF ($m) | Incremental ROIC |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *[
            f"| {case['name']} | {case['revenue_usd_millions']:.1f} | {case['gross_margin_percent']:.1f}% | {case['operating_income_usd_millions']:.1f} | ${case['eps_usd']:.2f} | {case['operating_cash_flow_usd_millions']:.1f} | {case['cash_flow']['maintenance_capex_usd_millions']:.1f} | {case['cash_flow']['growth_capex_usd_millions']:.1f} | {case['free_cash_flow_usd_millions']:.1f} | {case['incremental_roic_percent']:.1f}% |"
            for case in earnings["scenarios"]
        ],
        "",
        "### Base-case revenue drivers",
        "",
        "| Segment | Economic driver | Driver value | Share | Revenue conversion | Timing | Revenue ($m) | Segment GM |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
        *[
            f"| {driver['segment_name']} | {driver['economic_driver']} | {driver['driver_value']:.1f} {driver['driver_unit']} | {driver['company_share_percent']:.1f}% | {driver['revenue_conversion_factor']:.4f} | {driver['timing_conversion_percent']:.1f}% | {driver['revenue_usd_millions']:.1f} | {driver['gross_margin_percent']:.1f}% |"
            for driver in base_case["revenue_drivers"]
        ],
        "",
        "### Base-case financial bridge",
        "",
        f"Revenue ${base_case['revenue_usd_millions']:.1f}m → gross profit ${base_case['gross_profit_usd_millions']:.1f}m → operating expense ${base_case['operating_expense_usd_millions']:.1f}m → operating income ${base_case['operating_income_usd_millions']:.1f}m → tax ${base_case['tax_expense_usd_millions']:.1f}m → net income ${base_case['net_income_usd_millions']:.1f}m → EPS ${base_case['eps_usd']:.2f}",
        f"Net income ${base_case['net_income_usd_millions']:.1f}m → OCF ${base_case['operating_cash_flow_usd_millions']:.1f}m → maintenance CAPEX ${base_case['cash_flow']['maintenance_capex_usd_millions']:.1f}m + growth CAPEX ${base_case['cash_flow']['growth_capex_usd_millions']:.1f}m → FCF ${base_case['free_cash_flow_usd_millions']:.1f}m → incremental ROIC {base_case['incremental_roic_percent']:.1f}%",
        "",
        "### Incremental economics",
        "",
        f"Measurement: {base_economics['prior_period']} → {earnings['forecast_period']} over {base_economics['measurement_period_months']} months | stated investment-to-output lag {base_economics['investment_lag_months']} months. These are period economics; causal attribution requires the stated source methodology.",
        "",
        "| Case | Incremental revenue ($m) | Incremental operating income ($m) | Incremental NOPAT ($m) | Incremental invested capital ($m) | Incremental ROIC | Hurdle | Spread | Economic profit ($m) | State |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        *[
            f"| {case['name']} | {case['incremental_revenue_usd_millions']:+.1f} | {case['incremental_operating_income_usd_millions']:+.1f} | {case['incremental_nopat_usd_millions']:+.1f} | {case['incremental_invested_capital_usd_millions']:+.1f} | {case['incremental_roic_percent']:.1f}% | {case['hurdle_rate_percent']:.1f}% | {case['value_creation_spread_basis_points']:+.0f} bps | {case['incremental_economic_profit_usd_millions']:+.1f} | {case['incremental_economics']['state']} |"
            for case in earnings["scenarios"]
        ],
        "",
        "### Base-case invested-capital bridge and CAPEX productivity",
        "",
        f"Beginning invested capital ${base_capital_bridge['beginning_invested_capital_usd_millions']:.1f}m + maintenance CAPEX ${base_capital_bridge['maintenance_capex_usd_millions']:.1f}m + growth CAPEX ${base_capital_bridge['growth_capex_usd_millions']:.1f}m − D&A ${base_capital_bridge['less_depreciation_amortization_usd_millions']:.1f}m + net working-capital investment ${base_capital_bridge['net_working_capital_investment_usd_millions']:+.1f}m + acquisitions ${base_capital_bridge['acquisition_investment_usd_millions']:.1f}m + other ${base_capital_bridge['other_invested_capital_change_usd_millions']:+.1f}m = ending invested capital ${base_capital_bridge['reported_ending_invested_capital_usd_millions']:.1f}m (reconciliation difference ${base_capital_bridge['reconciliation_difference_usd_millions']:+.1f}m)",
        f"Growth CAPEX productivity: incremental revenue {formatted_multiple(base_economics['growth_capex_revenue_multiple'])} | incremental operating income {formatted_multiple(base_economics['growth_capex_operating_income_multiple'])} | incremental NOPAT {formatted_multiple(base_economics['growth_capex_nopat_multiple'])} | payback {formatted_years(base_economics['growth_capex_payback_years'])}",
        f"Incremental operating margin {formatted_change(base_economics['incremental_operating_margin_percent'])} | incremental capital turnover {formatted_multiple(base_economics['incremental_capital_turnover_ratio'])} | reinvestment rate {formatted_change(base_economics['reinvestment_rate_percent'])}",
        "",
        "### Driver sensitivities",
        "",
        "| Shock | Revenue impact | Operating income impact | EPS impact | OCF impact | FCF impact | Incremental ROIC impact |",
        "|---|---:|---:|---:|---:|---:|---:|",
        *[
            f"| {case['label']} | {formatted_change(case['results']['revenue_change_percent'])} | {formatted_change(case['results']['operating_income_change_percent'])} | {formatted_change(case['results']['eps_change_percent'])} | {formatted_change(case['results']['operating_cash_flow_change_percent'])} | {formatted_change(case['results']['free_cash_flow_change_percent'])} | {case['results']['incremental_roic_change_basis_points']:+.0f} bps |"
            for case in earnings["sensitivity_cases"]
        ],
        "",
        "## 4. Earnings Quality",
        "",
        f"Comparison: {quality['prior_period']} → {quality['current_period']} | Reported GAAP EPS growth: {formatted_change(quality_eps['reported_eps_growth_percent'])}",
        "",
        "### Balance-sheet growth versus revenue",
        "",
        "| Metric | Growth | Gap vs revenue | Absolute change ($m) |",
        "|---|---:|---:|---:|",
        f"| Revenue | {formatted_change(quality_growth['revenue_growth_percent'])} | — | — |",
        *[
            f"| {label} | {formatted_change(quality_growth[key]['growth_percent'])} | {formatted_points(quality_growth[key]['minus_revenue_growth_percentage_points'])} | {quality_growth[key]['absolute_change_usd_millions']:+.1f} |"
            for key, label in (
                ("accounts_receivable", "Accounts receivable"),
                ("inventory", "Inventory"),
                ("contract_liabilities", "Contract liabilities"),
                ("deferred_revenue", "Deferred revenue"),
            )
        ],
        "",
        "### Accruals and cash conversion",
        "",
        f"Accruals ${quality_cash['accruals_usd_millions']:+.1f}m ({quality_cash['accrual_ratio_percent_of_average_assets']:+.1f}% of average assets) | cash conversion {formatted_change(quality_cash['cash_conversion_percent'])} | working-capital contribution ${quality_cash['working_capital_contribution_usd_millions']:+.1f}m ({formatted_change(quality_cash['working_capital_contribution_percent_of_ocf'])} of OCF)",
        f"SBC ${quality['stock_based_compensation']['current_expense_usd_millions']:.1f}m | {quality['stock_based_compensation']['percent_of_revenue']:.1f}% of revenue | growth {formatted_change(quality['stock_based_compensation']['growth_percent'])}",
        f"D&A/CAPEX {quality['capital_intensity']['current_depreciation_to_capex_percent']:.1f}% | D&A growth {formatted_change(quality['capital_intensity']['depreciation_growth_percent'])} | CAPEX growth {formatted_change(quality['capital_intensity']['capex_growth_percent'])}",
        "",
        "### GAAP to non-GAAP reconciliation",
        "",
        f"GAAP EPS ${quality['gaap_to_non_gaap_reconciliation']['periods']['current']['gaap_eps_usd']:.2f} → non-GAAP EPS ${quality['gaap_to_non_gaap_reconciliation']['periods']['current']['non_gaap_eps_usd']:.2f} | premium {formatted_change(quality['gaap_to_non_gaap_reconciliation']['current_non_gaap_eps_premium_percent'])}",
        "",
        "| Adjustment | Location | Current pretax impact ($m) | Current after-tax impact ($m) | EPS growth contribution |",
        "|---|---|---:|---:|---:|",
        *[
            f"| {item['label']} | {item['income_statement_location']} | {item['current_pretax_impact_usd_millions']:+.1f} | {item['current_after_tax_impact_usd_millions']:+.1f} | {formatted_points(attribution_by_id.get(item['adjustment_id'], {}).get('contribution_percentage_points'))} |"
            for item in quality["adjustments"]
        ],
        "",
        "### EPS growth attribution",
        "",
        "| Driver | Contribution |",
        "|---|---:|",
        *[
            f"| {item['label']} | {formatted_points(item['contribution_percentage_points'])} |"
            for item in quality_eps["components"]
        ],
        f"| Reconciled EPS growth | {formatted_points(quality_eps['reconciled_total_percentage_points'])} |",
        "",
        f"Share-repurchase contribution: {formatted_points(quality_eps['share_repurchase_contribution_percentage_points'])} ({formatted_change(quality_eps['share_repurchase_share_of_eps_growth_percent'])} of total EPS growth)",
        "",
        "## 5. Supply-chain Read-through",
        "",
        f"Hypothesis: {supply_chain['hypothesis']['statement']}",
        f"Subject metric: {supply_chain['hypothesis']['subject_metric']} | expected direction: {supply_chain['hypothesis']['expected_direction']} | period: {supply_chain['hypothesis']['forecast_period']}",
        f"Cross-company confirmation: {supply_confirmation['state']} | external entities {supply_confirmation['external_entity_count']} | independent primary organizations {supply_confirmation['independent_primary_organization_count']} | supporting entities {len(supply_confirmation['supporting_external_entity_ids'])} | external contradicting signals {supply_confirmation['external_signal_counts']['contradicting']}",
        "",
        "### Transmission map",
        "",
        "| From | Relationship | To | Upstream metric | Subject metric | Lag | Mechanism |",
        "|---|---|---|---|---|---:|---|",
        *[
            f"| {supply_entities[item['from_entity_id']]['name']} | {item['relationship_type']} | {supply_entities[item['to_entity_id']]['name']} | {item['upstream_metric']} | {item['subject_metric']} | {item['expected_lag_months']}m | {item['mechanism']} |"
            for item in supply_chain["relationships"]
        ],
        "",
        "### Cross-company signals",
        "",
        "| Entity | Role | Metric / period | Prior | Current | Change | Read-through | Classification |",
        "|---|---|---|---:|---:|---:|---|---|",
        *[
            f"| {supply_entities[item['entity_id']]['name']} | {supply_entities[item['entity_id']]['role']} | {item['metric']} / {item['period']} | {item['prior_value']:.2f} {item['unit']} | {item['current_value']:.2f} {item['unit']} | {formatted_change(item['change_percent'])} | {item['subject_metric_direction']} | {item['classification']} |"
            for item in supply_chain["signals"]
        ],
        "",
        f"Coverage: {supply_chain['coverage_rationale']}",
        f"Falsification: {'; '.join(supply_chain['hypothesis']['falsification_criteria'])}",
        "",
        "## 6. Valuation",
        "",
        f"Probability-weighted price: ${valuation['probability_weighted_price']:.2f} | Expected return: {valuation['probability_weighted_return']:+.1%} | Bear return: {valuation['bear_case_return']:+.1%}",
        f"Reward/risk: {valuation['reward_to_risk']:.2f}" if valuation["reward_to_risk"] is not None else "Reward/risk: not meaningful",
        "",
        "## 7. Catalyst Path",
        "",
        *[
            f"- {item['window_start']}–{item['window_end']}: {item['event']} — {item['observable']}"
            for item in sections["catalyst_path"]
        ],
        "",
        "## 8. Risk / Disconfirming Evidence",
        "",
        *[f"- Risk ({item['probability']}): {item['statement']} — monitor: {item['monitor']}" for item in risks["risks"]],
        *[f"- Contrary evidence: {item['observation']} — impact: {item['thesis_impact']}" for item in risks["contrary_evidence"]],
        "",
        "## 9. Position Construction",
        "",
        f"Initial {position['initial_weight_percent']:.2f}% → target {position['target_weight_percent']:.2f}% → maximum {position['maximum_weight_percent']:.2f}%",
        f"Entry ${position['entry_price_low']:.2f}–${position['entry_price_high']:.2f} | risk budget {position['risk_budget_bps']:.0f} bps | earnings-event cap {position['earnings_event_weight_percent']:.2f}%",
        position["sizing_rationale"],
        "",
        "## 10. Score Summary",
        "",
        f"Investment Conviction: {score['investment_conviction']}/100",
        score["summary"],
        "",
        "Score is summary-only and is not used for recommendation gating or position sizing.",
        "",
    ])
    return "\n".join(lines)


def load_focused_research_dossiers(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    return [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
