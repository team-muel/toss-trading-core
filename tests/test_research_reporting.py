import json
import hashlib
import tempfile
import unittest
import uuid
from pathlib import Path

from scripts.render_bigquery_reporting import render_sql
from scripts.render_research_dashboard import render_dashboard
from scripts.render_research_log_metrics import render_log_metrics
from scripts.render_research_monitoring import render as render_monitoring_policies
from toss_trading.research.reporting import (
    STRATEGY_METRIC_KEYS,
    build_monitoring_event,
    build_research_summary,
    render_visual_report,
    summary_to_bigquery_row,
)


def sample_summary(
    *,
    experiment: Path | None = None,
    collection_failed: bool = False,
) -> dict:
    return build_research_summary(
        run_id="daily-20260727T000000Z-abc123",
        verified_at="2026-07-27T00:00:00+00:00",
        mode="daily",
        code_revision="abc123",
        provider_states={
            "toss": "collected",
            "tiingo": "skipped_license_or_secret_gate",
        },
        toss={
            "symbols_requested": 3,
            "raw_pages": 3,
            "adjusted_pages": 3,
            "raw_failures": [{"symbol": "QQQ"}] if collection_failed else [],
            "adjusted_failures": (
                [{"symbol": "QQQ"}] if collection_failed else []
            ),
        },
        quality={
            "adjustments": ["raw", "split_adjusted"],
            "duplicate_rows": 0,
            "invalid_rows": 0,
            "coverage_mismatch_rows": 0,
            "symbols": ["SPY", "TLT", "SGOV"],
        },
        artifacts={
            "source_files": 10,
            "source_bytes": 2048,
            "manifests": 2,
            "parquet_files": 2,
        },
        strategy_experiment=experiment,
    )


class ResearchReportingTest(unittest.TestCase):
    def test_unavailable_strategy_is_null_not_fake_zero(self):
        summary = sample_summary()
        row = summary_to_bigquery_row(
            summary,
            ingested_at="2026-07-27T00:01:00+00:00",
        )
        event = build_monitoring_event(summary)

        self.assertEqual(summary["strategy"]["state"], "not_available")
        self.assertIsNone(row["strategy_total_return"])
        self.assertNotIn("strategy_total_return", event)
        self.assertEqual(event["quality_error_rows"], 0)
        self.assertEqual(event["toss_collection_failure_count"], 0)
        report = render_visual_report(summary)
        self.assertIn("미측정", report)
        self.assertIn("Toss Trading 통합 보고서", report)

    def test_collection_failure_forces_visual_review(self):
        summary = sample_summary(collection_failed=True)
        event = build_monitoring_event(summary)
        report = render_visual_report(summary)

        self.assertEqual(event["toss_collection_failure_count"], 2)
        self.assertIn("부분 성공", report)
        self.assertIn("수집 실패 요청 2", report)
        self.assertIn("REVIEW", report)

    def test_verified_experiment_populates_strategy_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            metrics = {
                "total_return": 0.1,
                "cagr": 0.08,
                "annualized_volatility": 0.12,
                "sharpe_zero_rate": 0.7,
                "max_drawdown": -0.05,
                "calmar": 1.6,
                "turnover": 0.4,
                "trading_days": 252.0,
            }
            payload = {
                "strategy": "broad_etf_dual_momentum_v1",
                "input_adjustment": "total_return",
                "code_revision": "abc123",
                "data_manifest_ids": ["manifest-1"],
                "config": {},
                "benchmark_names": ["SPY buy-and-hold"],
                "metrics": metrics,
                "rebalances": [],
                "equity_curve": [["2026-01-01", 1.0]],
            }
            canonical = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest = hashlib.sha256(canonical).hexdigest()
            experiment_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"experiment:{digest}")
            )
            path = Path(tmp) / f"{experiment_id}.json"
            path.write_bytes(canonical)

            summary = sample_summary(experiment=path)
            row = summary_to_bigquery_row(summary)
            event = build_monitoring_event(summary)

            self.assertEqual(summary["strategy"]["state"], "available")
            self.assertEqual(row["strategy_total_return"], 0.1)
            self.assertEqual(event["strategy_total_return"], 0.1)
            self.assertEqual(
                event["strategy_name"],
                "broad_etf_dual_momentum_v1",
            )

    def test_fabricated_strategy_experiment_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fabricated.json"
            path.write_text(
                json.dumps(
                    {
                        "strategy": "fabricated",
                        "metrics": {
                            key: 0.1 for key in STRATEGY_METRIC_KEYS
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "code revision"):
                sample_summary(experiment=path)

    def test_reporting_infrastructure_templates_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metric_paths = render_log_metrics(
                source=Path("deploy/monitoring-research/log-metrics.yaml"),
                output_dir=root / "metrics",
            )
            policy_paths = render_monitoring_policies(
                source_dir=Path("deploy/monitoring-research"),
                output_dir=root / "policies",
                instance_id="654321",
                notification_channel=(
                    "projects/project-1/notificationChannels/channel-1"
                ),
            )
            dashboard = render_dashboard(
                source=Path(
                    "deploy/monitoring-dashboard/research-visual-report.json"
                ),
                output=root / "dashboard.json",
                project_id="project-1",
                project_number="123456",
                instance_id="654321",
                dataset_id="reporting",
            )
            sql = render_sql(
                source=Path(
                    "deploy/bigquery/latest_run_summaries.sql"
                ),
                output=root / "view.sql",
                project_id="project-1",
                dataset_id="reporting",
                table_id="runs",
            )

            self.assertEqual(len(metric_paths), 18)
            self.assertEqual(len(policy_paths), 6)
            failure_metric = json.loads(
                (
                    root
                    / "metrics"
                    / "research_toss_collection_failure_count.json"
                ).read_text(encoding="utf-8")
            )
            self.assertIn(
                "jsonPayload.toss_collection_failure_count",
                failure_metric["valueExtractor"],
            )
            strategy_metric = json.loads(
                (
                    root / "metrics" / "research_strategy_total_return.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                strategy_metric["metricDescriptor"]["valueType"],
                "DISTRIBUTION",
            )
            self.assertIn(
                'jsonPayload.strategy_state="available"',
                strategy_metric["filter"],
            )
            self.assertEqual(
                dashboard["displayName"],
                "Toss Trading - Operations, Data Quality, Strategy",
            )
            dashboard_json = json.dumps(dashboard)
            self.assertNotIn("ALIGN_PERCENTILE", dashboard_json)
            self.assertIn("ALIGN_SUM", dashboard_json)
            self.assertNotIn("__INSTANCE_ID__", json.dumps(dashboard))
            self.assertIn("project-1.reporting.runs", sql)


if __name__ == "__main__":
    unittest.main()
