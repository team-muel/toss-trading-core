import copy
import unittest
from datetime import date, timedelta

from toss_trading.research.stock_recommendations import (
    generate_stock_recommendations,
    load_recommendation_policy,
)
from tests.focused_research_fixtures import (
    earnings_call_payload,
    estimate_revision_payload,
    estimate_revision_sources,
)
from toss_trading.research.variant_perception import (
    build_focused_research_dossier,
    load_focused_research_policy,
)


def earnings_quality_payload():
    adjustments = [
        ("stock_based_compensation", "Stock-based compensation", "operating", -500, -600, -400, -492),
        ("restructuring", "Restructuring", "operating", -100, -80, -80, -65.6),
        ("acquisition_adjustment", "Acquisition adjustment", "operating", -50, -70, -40, -57.4),
        ("tax_benefit", "Tax benefit", "tax", 0, 0, 100, 120),
        ("one_off_gain", "One-off gain", "non_operating", 75, 100, 60, 82),
    ]
    adjustment_rows = [
        {
            "adjustment_id": adjustment_id,
            "label": label,
            "income_statement_location": location,
            "prior_pretax_impact_usd_millions": prior_pretax,
            "current_pretax_impact_usd_millions": current_pretax,
            "prior_after_tax_impact_usd_millions": prior_after_tax,
            "current_after_tax_impact_usd_millions": current_after_tax,
            "source_ids": ["filing"],
            "methodology": "Reconcile the disclosed GAAP impact to the normalized earnings bridge.",
        }
        for adjustment_id, label, location, prior_pretax, current_pretax, prior_after_tax, current_after_tax in adjustments
    ]

    def bridge(period):
        current = period == "current"
        core_operating_income = 9000 if current else 8000
        recurring_non_operating = -100
        tax_rate = 18 if current else 20
        shares = 800 if current else 840
        prefix = "current" if current else "prior"
        core_pretax = core_operating_income + recurring_non_operating
        core_net_income = core_pretax * (1 - tax_rate / 100)
        operating_adjustments = sum(row[f"{prefix}_pretax_impact_usd_millions"] for row in adjustment_rows if row["income_statement_location"] == "operating")
        pretax_adjustments = sum(row[f"{prefix}_pretax_impact_usd_millions"] for row in adjustment_rows if row["income_statement_location"] in {"operating", "non_operating"})
        after_tax_adjustments = sum(row[f"{prefix}_after_tax_impact_usd_millions"] for row in adjustment_rows)
        gaap_net_income = core_net_income + after_tax_adjustments
        return {
            "core_operating_income_usd_millions": core_operating_income,
            "reported_gaap_operating_income_usd_millions": core_operating_income + operating_adjustments,
            "recurring_non_operating_income_usd_millions": recurring_non_operating,
            "normalized_tax_rate_percent": tax_rate,
            "reported_gaap_pretax_income_usd_millions": core_pretax + pretax_adjustments,
            "reported_gaap_net_income_usd_millions": gaap_net_income,
            "reported_gaap_eps_usd": gaap_net_income / shares,
            "reported_non_gaap_net_income_usd_millions": core_net_income,
            "reported_non_gaap_eps_usd": core_net_income / shares,
            "diluted_shares_millions": shares,
            "buyback_attributable_weighted_average_share_reduction_millions": 18 if current else 0,
        }

    return {
        "periods": {"prior_period": "FY2026", "current_period": "FY2027"},
        "balance_sheet": {
            "prior": {"revenue_usd_millions": 30000, "accounts_receivable_usd_millions": 4000, "inventory_usd_millions": 3000, "contract_liabilities_usd_millions": 2000, "deferred_revenue_usd_millions": 1000},
            "current": {"revenue_usd_millions": 36000, "accounts_receivable_usd_millions": 5200, "inventory_usd_millions": 3900, "contract_liabilities_usd_millions": 2600, "deferred_revenue_usd_millions": 1250},
            "source_ids": ["filing"],
            "methodology": "Compare reported period-end balances with the corresponding annual revenue periods.",
        },
        "cash_conversion": {
            "prior": {"operating_cash_flow_usd_millions": 6500, "total_assets_usd_millions": 40000, "working_capital_contribution_usd_millions": -300},
            "current": {"operating_cash_flow_usd_millions": 7200, "total_assets_usd_millions": 45000, "working_capital_contribution_usd_millions": -600},
            "source_ids": ["filing"],
            "methodology": "Use reported operating cash flow and signed working-capital cash contribution.",
        },
        "capital_investment": {
            "prior": {"depreciation_amortization_usd_millions": 1300, "capex_usd_millions": 1800},
            "current": {"depreciation_amortization_usd_millions": 1500, "capex_usd_millions": 2200},
            "source_ids": ["filing"],
            "methodology": "Compare reported depreciation and amortization with cash capital expenditures.",
        },
        "earnings_bridge": {
            "prior": bridge("prior"),
            "current": bridge("current"),
            "source_ids": ["filing"],
            "methodology": "Reconcile core operating profit, recurring below-line items, tax, adjustments, and diluted shares to EPS.",
        },
        "adjustments": adjustment_rows,
        "methodology": "Analyze earnings quality independently from valuation and reconcile every reported and adjusted figure.",
        "source_ids": ["filing"],
    }


