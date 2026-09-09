import json
import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from research_platform.cli.research_validate_bars import validate_parquet
from research_platform.cli.research_reporting import _read_summary, main as reporting_main
from research_platform import DataLake, MarketBar
from research_platform.automation import (
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
            # Offline interpretation remains testable without retaining the
            # old Vertex/Gmail application driver as a runnable command.
            from research_platform.interpretation import build_research_evidence, deterministic_interpretation, save_interpretation
            interpretation_path = root / "runtime-interpretation.json"
            evidence = build_research_evidence(summary)
            interpretation = deterministic_interpretation(summary, evidence=evidence,
                                                         failure_reason="StandaloneRuntimeRetired")
            save_interpretation(interpretation, interpretation_path)
            saved = json.loads(interpretation_path.read_text())
            self.assertEqual(saved["current_run_id"], root.name)
            self.assertTrue(saved["evidence_digest"])
            with self.assertRaisesRegex(SystemExit, "retired"):
                reporting_main(["interpret", "--summary", str(summary_path), "--output", str(interpretation_path)])
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

    def test_standalone_gcp_runtime_is_retired_not_reprovisioned(self):
        import subprocess
        import tomllib
        for name in ("install_research_automation_vm.sh", "provision_research_automation_gcp.sh",
                     "run_research_automation_gcp.sh", "run_stock_recommendations_gcp.sh",
                     "bootstrap_research_vm.sh", "prune_research_runtime.sh",
                     "run_toss_history_collection_gcp.sh", "audit_active_research_release.sh",
                     "check_research_identity_gcp.sh"):
            result = subprocess.run(["bash", str(Path("scripts")/name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 78, name)
            self.assertIn("retired", result.stderr)
        units = list(Path("deploy/systemd").glob("*.service")) + list(Path("deploy/systemd").glob("*.timer"))
        self.assertFalse(units)
        entrypoints = tomllib.loads(Path("pyproject.toml").read_text())["project"]["scripts"]
        for name in ("toss-research-automation", "toss-research-reporting",
                     "toss-research-plan-hypotheses", "toss-research-recommend-stocks"):
            self.assertNotIn(name, entrypoints)
        self.assertIn("toss-runtime-validate", entrypoints)
        self.assertTrue(Path("cloudbuild.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
