from datetime import date, timedelta


def positioning_sources(*, as_of: str, prefix: str) -> list[dict]:
    current = date.fromisoformat(as_of)
    rows = [
        ("sec_13f_prior", "ownership_dataset", "SEC 13F Normalized Dataset", current - timedelta(days=100)),
        ("sec_13f_current", "ownership_dataset", "SEC 13F Normalized Dataset", current - timedelta(days=10)),
        ("sec_13g", "regulatory_filing", "SEC EDGAR", current - timedelta(days=20)),
        ("sec_form4_purchase", "regulatory_filing", "SEC EDGAR", current - timedelta(days=15)),
        ("sec_form4_sale", "regulatory_filing", "SEC EDGAR", current - timedelta(days=5)),
        ("ownership", "ownership_dataset", "Ownership Data Vendor", current),
        ("etf", "etf_holdings_dataset", "ETF Holdings Vendor", current),
        ("short_prior", "short_interest_dataset", "FINRA Short Interest", current - timedelta(days=30)),
        ("short_current", "short_interest_dataset", "FINRA Short Interest", current - timedelta(days=5)),
        ("short_volume", "short_sale_volume_dataset", "FINRA Short Sale Volume", current),
        ("borrow", "securities_lending_dataset", "Securities Lending Vendor", current),
        ("options", "options_market_dataset", "Options Market Data Vendor", current),
    ]
    return [
        {
            "source_id": f"{prefix}_{source_id}",
            "source_type": source_type,
            "organization": organization,
            "observed_at": observed_at.isoformat(),
            "locator": f"immutable {source_id} positioning snapshot",
        }
        for source_id, source_type, organization, observed_at in rows
    ]


