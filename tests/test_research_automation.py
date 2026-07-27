import json
import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path

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
            (root / "lake" / "catalog" / "manifests").mkdir(parents=True)
            parquet = (
                root
                / "lake"
                / "silver"
                / "market_bars"
                / "source=toss-openapi"
                / "interval=1d"
                / "adjustment=raw"
                / "year=2026"
                / "part-test.parquet"
            )
            parquet.parent.mkdir(parents=True)
            parquet.write_bytes(b"PAR1test")
            bundle_base = {
                "request": {"symbols": ["SPY"]},
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
            (root / "reports" / "market-bars-qa.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "adjustments": ["raw", "split_adjusted"],
                        "duplicate_rows": 0,
                        "invalid_rows": 0,
                        "coverage_mismatch_rows": 0,
                        "symbols": ["SPY"],
                    }
                ),
                encoding="utf-8",
            )
            (root / "lake" / "catalog" / "manifests" / "one.json").write_text(
                "{}",
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
            for line in checksums.splitlines():
                expected, relative_path = line.split("  ", maxsplit=1)
                self.assertEqual(
                    hashlib.sha256(
                        (root / relative_path).read_bytes()
                    ).hexdigest(),
                    expected,
                    relative_path,
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
            "deploy/storage/research-lifecycle.json",
            "deploy/monitoring-research/log-metrics.yaml",
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
        self.assertIn("TOSS_API_LOCK_PATH", runner)
        self.assertIn("research_validate_bars", runner)
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
        self.assertIn("gcloud storage rsync", runner)
        self.assertIn("RESEARCH_TIINGO_LICENSE_ACCEPTED", runner)
        self.assertIn("RESEARCH_FRED_SERIES_RIGHTS_APPROVED", runner)
        self.assertIn("RESEARCH_SEC_CONTACT_APPROVED", runner)
        self.assertIn('readlink -f "${ROOT_DIR}"', runner)
        self.assertIn('^[0-9a-f]{7,40}$', runner)
        self.assertIn('CODE_REVISION="${RELEASE_REVISION}"', runner)

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

        research_service = Path(
            "deploy/systemd/toss-research-automation@.service"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Environment=PATH=/home/seoje/toss-trading/current/.venv/bin:"
            "/snap/google-cloud-cli/current/bin:",
            research_service,
        )
        self.assertNotIn(":/snap/bin:", research_service)

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


if __name__ == "__main__":
    unittest.main()