def trading_dates(count: int) -> list[str]:
    current = date(2025, 8, 18)
    result = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


class StockRecommendationTest(unittest.TestCase):
    def setUp(self):
        base = load_recommendation_policy(
            "config/stock_recommendation_policy.json"
        )
        self.policy = copy.deepcopy(base)
        self.policy["minimum_universe_size"] = 3
        self.policy["target_universe_size"] = 4
        self.policy["maximum_universe_size"] = 5
        self.policy["maximum_screening_candidates"] = 2
        self.policy["maximum_buy_recommendations"] = 2
        self.policy["minimum_median_dollar_volume_usd"] = 1

    def rows(self):
        dates = trading_dates(253)
        rows = []
        for symbol, slope, volatility in [
            ("AAA", 0.003, 0.0),
            ("BBB", 0.002, 0.001),
            ("CCC", 0.001, 0.002),
            ("DDD", -0.001, 0.0),
        ]:
            price = 20.0
            for index, market_date in enumerate(dates):
                price *= 1 + slope + (volatility if index % 2 else -volatility)
                rows.append(
                    {
                        "symbol": symbol,
                        "exchange_local_date": market_date,
                        "close": price,
                        "volume": 1_000_000,
                    }
                )
        return dates[-1], rows

    def focused_dossier(self, as_of: str):
        sources = [
            {"source_id": "px", "source_type": "market_price", "organization": "Exchange", "observed_at": as_of, "locator": "close"},
            {"source_id": "consensus", "source_type": "consensus_dataset", "organization": "Consensus Provider", "observed_at": as_of, "locator": "snapshot"},
            {"source_id": "filing", "source_type": "company_filing", "organization": "AAA", "observed_at": as_of, "locator": "10-Q"},
            {"source_id": "industry", "source_type": "industry_dataset", "organization": "Industry Body", "observed_at": as_of, "locator": "industry outlook"},
            {"source_id": "customer_a", "source_type": "company_guidance", "organization": "Customer A", "observed_at": as_of, "locator": "capacity guidance"},
            {"source_id": "customer_b", "source_type": "company_guidance", "organization": "Customer B", "observed_at": as_of, "locator": "capital plan"},
            {"source_id": "supplier", "source_type": "earnings_transcript", "organization": "Supplier Co", "observed_at": as_of, "locator": "earnings call"},
            {"source_id": "peer", "source_type": "earnings_transcript", "organization": "Peer Co", "observed_at": as_of, "locator": "earnings call"},
            {"source_id": "aaa_prior_call", "source_type": "earnings_transcript", "organization": "AAA", "observed_at": as_of, "locator": "prior-quarter earnings call transcript"},
            {"source_id": "aaa_current_call", "source_type": "earnings_transcript", "organization": "AAA", "observed_at": as_of, "locator": "current-quarter earnings call transcript"},
            *estimate_revision_sources(as_of=as_of, prefix="aaa_revision"),
        ]
        chains = []
        for metric_id, category, consensus, implied, house in [
            ("driver", "industry_or_revenue_driver", 8.0, 6.0, 10.0),
            ("margin", "margin_or_cash_flow", 30.0, 29.0, 32.0),
            ("roic", "capital_efficiency_or_valuation", 15.0, 13.0, 18.0),
        ]:
            chains.append({
                "metric_id": metric_id,
                "metric_name": metric_id,
                "category": category,
                "horizon": "FY2027",
                "unit": "percent",
                "favorable_direction": "higher",
                "market_consensus": {"value": consensus, "source_ids": ["consensus"], "methodology": "Point-in-time median consensus."},
                "price_implied_expectation": {
                    "value": implied,
                    "source_ids": ["px", "filing"],
                    "methodology": "Solve the valuation model for this metric while holding stated assumptions fixed.",
                    "method": "reverse_dcf",
                    "model_price_at_implied": 30.0,
                    "equation": "Current enterprise value equals discounted forecast cash flows and terminal value.",
                    "assumptions": [
                        {"name": "discount rate", "value": 9.0, "unit": "percent", "source_ids": ["px"]},
                        {"name": "terminal growth", "value": 3.0, "unit": "percent", "source_ids": ["filing"]},
                    ],
                },
                "house_estimate": {"value": house, "source_ids": ["filing", "industry"], "methodology": "Driver-based estimate using disclosed capacity and industry demand."},
                "why_market_may_be_wrong": "The market extrapolates the current mix and underweights the disclosed higher-return capacity ramp.",
                "falsification_criteria": ["Two consecutive quarters miss the disclosed capacity ramp."],
                "catalyst_ids": ["earnings"],
            })

        def scenario(name, probability, scale, margin_delta, fixed_opex):
            drivers = []
            for driver_id, segment_name, driver_value, share, gross_margin in [
                ("core", "Core systems", 1000, 60, 35),
                ("growth", "Growth systems", 1000, 50, 30),
            ]:
                drivers.append({
                    "driver_id": driver_id,
                    "segment_name": segment_name,
                    "economic_driver": f"{segment_name} addressable demand",
                    "driver_value": driver_value * scale,
                    "driver_unit": "usd_millions",
                    "company_share_percent": share,
                    "revenue_conversion_factor": 1.0,
                    "timing_conversion_percent": 100.0,
                    "gross_margin_percent": gross_margin + margin_delta,
                    "source_ids": ["filing", "industry"],
                    "methodology": "Addressable demand multiplied by share and timing.",
                })
            return {
                "name": name,
                "probability": probability,
                "revenue_drivers": drivers,
                "operating_expenses": {
                    "fixed_usd_millions": fixed_opex,
                    "variable_percent_of_revenue": 5.0,
                    "source_ids": ["filing"],
                    "methodology": "Fixed operating costs plus a revenue-linked pool.",
                },
                "below_the_line": {
                    "net_interest_expense_usd_millions": 5,
                    "other_non_operating_income_usd_millions": 0,
                    "tax_rate_percent": 20,
                    "diluted_shares_millions": 55,
                    "source_ids": ["filing"],
                    "methodology": "Normalized tax rate and current diluted share base.",
                },
                "cash_flow": {
                    "depreciation_amortization_usd_millions": 30,
                    "stock_based_compensation_usd_millions": 10,
                    "change_in_net_working_capital_usd_millions": 12 * scale,
                    "other_operating_cash_adjustments_usd_millions": 0,
                    "maintenance_capex_usd_millions": 30,
                    "growth_capex_usd_millions": 40,
                    "source_ids": ["filing"],
                    "methodology": "Net income to OCF bridge with split CAPEX.",
                },
                "capital_efficiency": {
                    "prior_period": "FY2026",
                    "measurement_period_months": 12,
                    "prior_period_revenue_usd_millions": 1000,
                    "prior_period_operating_income_usd_millions": 150,
                    "prior_period_invested_capital_usd_millions": 800,
                    "ending_invested_capital_usd_millions": (
                        800 + 30 + 40 - 30 + 12 * scale
                    ),
                    "hurdle_rate_percent": 10.0,
                    "acquisition_investment_usd_millions": 0,
                    "other_invested_capital_change_usd_millions": 0,
                    "investment_lag_months": 18,
                    "source_ids": ["filing"],
                    "methodology": "Reconciled capital bridge and incremental NOPAT relative to the hurdle rate.",
                },
                "key_assumptions": [
                    "Demand and share are modeled independently.",
                    "Cash conversion follows the explicit bridge.",
                ],
                "source_ids": ["filing", "industry"],
                "methodology": "Bottom-up driver model.",
            }
        payload = {
            "symbol": "AAA",
            "as_of_date": as_of,
            "current_price": 30.0,
            "currency": "USD",
            "price_source_ids": ["px"],
            "investment_thesis": {"statement": "Capacity mix creates an underappreciated earnings and return uplift.", "time_horizon_months": 12, "pillars": ["Capacity mix lifts growth.", "Incremental returns exceed the implied case."], "recommendation": "buy"},
            "variant_view": {
                "market_expectation_summary": "The market expects a modest cycle recovery with limited incremental returns.",
                "our_variant_summary": "Capacity mix should lift growth, margin, and incremental returns above price-implied levels.",
                "expectation_chains": chains,
                "estimate_revision": estimate_revision_payload(
                    as_of=as_of,
                    prefix="aaa_revision",
                    prior_price=32.0,
                    current_price=30.0,
                ),
            },
            "sources": sources,
            "catalyst_path": [{
                "catalyst_id": "earnings",
                "window_start": as_of,
                "window_end": as_of,
                "event": "Quarterly results",
                "observable": "Revenue mix, gross margin, and incremental return disclosures",
                "thesis_resolution": "The reported mix either closes or invalidates the modeled expectation gap.",
                "source_ids": ["filing"],
            }],
            "earnings_model": {"forecast_period": "FY2027", "market_implied_eps_usd": 2.5, "market_implied_eps_source_ids": ["px", "filing"], "market_implied_eps_methodology": "Solve implied FY2027 EPS at the observed price.", "consensus_eps_usd": 2.8, "consensus_eps_source_ids": ["consensus"], "consensus_eps_methodology": "Point-in-time median FY2027 EPS.", "scenarios": [
                scenario("bear", 0.2, 0.82, -3.0, 80),
                scenario("base", 0.5, 1.0, 0.0, 80),
                scenario("bull", 0.3, 1.18, 3.0, 85),
            ], "sensitivity_cases": [{
                "sensitivity_id": "growth_demand_plus_10",
                "label": "Growth demand +10%",
                "driver_shocks": [{"driver_id": "growth", "shock_percent": 10.0}],
                "source_ids": ["filing", "industry"],
                "rationale": "Measure the EPS and FCF flow-through from the growth demand driver.",
            }]},
            "earnings_quality": earnings_quality_payload(),
            "supply_chain_read_through": {
                "hypothesis": {
                    "hypothesis_id": "capacity_read_through",
                    "statement": "Independent customer capacity plans and supplier bookings support higher subject revenue, with peer guidance retained as a counter-signal.",
                    "subject_metric": "Growth systems revenue",
                    "forecast_period": "FY2027",
                    "expected_direction": "increase",
                    "falsification_criteria": [
                        "Customer capacity plans decline.",
                        "Supplier bookings and subject backlog both contract.",
                    ],
                },
                "entities": [
                    {"entity_id": "aaa", "name": "AAA", "identifier": "AAA", "role": "subject_company", "source_organization": "AAA"},
                    {"entity_id": "customer_a", "name": "Customer A", "identifier": "CUSTA", "role": "customer", "source_organization": "Customer A"},
                    {"entity_id": "customer_b", "name": "Customer B", "identifier": "CUSTB", "role": "customer", "source_organization": "Customer B"},
                    {"entity_id": "supplier", "name": "Supplier Co", "identifier": "SUP", "role": "supplier", "source_organization": "Supplier Co"},
                    {"entity_id": "peer", "name": "Peer Co", "identifier": "PEER", "role": "competitor", "source_organization": "Peer Co"},
                ],
                "relationships": [
                    {"relationship_id": "customer_a_to_aaa", "from_entity_id": "customer_a", "to_entity_id": "aaa", "relationship_type": "customer_of", "upstream_metric": "Capacity plan", "subject_metric": "Growth systems revenue", "expected_lag_months": 9, "mechanism": "Customer capacity investment expands the addressable equipment demand pool after procurement timing.", "source_ids": ["customer_a", "filing"]},
                    {"relationship_id": "customer_b_to_aaa", "from_entity_id": "customer_b", "to_entity_id": "aaa", "relationship_type": "customer_of", "upstream_metric": "Capital plan", "subject_metric": "Growth systems revenue", "expected_lag_months": 12, "mechanism": "The second customer provides an independent demand-pool confirmation with a separate budget cycle.", "source_ids": ["customer_b", "filing"]},
                    {"relationship_id": "supplier_to_aaa", "from_entity_id": "supplier", "to_entity_id": "aaa", "relationship_type": "supplier_to", "upstream_metric": "Component bookings", "subject_metric": "Shipment capacity", "expected_lag_months": 6, "mechanism": "Supplier bookings test whether demand propagates upstream from the subject company.", "source_ids": ["supplier", "filing"]},
                    {"relationship_id": "peer_to_aaa", "from_entity_id": "peer", "to_entity_id": "aaa", "relationship_type": "competes_with", "upstream_metric": "Peer outlook", "subject_metric": "Industry demand and relative share", "expected_lag_months": 3, "mechanism": "Peer guidance distinguishes common demand from subject-specific share assumptions.", "source_ids": ["peer", "filing"]},
                ],
                "signals": [
                    {"signal_id": "aaa_backlog", "entity_id": "aaa", "relationship_ids": ["customer_a_to_aaa"], "metric": "Backlog", "period": "FY2027 outlook", "unit": "usd_millions", "prior_value": 900, "current_value": 1000, "subject_metric_direction": "increase", "source_ids": ["filing"], "methodology": "Use subject-company backlog only as the non-independent anchor."},
                    {"signal_id": "customer_a_capacity", "entity_id": "customer_a", "relationship_ids": ["customer_a_to_aaa"], "metric": "Capacity plan", "period": "FY2027 plan", "unit": "units", "prior_value": 100, "current_value": 110, "subject_metric_direction": "increase", "source_ids": ["customer_a"], "methodology": "Compare primary company capacity guidance across the same planning horizon."},
                    {"signal_id": "customer_b_capital", "entity_id": "customer_b", "relationship_ids": ["customer_b_to_aaa"], "metric": "Capital plan", "period": "FY2027 plan", "unit": "usd_millions", "prior_value": 500, "current_value": 575, "subject_metric_direction": "increase", "source_ids": ["customer_b"], "methodology": "Use the customer's disclosed capital plan and preserve procurement lag."},
                    {"signal_id": "supplier_bookings", "entity_id": "supplier", "relationship_ids": ["supplier_to_aaa"], "metric": "Component bookings", "period": "FY2027 run-rate", "unit": "units", "prior_value": 80, "current_value": 88, "subject_metric_direction": "increase", "source_ids": ["supplier"], "methodology": "Use supplier bookings as an independent upstream confirmation."},
                    {"signal_id": "peer_outlook", "entity_id": "peer", "relationship_ids": ["peer_to_aaa"], "metric": "Peer revenue outlook", "period": "FY2027 outlook", "unit": "usd_millions", "prior_value": 700, "current_value": 680, "subject_metric_direction": "decrease", "source_ids": ["peer"], "methodology": "Retain weaker peer guidance as explicit counterevidence."},
                ],
                "coverage_rationale": "Cover two customers with separate budget cycles, one upstream supplier, one competitor, and the subject-company anchor using issuer-primary evidence.",
                "methodology": "Map numeric issuer signals through explicit relationships and lags, count independent organizations, and preserve contradictory evidence.",
                "source_ids": ["filing", "customer_a", "customer_b", "supplier", "peer"],
            },
            "earnings_call_diff": earnings_call_payload(
                as_of=as_of,
                prior_call_source="aaa_prior_call",
                current_call_source="aaa_current_call",
                primary_source="filing",
            ),
            "valuation": {"framework": "Scenario EPS and normalized P/E cross-checked against cash flow.", "cases": [
                {"name": "bear", "probability": 0.2, "price_target": 26.0, "method": "pe", "equation": "Bear EPS times 13x.", "assumptions": ["Multiple contracts.", "No mix premium."], "source_ids": ["px", "consensus"]},
                {"name": "base", "probability": 0.5, "price_target": 42.0, "method": "pe", "equation": "Base EPS times 13.1x.", "assumptions": ["Normal multiple.", "Partial mix premium."], "source_ids": ["px", "consensus"]},
                {"name": "bull", "probability": 0.3, "price_target": 54.0, "method": "pe", "equation": "Bull EPS times 13.5x.", "assumptions": ["Growth persists.", "Full mix premium."], "source_ids": ["px", "consensus"]},
            ]},
            "risk_disconfirming_evidence": {"risks": [
                {"risk_id": "margin", "statement": "Margin does not expand.", "probability": "medium", "impact": "EPS upside disappears.", "monitor": "Gross margin stays below 30%.", "source_ids": ["filing"]},
                {"risk_id": "demand", "statement": "Capacity is underutilized.", "probability": "medium", "impact": "Revenue misses the base case.", "monitor": "Orders decline for two quarters.", "source_ids": ["industry"]},
            ], "contrary_evidence": [{"evidence_id": "spend", "observation": "Capacity spend precedes demand.", "thesis_impact": "Free cash flow may weaken.", "source_ids": ["filing"]}], "monitoring_plan": "Refresh consensus and reverse valuation after every earnings release."},
            "position_construction": {"initial_weight_percent": 1.0, "target_weight_percent": 3.0, "maximum_weight_percent": 5.0, "earnings_event_weight_percent": 1.5, "entry_price_low": 28.0, "entry_price_high": 31.0, "risk_budget_bps": 40, "sizing_rationale": "Start below target pending mix confirmation.", "add_conditions": ["Mix and margin confirm."], "reduce_conditions": ["Price reaches value before evidence."], "exit_conditions": ["Orders and margin falsify the thesis."]},
            "score_summary": {"investment_conviction": 78, "summary": "A positive but evidence-dependent setup."},
        }
        return build_focused_research_dossier(
            payload,
            policy=load_focused_research_policy("config/focused_research_policy.json"),
            code_revision="b" * 40,
        )

    def test_screening_candidates_are_not_buy_recommendations_without_dossier(self):
        as_of, rows = self.rows()
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            source_manifest_ids=["manifest-1"],
        )
        self.assertEqual(result["universe"]["ranked_count"], 4)
        self.assertEqual(result["schema_version"], "stock-recommendation-run-v9")
        self.assertEqual(len(result["screening_candidates"]), 2)
        self.assertEqual(result["screening_candidates"][0]["symbol"], "AAA")
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(
            result["recommendation_gate"]["withheld_pending_focused_research_count"],
            2,
        )
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(
            result["prospective_tracking"]["performance_revealed"]
        )
        self.assertEqual(
            result["focused_research_queue"][0]["required_sections"][-1],
            "score_summary",
        )

    def test_buy_recommendation_requires_passed_variant_perception_dossier(self):
        as_of, rows = self.rows()
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[self.focused_dossier(as_of)],
        )
        self.assertEqual([item["symbol"] for item in result["recommendations"]], ["AAA"])
        self.assertEqual(
            result["recommendations"][0]["focused_research_state"],
            "buy_dossier_passed",
        )
        self.assertGreater(
            result["recommendations"][0]["valuation"]["probability_weighted_return"], 0
        )
        earnings = result["recommendations"][0]["earnings_model"]
        self.assertEqual(earnings["market_implied_eps_usd"], 2.5)
        self.assertGreater(earnings["base_vs_consensus_percent"], 0)
        self.assertEqual(len(earnings["scenarios"][1]["revenue_drivers"]), 2)
        self.assertGreater(
            earnings["sensitivity_cases"][0]["results"]["eps_change_percent"], 0
        )
        incremental = earnings["scenarios"][1]["incremental_economics"]
        self.assertEqual(incremental["state"], "value_creating_above_hurdle")
        self.assertAlmostEqual(
            incremental["invested_capital_bridge"][
                "reconciliation_difference_usd_millions"
            ],
            0,
        )
        quality = result["recommendations"][0]["earnings_quality"]
        self.assertEqual(quality["eps_growth_attribution"]["state"], "reconciled")
        self.assertEqual(len(quality["adjustments"]), 5)
        supply_chain = result["recommendations"][0]["supply_chain_read_through"]
        self.assertEqual(
            supply_chain["cross_company_confirmation"]["state"],
            "confirmed_with_counter_signals",
        )
        earnings_call = result["recommendations"][0]["earnings_call_diff"]
        self.assertEqual(
            earnings_call["management_guidance_calibration"]["quarter_count"], 8
        )
        estimate_revision = result["recommendations"][0]["variant_view"][
            "estimate_revision"
        ]
        self.assertEqual(
            estimate_revision["price_divergence"]["state"],
            "positive_revision_price_decline",
        )
        self.assertEqual(list(result["recommendations"][0])[-1], "score_summary")

    def test_stale_dossier_returns_to_research_queue_instead_of_failing_run(self):
        as_of, rows = self.rows()
        stale_date = (date.fromisoformat(as_of) - timedelta(days=30)).isoformat()
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[self.focused_dossier(stale_date)],
            focused_research_maximum_age_days=14,
        )
        self.assertEqual(result["recommendations"], [])
        aaa = next(
            item for item in result["screening_candidates"] if item["symbol"] == "AAA"
        )
        self.assertEqual(aaa["focused_research_state"], "focused_research_stale")

    def test_legacy_dossier_requires_driver_model_upgrade_without_failing_run(self):
        as_of, rows = self.rows()
        legacy = copy.deepcopy(self.focused_dossier(as_of))
        legacy["schema_version"] = "focused-research-dossier-v2"
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[legacy],
        )
        aaa = next(
            item for item in result["screening_candidates"] if item["symbol"] == "AAA"
        )
        self.assertEqual(
            aaa["focused_research_state"],
            "focused_research_driver_model_required",
        )
        self.assertEqual(
            result["recommendation_gate"]["driver_model_upgrade_required_count"], 1
        )
        self.assertEqual(result["recommendations"], [])

    def test_v3_dossier_requires_earnings_quality_upgrade_without_failing_run(self):
        as_of, rows = self.rows()
        legacy = copy.deepcopy(self.focused_dossier(as_of))
        legacy["schema_version"] = "focused-research-dossier-v3"
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[legacy],
        )
        aaa = next(
            item for item in result["screening_candidates"] if item["symbol"] == "AAA"
        )
        self.assertEqual(
            aaa["focused_research_state"],
            "focused_research_earnings_quality_required",
        )
        self.assertEqual(
            result["recommendation_gate"][
                "earnings_quality_upgrade_required_count"
            ],
            1,
        )
        self.assertEqual(result["recommendations"], [])

    def test_v4_dossier_requires_incremental_economics_upgrade_without_failing_run(self):
        as_of, rows = self.rows()
        legacy = copy.deepcopy(self.focused_dossier(as_of))
        legacy["schema_version"] = "focused-research-dossier-v4"
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[legacy],
        )
        aaa = next(
            item for item in result["screening_candidates"] if item["symbol"] == "AAA"
        )
        self.assertEqual(
            aaa["focused_research_state"],
            "focused_research_incremental_economics_required",
        )
        self.assertEqual(
            result["recommendation_gate"][
                "incremental_economics_upgrade_required_count"
            ],
            1,
        )
        self.assertEqual(result["recommendations"], [])

    def test_v5_dossier_requires_supply_chain_upgrade_without_failing_run(self):
        as_of, rows = self.rows()
        legacy = copy.deepcopy(self.focused_dossier(as_of))
        legacy["schema_version"] = "focused-research-dossier-v5"
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[legacy],
        )
        aaa = next(
            item for item in result["screening_candidates"] if item["symbol"] == "AAA"
        )
        self.assertEqual(
            aaa["focused_research_state"],
            "focused_research_supply_chain_required",
        )
        self.assertEqual(
            result["recommendation_gate"]["supply_chain_upgrade_required_count"], 1
        )
        self.assertEqual(result["recommendations"], [])

    def test_v6_dossier_requires_earnings_call_upgrade_without_failing_run(self):
        as_of, rows = self.rows()
        legacy = copy.deepcopy(self.focused_dossier(as_of))
        legacy["schema_version"] = "focused-research-dossier-v6"
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[legacy],
        )
        aaa = next(
            item for item in result["screening_candidates"] if item["symbol"] == "AAA"
        )
        self.assertEqual(
            aaa["focused_research_state"],
            "focused_research_earnings_call_required",
        )
        self.assertEqual(
            result["recommendation_gate"]["earnings_call_upgrade_required_count"], 1
        )
        self.assertEqual(result["recommendations"], [])

    def test_v7_dossier_requires_estimate_revision_upgrade_without_failing_run(self):
        as_of, rows = self.rows()
        legacy = copy.deepcopy(self.focused_dossier(as_of))
        legacy["schema_version"] = "focused-research-dossier-v7"
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[legacy],
        )
        aaa = next(
            item for item in result["screening_candidates"] if item["symbol"] == "AAA"
        )
        self.assertEqual(
            aaa["focused_research_state"],
            "focused_research_estimate_revision_required",
        )
        self.assertEqual(
            result["recommendation_gate"][
                "estimate_revision_upgrade_required_count"
            ],
            1,
        )
        self.assertEqual(result["recommendations"], [])

    def test_newest_legacy_dossier_version_controls_upgrade_queue(self):
        as_of, rows = self.rows()
        v3 = copy.deepcopy(self.focused_dossier(as_of))
        v3["schema_version"] = "focused-research-dossier-v3"
        v5 = copy.deepcopy(self.focused_dossier(as_of))
        v5["schema_version"] = "focused-research-dossier-v5"
        v6 = copy.deepcopy(self.focused_dossier(as_of))
        v6["schema_version"] = "focused-research-dossier-v6"
        v7 = copy.deepcopy(self.focused_dossier(as_of))
        v7["schema_version"] = "focused-research-dossier-v7"
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[v3, v5, v6, v7],
        )
        aaa = next(
            item for item in result["screening_candidates"] if item["symbol"] == "AAA"
        )
        self.assertEqual(
            aaa["focused_research_state"],
            "focused_research_estimate_revision_required",
        )

    def test_refuses_too_small_universe(self):
        as_of, rows = self.rows()
        self.policy["minimum_universe_size"] = 5
        with self.assertRaisesRegex(ValueError, "smaller"):
            generate_stock_recommendations(
                rows,
                policy=self.policy,
                as_of_date=as_of,
                code_revision="a" * 40,
            )

    def test_checked_in_policy_is_gated_at_2500_names(self):
        policy = load_recommendation_policy(
            "config/stock_recommendation_policy.json"
        )
        self.assertFalse(policy["enabled"])
        self.assertEqual(policy["target_universe_size"], 2500)
        self.assertGreaterEqual(policy["minimum_universe_size"], 2000)
        self.assertLessEqual(policy["maximum_universe_size"], 3000)
        self.assertFalse(policy["execution_authorized"])


if __name__ == "__main__":
    unittest.main()