def positioning_payload(*, as_of: str, prefix: str, current_price: float) -> dict:
    current = date.fromisoformat(as_of)
    source = lambda suffix: f"{prefix}_{suffix}"
    holders = [
        ("holder_a", "Large Passive A", "passive", 120),
        ("holder_b", "Large Passive B", "passive", 100),
        ("holder_c", "Active Manager C", "active", 80),
        ("holder_d", "Active Manager D", "active", 60),
        ("holder_e", "Strategic Holder E", "strategic", 50),
        ("holder_f", "Insider Group F", "insider", 40),
    ]
    realized_prices = [
        current_price * (0.94 + index * 0.0028 + (0.002 if index % 2 else -0.001))
        for index in range(22)
    ]
    realized_prices[-1] = current_price
    first_expiry = current + timedelta(days=30)
    second_expiry = current + timedelta(days=60)
    strikes = [current_price * 0.9, current_price, current_price * 1.1]
    open_interest = []
    for expiry_index, expiration in enumerate((first_expiry, second_expiry)):
        for strike_index, strike in enumerate(strikes):
            for option_type in ("call", "put"):
                open_interest.append(
                    {
                        "expiration": expiration.isoformat(),
                        "strike": strike,
                        "option_type": option_type,
                        "open_interest": (
                            1000
                            + expiry_index * 300
                            + strike_index * 250
                            + (150 if option_type == "put" else 0)
                        ),
                    }
                )
    return {
        "institutional_ownership": {
            "shares_outstanding": 1000,
            "prior_13f": {
                "report_date": (current - timedelta(days=130)).isoformat(),
                "snapshot_date": (current - timedelta(days=100)).isoformat(),
                "institutional_shares": 600,
                "reporting_manager_count": 400,
                "source_ids": [source("sec_13f_prior")],
            },
            "current_13f": {
                "report_date": (current - timedelta(days=40)).isoformat(),
                "snapshot_date": (current - timedelta(days=10)).isoformat(),
                "institutional_shares": 630,
                "reporting_manager_count": 420,
                "source_ids": [source("sec_13f_current")],
            },
            "major_holders": [
                {
                    "holder_id": holder_id,
                    "holder_name": name,
                    "style": style,
                    "shares": shares,
                    "source_ids": [source("ownership")],
                }
                for holder_id, name, style, shares in holders
            ],
            "beneficial_ownership_filings": [
                {
                    "filing_id": "holder_a_13g_amendment",
                    "form_type": "13G/A",
                    "filer": "Large Passive A",
                    "event_date": (current - timedelta(days=25)).isoformat(),
                    "filing_date": (current - timedelta(days=20)).isoformat(),
                    "ownership_percent": 12.0,
                    "change_percentage_points": 1.0,
                    "intent": "passive",
                    "source_ids": [source("sec_13g")],
                }
            ],
            "form4_transactions": [
                {
                    "transaction_id": "insider_purchase",
                    "insider": "Chief Executive Officer",
                    "role": "CEO",
                    "transaction_date": (current - timedelta(days=16)).isoformat(),
                    "filing_date": (current - timedelta(days=15)).isoformat(),
                    "transaction_code": "P",
                    "direction": "acquired",
                    "shares": 1000,
                    "price_per_share": current_price * 0.95,
                    "ownership_nature": "direct",
                    "source_ids": [source("sec_form4_purchase")],
                },
                {
                    "transaction_id": "insider_sale",
                    "insider": "Chief Financial Officer",
                    "role": "CFO",
                    "transaction_date": (current - timedelta(days=6)).isoformat(),
                    "filing_date": (current - timedelta(days=5)).isoformat(),
                    "transaction_code": "S",
                    "direction": "disposed",
                    "shares": 500,
                    "price_per_share": current_price,
                    "ownership_nature": "direct",
                    "source_ids": [source("sec_form4_sale")],
                },
            ],
            "etf_exposures": [
                {
                    "etf_id": "broad_market_etf",
                    "etf_name": "Broad Market ETF",
                    "snapshot_date": current.isoformat(),
                    "shares": 70,
                    "fund_weight_percent": 1.5,
                    "source_ids": [source("etf")],
                },
                {
                    "etf_id": "sector_etf",
                    "etf_name": "Sector ETF",
                    "snapshot_date": current.isoformat(),
                    "shares": 40,
                    "fund_weight_percent": 4.0,
                    "source_ids": [source("etf")],
                },
            ],
            "methodology": "Separate lagged 13F aggregates, beneficial ownership filings, insider transactions, classified major holders, and ETF holdings without double counting ETF shares as an addition to passive ownership.",
            "source_ids": [
                source("sec_13f_prior"), source("sec_13f_current"),
                source("sec_13g"), source("sec_form4_purchase"),
                source("sec_form4_sale"), source("ownership"), source("etf"),
            ],
        },
        "short_positioning": {
            "prior": {
                "settlement_date": (current - timedelta(days=35)).isoformat(),
                "publication_date": (current - timedelta(days=30)).isoformat(),
                "shares_short": 50,
                "float_shares": 900,
                "average_daily_volume": 14,
                "source_ids": [source("short_prior")],
            },
            "current": {
                "settlement_date": (current - timedelta(days=10)).isoformat(),
                "publication_date": (current - timedelta(days=5)).isoformat(),
                "shares_short": 60,
                "float_shares": 900,
                "average_daily_volume": 15,
                "source_ids": [source("short_current")],
            },
            "short_sale_volume": {
                "window_start": (current - timedelta(days=4)).isoformat(),
                "window_end": current.isoformat(),
                "short_volume": 400,
                "total_volume": 1000,
                "source_ids": [source("short_volume")],
            },
            "borrow": {
                "availability_state": "available",
                "as_of_date": current.isoformat(),
                "available_shares": 25,
                "borrow_fee_percent": 1.8,
                "utilization_percent": 42,
                "source_ids": [source("borrow")],
            },
            "methodology": "Treat settlement-date short interest, daily short-sale volume, and securities-lending availability as distinct datasets and never infer one directly from another.",
            "source_ids": [
                source("short_prior"), source("short_current"),
                source("short_volume"), source("borrow"),
            ],
        },
        "options_positioning": {
            "as_of_date": current.isoformat(),
            "spot_price": current_price,
            "iv_term_structure": [
                {"expiration": first_expiry.isoformat(), "atm_iv_percent": 40},
                {"expiration": second_expiry.isoformat(), "atm_iv_percent": 38},
                {"expiration": (current + timedelta(days=120)).isoformat(), "atm_iv_percent": 35},
            ],
            "atm_iv_history_percent": [20 + (index % 30) * 0.5 for index in range(252)],
            "realized_price_history": realized_prices,
            "skew": {
                "put_25_delta_iv_percent": 45,
                "atm_iv_percent": 40,
                "call_25_delta_iv_percent": 37,
            },
            "open_interest": open_interest,
            "earnings_expected_move": {
                "event_date": (current + timedelta(days=7)).isoformat(),
                "expiration": first_expiry.isoformat(),
                "atm_strike": current_price,
                "call_price": current_price * 0.04,
                "put_price": current_price * 0.035,
            },
            "post_earnings_iv_crush": [
                {
                    "event_date": (current - timedelta(days=90 * index)).isoformat(),
                    "pre_event_atm_iv_percent": 52 - index,
                    "post_event_atm_iv_percent": 36 - index,
                    "source_ids": [source("options")],
                }
                for index in range(1, 5)
            ],
            "methodology": "Recalculate IV percentile, realized volatility, 25-delta skew, OI concentrations, earnings straddle expected move, and historical IV crush from point-in-time option and price inputs.",
            "source_ids": [source("options")],
        },
        "macro_futures_overlay": {
            "applicability": "not_applicable",
            "positions": [],
            "rationale": "CFTC futures positioning is not issuer ownership or single-name short/options positioning and is excluded unless a documented macro-futures transmission channel is part of the thesis.",
        },
        "methodology": "Analyze ownership, short positioning, and options separately, retain reporting lags and dataset limitations, and prohibit positioning summaries from directly gating recommendations or sizing positions.",
        "source_ids": [
            source("sec_13f_prior"), source("sec_13f_current"), source("sec_13g"),
            source("sec_form4_purchase"), source("sec_form4_sale"), source("ownership"),
            source("etf"), source("short_prior"), source("short_current"),
            source("short_volume"), source("borrow"), source("options"),
        ],
    }


