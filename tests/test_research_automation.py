import json
import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from toss_trading.cli.research_validate_bars import validate_parquet
from toss_trading.cli.research_reporting import _read_summary, main as reporting_main
from toss_trading.research import DataLake, MarketBar
from toss_trading.research.automation import (
    parse_provider_states,
    resolve_collection_window,
    verify_research_run,
)


class ResearchAutomationTest(unittest.TestCase):
    def test_daily_and_weekly_windows_are_deterministic(self):
        daily = resolve_collection_window(
            "daily",
            today_utc=date(2026, 7, 25),
        )
        weekly = resolve_collection_window(
            "weekly",
            today_utc=date(2026, 7, 25),
        )

        self.assertEqual(daily.start_date, "2026-06-10")
        self.assertEqual(daily.through_date, "2026-07-24")
        self.assertEqual(daily.realtime_start, "2026-04-26")
        self.assertEqual(weekly.start_date, "2004-01-01")
        self.assertEqual(weekly.realtime_start, "2004-01-01")
        self.assertEqual(daily.realtime_end, "2026-07-24")
        self.assertEqual(weekly.realtime_end, "2026-07-24")

    def test_provider_states_reject_ambiguous_values(self):
        self.assertEqual(
            parse_provider_states(["toss=collected", "tiingo=skipped"]),
            {"tiingo": "skipped", "toss": "collected"},
        )
        with self.assertRaises(ValueError):
            parse_provider_states(["tiingo"])

    def test_verify_run_requires_both_adjustments_and_writes_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            (root / "reports").mkdir()
            bundle_base = {
                "request": {
                    "symbols": ["SPY"],
                    "start_date": "2026-01-01",
                    "interval": "1d",
                    "count": 500,
                    "max_pages": 20,
                },
                "pages": [{"symbol": "SPY"}],
                "failures": [],
            }
            for name, adjusted in (
                ("toss-candles-raw.json", False),
                ("toss-candles-adjusted.json", True),
            ):
                bundle = {
                    **bundle_base,
                    "request": {
                        **bundle_base["request"],
                        "adjusted": adjusted,
                    },
                }
                (root / "input" / name).write_text(
                    json.dumps(bundle),
                    encoding="utf-8",
                )
            lake = DataLake(root / "lake")
            raw_manifest = lake.store_raw(
                source="toss-openapi",
                dataset="candles",
                body={"adjusted": False, "rows": [1]},
                media_type="application/json",
                schema_version="v1",
                available_at="2026-01-02T21:05:00+00:00",
                request={"symbol": "SPY", "adjusted": False},
                license_tag="test-only",
                code_revision="abc123",
                retrieved_at="2026-01-02T21:06:00+00:00",
            )
            adjusted_manifest = lake.store_raw(
                source="toss-openapi",
                dataset="candles",
                body={"adjusted": True, "rows": [1]},
                media_type="application/json",
                schema_version="v1",
                available_at="2026-01-02T21:05:00+00:00",
                request={"symbol": "SPY", "adjusted": True},
                license_tag="test-only",
                code_revision="abc123",
                retrieved_at="2026-01-02T21:06:00+00:00",
            )
            common = {
                "symbol": "SPY",
                "event_time_utc": "2026-01-02T21:00:00+00:00",
                "available_at": "2026-01-02T21:05:00+00:00",
                "exchange_local_date": "2026-01-02",
                "interval": "1d",
                "open": "100",
                "high": "102",
                "low": "99",
                "close": "101",
                "volume": "1000",
                "currency": "USD",
                "session": "regular",
                "source": "toss-openapi",
                "source_revision": "test-v1",
            }
            lake.write_market_bars(
                [
                    MarketBar(
                        **common,
                        adjustment="raw",
                        raw_manifest_id=raw_manifest.manifest_id,
                    ),
                    MarketBar(
                        **common,
                        adjustment="split_adjusted",
                        raw_manifest_id=adjusted_manifest.manifest_id,
                        quality_flag="estimated",
                    ),
                ],
                code_revision="abc123",
                license_tag="test-only",
            )
            qa = validate_parquet(
                [str(path) for path in (root / "lake" / "silver").rglob("*.parquet")],
                required_adjustments={"raw", "split_adjusted"},
            )
            (root / "reports" / "market-bars-qa.json").write_text(
                json.dumps(qa),
                encoding="utf-8",
            )

            result = verify_research_run(
                root,
                mode="daily",
                code_revision="abc123",
                provider_states={"toss": "collected"},
            )

            self.assertTrue(result["ready_for_upload"])
            self.assertEqual(result["schema_version"], "research-automation-run-v2")
            self.assertEqual(result["run_id"], root.name)
            self.assertTrue((root / "run-status.json").is_file())
            self.assertTrue((root / "SHA256SUMS").is_file())
            summary_path = root / "reports" / "reporting-summary.json"
            visual_path = root / "reports" / "visual-report.html"
            self.assertTrue(summary_path.is_file())
            self.assertTrue(visual_path.is_file())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["quality"]["error_rows"], 0)
            self.assertEqual(
                summary["strategy"]["state"],
                "not_available",
            )
            checksums = (root / "SHA256SUMS").read_text(encoding="utf-8")
            self.assertIn("reports/reporting-summary.json", checksums)
            self.assertIn("reports/visual-report.html", checksums)
            self.assertIn("run-status.json", checksums)
            for line in checksums.splitlines():
                expected, relative_path = line.split("  ", maxsplit=1)
                self.assertEqual(
                    hashlib.sha256(
                        (root / relative_path).read_bytes()
                    ).hexdigest(),
                    expected,
                    relative_path,
                )
            interpretation_path = root / "runtime-interpretation.json"
            output = io.StringIO()
            with patch.dict(
                "os.environ",
                {"RESEARCH_INTERPRETATION_ENABLED": "0"},
            ), redirect_stdout(output):
                exit_code = reporting_main(
                    [
                        "interpret",
                        "--summary",
                        str(summary_path),
                        "--output",
                        str(interpretation_path),
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                json.loads(output.getvalue())["source"],
                "deterministic_fallback",
            )
            saved_interpretation = json.loads(
                interpretation_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                saved_interpretation["current_run_id"],
                root.name,
            )
            self.assertTrue(saved_interpretation["evidence_digest"])
            original_summary = summary_path.read_text(encoding="utf-8")
            summary_path.write_text(
                original_summary.replace('"ready_for_upload":true', '"ready_for_upload":false'),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "not upload-ready|modified"):
                _read_summary(summary_path)
            summary_path.write_text(original_summary, encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "already verified"):
                verify_research_run(
                    root,
                    mode="daily",
                    code_revision="abc123",
                    provider_states={"toss": "collected"},
                )

    def test_verify_rejects_placeholder_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            (root / "reports").mkdir()
            manifest_dir = root / "lake" / "catalog" / "manifests"
            manifest_dir.mkdir(parents=True)
            for name, adjusted in (
                ("toss-candles-raw.json", False),
                ("toss-candles-adjusted.json", True),
            ):
                (root / "input" / name).write_text(
                    json.dumps(
                        {
                            "request": {"symbols": ["SPY"], "adjusted": adjusted},
                            "pages": [{"symbol": "SPY"}],
                            "failures": [],
                        }
                    ),
                    encoding="utf-8",
                )
            (root / "reports" / "market-bars-qa.json").write_text(
                json.dumps({"ok": True}),
                encoding="utf-8",
            )
            (manifest_dir / "one.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "lacks required fields"):
                verify_research_run(
                    root,
                    mode="daily",
                    code_revision="abc123",
                    provider_states={"toss": "collected"},
                )

    def test_checked_in_gcp_automation_contract(self):
        required = [
            "cloudbuild.yaml",
            "scripts/run_research_automation_gcp.sh",
            "scripts/provision_research_automation_gcp.sh",
            "scripts/install_research_automation_vm.sh",
            "deploy/systemd/toss-research-automation@.service",
            "deploy/systemd/toss-research-daily.timer",
            "deploy/systemd/toss-research-weekly.timer",
            "deploy/systemd/toss-research-prune.service",
            "deploy/systemd/toss-research-prune.timer",
            "scripts/prune_research_runtime.sh",
            "deploy/storage/research-lifecycle.json",
            "deploy/monitoring-research/log-metrics.yaml",
            "deploy/monitoring-research/research-reporting-upload-failed.yaml",
            "deploy/monitoring-research/research-interpretation-failed.yaml",
            "deploy/monitoring-dashboard/research-visual-report.json",
            "deploy/bigquery/research_run_summary_schema.json",
            "deploy/bigquery/latest_run_summaries.sql",
        ]
        for value in required:
            self.assertTrue(Path(value).is_file(), value)

        runner = Path("scripts/run_research_automation_gcp.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("flock -n 9", runner)
        self.assertIn("flock -n 8", runner)
        self.assertIn("flock -u 8", runner)
        self.assertIn("TOSS_API_LOCK_PATH", runner)
        self.assertIn("research_validate_bars", runner)
        self.assertIn("--require-adjustment total_return", runner)
        self.assertIn("research_backtest", runner)
        self.assertIn("--align-common-history", runner)
        self.assertIn("--validation-protocol", runner)
        self.assertIn("research_plan_hypotheses", runner)
        self.assertIn("autonomous_research_policy.json", runner)
        self.assertIn("research_hypothesis_planning_ok", runner)
        self.assertIn("research_hypothesis_planning_failed", runner)
        self.assertIn("--hypothesis-plan", runner)
        self.assertIn("research_evaluate_hypotheses", runner)
        self.assertIn("research_hypothesis_evaluation_ok", runner)
        self.assertIn("research_hypothesis_evaluation_failed", runner)
        self.assertIn("--hypothesis-evaluation", runner)
        self.assertIn("research_strategy_artifact_ok", runner)
        self.assertIn("research_strategy_promotion_pending", runner)
        self.assertIn("research_strategy_promotion_blocked", runner)
        self.assertIn(
            'STRATEGY_METHODOLOGY_STATE}" == "collecting"',
            runner,
        )
        self.assertIn("research_weekly_automation_ok", runner)
        self.assertIn("research_weekly_stale", runner)
        self.assertIn("strategy-backtest.json", runner)
        self.assertIn("research_automation verify", runner)
        self.assertIn(
            '> "${RUNTIME_ROOT}/last-verification.json"',
            runner,
        )
        self.assertNotIn(
            '> "${REPORT_DIR}/verification.json"',
            runner,
        )
        self.assertIn("research_reporting event", runner)
        self.assertIn("upload-bigquery", runner)
        self.assertIn("research_upload_gcs", runner)
        self.assertIn(
            '--destination-uri "${GCS_URI%/}/runs/${RUN_ID}"',
            runner,
        )
        self.assertIn("last-gcs-upload.json", runner)
        self.assertNotIn("--recursive", runner)
        self.assertNotIn("gcloud storage rsync", runner)
        self.assertNotIn(
            '"${GCS_URI%/}/status/latest-${RUN_MODE}.json"',
            runner,
        )
        self.assertNotIn(
            '"${GCS_URI%/}/reports/latest-${RUN_MODE}.json"',
            runner,
        )
        self.assertNotIn("TOSS_ACCOUNT_SEQ_SECRET", runner)
        self.assertIn("RESEARCH_TIINGO_LICENSE_ACCEPTED", runner)
        self.assertIn("RESEARCH_FRED_SERIES_RIGHTS_APPROVED", runner)
        self.assertIn("RESEARCH_SEC_CONTACT_APPROVED", runner)
        self.assertIn(
            "optional_secret_rejected env=${env_name} "
            "reason=invalid_control_character",
            runner,
        )
        self.assertIn("research_reporting \\", runner)
        self.assertIn("research_email_ok", runner)
        self.assertIn("research_email_failed", runner)
        self.assertIn("research_interpretation_ok", runner)
        self.assertIn("research_interpretation_failed", runner)
        self.assertIn("research_reporting \\\n    interpret", runner)
        self.assertIn("RESEARCH_INTERPRETATION_MODEL", runner)
        self.assertIn("PREVIOUS_SUMMARY_ARGS", runner)
        self.assertIn("GMAIL_OAUTH_REFRESH_TOKEN", runner)
        self.assertIn("RESEARCH_EMAIL_RECIPIENT", runner)
        self.assertIn("last-email-delivery.json", runner)
        self.assertIn('readlink -f "${ROOT_DIR}"', runner)
        self.assertIn('^[0-9a-f]{7,40}$', runner)
        self.assertIn('CODE_REVISION="${RELEASE_REVISION}"', runner)

        provisioner = Path(
            "scripts/provision_research_automation_gcp.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("aiplatform.googleapis.com", provisioner)
        self.assertIn("roles/aiplatform.user", provisioner)

        installer = Path(
            "scripts/install_research_automation_vm.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "deploy/systemd/toss-foundation.service",
            installer,
        )
        self.assertIn(
            "/etc/systemd/system/toss-foundation.service",
            installer,
        )
        self.assertIn(
            "deploy/systemd/toss-foundation.timer",
            installer,
        )
        self.assertIn(
            "/etc/systemd/system/toss-foundation.timer",
            installer,
        )

        research_service = Path(
            "deploy/systemd/toss-research-automation@.service"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Environment=PATH=/home/seoje/toss-trading/current/.venv/bin:"
            "/snap/google-cloud-cli/current/bin:",
            research_service,
        )
        self.assertNotIn(":/snap/bin:", research_service)
        self.assertNotIn("TOSS_ACCOUNT_SEQ_SECRET", research_service)
        self.assertNotIn("TOSS_BROKER_BASE_URL_SECRET", research_service)

        provisioner = Path(
            "scripts/provision_research_automation_gcp.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("roles/storage.objectCreator", provisioner)
        self.assertIn("remove-iam-policy-binding", provisioner)
        self.assertIn("gmail.googleapis.com", provisioner)
        self.assertIn(
            "toss-research-gmail-oauth-refresh-token",
            provisioner,
        )

        cloudbuild = Path("cloudbuild.yaml").read_text(encoding="utf-8")
        self.assertIn(
            "test -f src/toss_trading/runtime/rate_limit.py",
            cloudbuild,
        )
        self.assertIn("apt-get install", cloudbuild)
        self.assertIn("shellcheck", cloudbuild)
        self.assertIn("id: upload-wheel", cloudbuild)
        self.assertIn("gcloud auth print-access-token", cloudbuild)
        self.assertIn("storage.googleapis.com/upload", cloudbuild)
        self.assertIn(
            "name=builds%2F${BUILD_ID}%2F${wheel_name}",
            cloudbuild,
        )
        self.assertIn(
            "serviceAccount: "
            "projects/toss-trading-core-lab/serviceAccounts/"
            "toss-research-build@toss-trading-core-lab."
            "iam.gserviceaccount.com",
            cloudbuild,
        )

        provisioner = Path(
            "scripts/provision_research_automation_gcp.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("BUILD_SERVICE_ACCOUNT_NAME", provisioner)
        self.assertIn("roles/logging.logWriter", provisioner)
        self.assertIn("CLOUD_BUILD_SOURCE_BUCKET", provisioner)
        self.assertIn("roles/storage.objectViewer", provisioner)
        self.assertIn("BuildArtifactsPrefix", provisioner)
        self.assertIn(
            "objects/builds/",
            provisioner,
        )
        self.assertIn("bigquery.googleapis.com", provisioner)
        self.assertIn("roles/bigquery.dataEditor", provisioner)
        self.assertIn("render_research_dashboard.py", provisioner)
        self.assertIn("monitoring dashboards", provisioner)
        self.assertIn("monitoring policies update", provisioner)

        history_runner = Path(
            "scripts/run_toss_history_collection_gcp.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("TOSS_API_LOCK_PATH", history_runner)
        self.assertIn("flock -n 8", history_runner)


if __name__ == "__main__":
    unittest.main()
