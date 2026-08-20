from datetime import date, timedelta


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