def estimate_revision_sources(*, as_of: str, prefix: str) -> list[dict]:
    current_date = date.fromisoformat(as_of)
    dates = {
        "prior": current_date - timedelta(days=30),
        "before_event": current_date - timedelta(days=10),
        "after_event": current_date - timedelta(days=3),
        "current": current_date,
    }
    return [
        {
            "source_id": f"{prefix}_consensus_{name}",
            "source_type": "consensus_dataset",
            "organization": "Point-in-time Consensus Vendor",
            "observed_at": observed.isoformat(),
            "locator": f"immutable {name} estimate snapshot",
        }
        for name, observed in dates.items()
    ] + [
        {
            "source_id": f"{prefix}_price_prior",
            "source_type": "market_price",
            "organization": "Primary Market Price Feed",
            "observed_at": dates["prior"].isoformat(),
            "locator": "official close at revision-window start",
        },
        {
            "source_id": f"{prefix}_price_current",
            "source_type": "market_price",
            "organization": "Primary Market Price Feed",
            "observed_at": dates["current"].isoformat(),
            "locator": "official close at revision-window end",
        },
    ]


def estimate_revision_payload(
    *,
    as_of: str,
    prefix: str,
    prior_price: float,
    current_price: float,
) -> dict:
    current_date = date.fromisoformat(as_of)
    prior_date = current_date - timedelta(days=30)
    before_event_date = current_date - timedelta(days=10)
    event_date = current_date - timedelta(days=7)
    after_event_date = current_date - timedelta(days=3)

    def snapshot(name: str, value: float, analyst_count: int = 20) -> dict:
        dates = {
            "prior": prior_date,
            "before_event": before_event_date,
            "after_event": after_event_date,
            "current": current_date,
        }
        return {
            "snapshot_date": dates[name].isoformat(),
            "value": value,
            "analyst_count": analyst_count,
            "source_ids": [f"{prefix}_consensus_{name}"],
        }

    metric_inputs = [
        ("fy1_eps", "fy1_eps", "FY1", "per_share_usd", 10.0, 10.7),
        ("fy2_eps", "fy2_eps", "FY2", "per_share_usd", 11.0, 11.4),
        ("revenue", "revenue", "FY1", "usd_millions", 30000, 31500),
        ("ebitda", "ebitda", "FY1", "usd_millions", 10000, 10300),
        ("free_cash_flow", "free_cash_flow", "FY1", "usd_millions", 7000, 7350),
    ]
    metric_revisions = [
        {
            "metric_id": metric_id,
            "metric_type": metric_type,
            "horizon": horizon,
            "unit": unit,
            "prior": snapshot("prior", prior_value, 19),
            "current": snapshot("current", current_value, 20),
        }
        for metric_id, metric_type, horizon, unit, prior_value, current_value
        in metric_inputs
    ]
    target_prior = {
        "snapshot_date": prior_date.isoformat(),
        "minimum": 180,
        "p25": 220,
        "median": 250,
        "mean": 252,
        "p75": 280,
        "maximum": 320,
        "analyst_count": 19,
        "source_ids": [f"{prefix}_consensus_prior"],
    }
    target_current = {
        "snapshot_date": current_date.isoformat(),
        "minimum": 185,
        "p25": 230,
        "median": 260,
        "mean": 263,
        "p75": 295,
        "maximum": 340,
        "analyst_count": 20,
        "source_ids": [f"{prefix}_consensus_current"],
    }
    return {
        "comparison_window_days": 30,
        "metric_revisions": metric_revisions,
        "target_price_distribution": {
            "currency": "USD",
            "prior": target_prior,
            "current": target_current,
        },
        "earnings_event_revision": {
            "event_id": "latest_quarterly_earnings",
            "event_date": event_date.isoformat(),
            "event": "Latest quarterly earnings release",
            "metrics": [
                {
                    "metric_id": "event_fy1_eps",
                    "metric_type": "fy1_eps",
                    "horizon": "FY1",
                    "unit": "per_share_usd",
                    "before_event": snapshot("before_event", 10.2, 20),
                    "after_event": snapshot("after_event", 10.7, 20),
                },
                {
                    "metric_id": "event_revenue",
                    "metric_type": "revenue",
                    "horizon": "FY1",
                    "unit": "usd_millions",
                    "before_event": snapshot("before_event", 30500, 20),
                    "after_event": snapshot("after_event", 31500, 20),
                },
            ],
        },
        "analyst_revision_breadth": {
            "horizon": "FY1 EPS",
            "window_start": prior_date.isoformat(),
            "window_end": current_date.isoformat(),
            "raised": 12,
            "lowered": 3,
            "unchanged": 5,
            "source_ids": [f"{prefix}_consensus_current"],
        },
        "price_context": {
            "prior_date": prior_date.isoformat(),
            "current_date": current_date.isoformat(),
            "prior_price": prior_price,
            "current_price": current_price,
            "source_ids": [
                f"{prefix}_price_prior",
                f"{prefix}_price_current",
            ],
        },
        "methodology": "Compare immutable point-in-time consensus snapshots, event-bracketing snapshots, analyst action breadth, and matched market closes without using the divergence as a recommendation gate.",
        "source_ids": [
            f"{prefix}_consensus_prior",
            f"{prefix}_consensus_before_event",
            f"{prefix}_consensus_after_event",
            f"{prefix}_consensus_current",
            f"{prefix}_price_prior",
            f"{prefix}_price_current",
        ],
    }


def earnings_call_payload(
    *,
    as_of: str,
    prior_call_source: str,
    current_call_source: str,
    primary_source: str,
) -> dict:
    current_date = date.fromisoformat(as_of)
    prior_date = current_date - timedelta(days=90)
    history_rows = []
    history_values = [
        (90, 100, 102),
        (100, 110, 108),
        (110, 120, 118),
        (120, 130, 128),
        (130, 140, 142),
        (140, 150, 147),
        (150, 160, 149),
        (160, 170, 172),
    ]
    for index, (low, high, actual) in enumerate(history_values, start=1):
        period_end = current_date - timedelta(days=(9 - index) * 90)
        history_rows.append(
            {
                "fiscal_period": f"Q{index}",
                "period_end": period_end.isoformat(),
                "metric_id": "quarterly_revenue",
                "unit": "usd_millions",
                "favorable_direction": "higher",
                "guidance_low": low,
                "guidance_high": high,
                "actual": actual,
                "source_ids": [primary_source],
            }
        )
    return {
        "prior_call": {
            "period": "Prior quarter",
            "call_date": prior_date.isoformat(),
            "source_ids": [prior_call_source],
            "statements": [
                {
                    "topic_id": "demand",
                    "statement_type": "demand",
                    "speaker": "Chief Executive Officer",
                    "text": "Demand remains strong.",
                    "confidence_level": 2,
                    "horizon": "next two quarters",
                },
                {
                    "topic_id": "capacity_constraint",
                    "statement_type": "capacity",
                    "speaker": "Chief Executive Officer",
                    "text": "Capacity constraints are expected through 2026.",
                    "confidence_level": 3,
                    "horizon": "through 2026",
                },
                {
                    "topic_id": "margin_progress",
                    "statement_type": "margin",
                    "speaker": "Chief Financial Officer",
                    "text": "Gross margin should improve gradually.",
                    "confidence_level": 2,
                    "horizon": "next fiscal year",
                },
            ],
            "analyst_questions": [
                {
                    "theme_id": "backlog_conversion",
                    "analyst_organization": "Research Firm A",
                    "question": "How quickly will backlog convert to revenue?",
                    "response_summary": "Management provided a quarterly conversion schedule.",
                    "response_state": "answered",
                },
                {
                    "theme_id": "margin_bridge",
                    "analyst_organization": "Research Firm B",
                    "question": "What drives the expected margin improvement?",
                    "response_summary": "Management identified mix and utilization drivers.",
                    "response_state": "answered",
                },
            ],
            "numeric_guidance": [
                {
                    "guidance_id": "quarterly_revenue",
                    "metric": "Quarterly revenue",
                    "period": "Prior-quarter forward guidance",
                    "unit": "usd_millions",
                    "low": 8500,
                    "high": 9500,
                },
                {
                    "guidance_id": "quarterly_eps",
                    "metric": "Quarterly EPS",
                    "period": "Prior-quarter forward guidance",
                    "unit": "per_share_usd",
                    "low": 8.0,
                    "high": 10.0,
                },
            ],
        },
        "current_call": {
            "period": "Current quarter",
            "call_date": current_date.isoformat(),
            "source_ids": [current_call_source],
            "statements": [
                {
                    "topic_id": "demand",
                    "statement_type": "demand",
                    "speaker": "Chief Executive Officer",
                    "text": "Demand is exceptionally strong.",
                    "confidence_level": 4,
                    "horizon": "next four quarters",
                },
                {
                    "topic_id": "capacity_constraint",
                    "statement_type": "capacity",
                    "speaker": "Chief Executive Officer",
                    "text": "Capacity constraints are expected through 2027.",
                    "confidence_level": 4,
                    "horizon": "through 2027",
                },
                {
                    "topic_id": "advanced_packaging",
                    "statement_type": "operations",
                    "speaker": "Chief Executive Officer",
                    "text": "Advanced packaging demand is broadening across customers.",
                    "confidence_level": 3,
                    "horizon": "next fiscal year",
                },
            ],
            "analyst_questions": [
                {
                    "theme_id": "backlog_conversion",
                    "analyst_organization": "Research Firm A",
                    "question": "Has the backlog conversion schedule changed?",
                    "response_summary": "Management reaffirmed the schedule with updated timing.",
                    "response_state": "answered",
                },
                {
                    "theme_id": "growth_capex_returns",
                    "analyst_organization": "Research Firm C",
                    "question": "What return should investors expect on the new capacity spend?",
                    "response_summary": "Management discussed strategic need without quantifying returns.",
                    "response_state": "evaded",
                },
            ],
            "numeric_guidance": [
                {
                    "guidance_id": "quarterly_revenue",
                    "metric": "Quarterly revenue",
                    "period": "Current-quarter forward guidance",
                    "unit": "usd_millions",
                    "low": 8800,
                    "high": 9800,
                },
                {
                    "guidance_id": "quarterly_eps",
                    "metric": "Quarterly EPS",
                    "period": "Current-quarter forward guidance",
                    "unit": "per_share_usd",
                    "low": 8.5,
                    "high": 9.5,
                },
            ],
        },
        "guidance_history": history_rows,
        "prior_commitments": [
            {
                "commitment_id": "capacity_expansion",
                "made_period": "Prior year",
                "due_period": "Current quarter",
                "statement": "Bring the first phase of capacity online by the current quarter.",
                "status": "met",
                "evidence": "The current filing reports the first phase in production.",
                "source_ids": [prior_call_source, primary_source],
            },
            {
                "commitment_id": "margin_target",
                "made_period": "Prior year",
                "due_period": "Current quarter",
                "statement": "Reach the stated gross-margin target by the current quarter.",
                "status": "missed",
                "evidence": "Reported gross margin remained below the stated target.",
                "source_ids": [prior_call_source, primary_source],
            },
            {
                "commitment_id": "packaging_milestone",
                "made_period": "Prior quarter",
                "due_period": "Next fiscal year",
                "statement": "Qualify the next advanced-packaging platform next fiscal year.",
                "status": "pending",
                "evidence": "The due period has not yet elapsed.",
                "source_ids": [current_call_source],
            },
        ],
        "methodology": "Diff stable topic and question identifiers across consecutive calls, calculate guidance range changes, and calibrate management language with eight quarters of guidance versus actual outcomes.",
        "source_ids": [prior_call_source, current_call_source, primary_source],
    }
