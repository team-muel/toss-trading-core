from pathlib import Path
import unittest


STABLE_CLOUD_MONITORING_EVENTS = [
    "foundation_runner_start",
    "foundation_runner_ok",
    "foundation_runner_failed",
    "foundation_snapshot_start",
    "foundation_snapshot_ok",
    "foundation_snapshot_failed",
    "foundation_audit_start",
    "foundation_audit_ok",
    "foundation_audit_failed",
    "gcp_secret_environment_loaded",
]


class GcpRunnerFilesTest(unittest.TestCase):
    def test_required_gcp_runner_files_exist(self):
        for path in [
            "docs/14_gcp_static_ip_runner.md",
            "docs/15_gcp_secret_manager_and_runtime.md",
            "docs/16_cloud_monitoring_runner_health.md",
            "scripts/load_gcp_secrets.sh",
            "scripts/run_foundation_gcp.sh",
        ]:
            self.assertTrue(Path(path).exists(), path)

    def test_runner_sources_secret_loader_and_writes_jsonl(self):
        runner = Path("scripts/run_foundation_gcp.sh").read_text(encoding="utf-8")

        self.assertIn("source \"scripts/load_gcp_secrets.sh\"", runner)
        self.assertIn("FOUNDATION_JSON_LOG_PATH", runner)
        self.assertIn("FOUNDATION_BACKUP_DIR", runner)
        self.assertIn("FOUNDATION_LOCK_PATH", runner)
        self.assertIn("FOUNDATION_MAX_ORDER_DETAILS", runner)
        self.assertIn("--max-order-details", runner)
        self.assertIn("command -v flock", runner)
        self.assertIn("exec 9>\"${LOCK_PATH}\"", runner)
        self.assertIn("flock -n 9", runner)
        self.assertIn("foundation_runner_lock_busy", runner)
        self.assertIn("sqlite3.connect(source_path)", runner)
        self.assertIn("source.backup(target)", runner)
        self.assertIn("FOUNDATION_GCS_BACKUP_URI", runner)
        self.assertIn("gcloud storage cp", runner)
        self.assertIn("foundation_runner_backup_ok", runner)
        self.assertIn("foundation_runner_backup_upload_ok", runner)
        self.assertIn("json_log \"foundation_runner_start\"", runner)
        self.assertIn("json_log \"foundation_runner_ok\"", runner)
        self.assertIn("foundation_runner_failed", runner)
        self.assertIn("trap on_error ERR", runner)
        self.assertIn("date -u", runner)
        self.assertIn("foundation_runner_ok", runner)

    def test_cloud_monitoring_event_contract_is_documented_and_emitted(self):
        docs = Path("docs/16_cloud_monitoring_runner_health.md").read_text(encoding="utf-8")
        runner = Path("scripts/run_foundation_gcp.sh").read_text(encoding="utf-8")
        snapshot = Path("src/toss_trading/cli/foundation_snapshot.py").read_text(
            encoding="utf-8"
        )
        audit = Path("src/toss_trading/cli/foundation_audit.py").read_text(
            encoding="utf-8"
        )
        emitters = "\n".join([runner, snapshot, audit])

        for event in STABLE_CLOUD_MONITORING_EVENTS:
            self.assertIn(event, docs)
            self.assertIn(event, emitters)

    def test_secret_loader_does_not_echo_secret_values(self):
        loader = Path("scripts/load_gcp_secrets.sh").read_text(encoding="utf-8")

        self.assertIn("gcloud secrets versions access", loader)
        self.assertIn("gcloud CLI is required", loader)
        self.assertIn("secret_loader_loaded env=", loader)
        self.assertNotIn("echo \"${value}\"", loader)


if __name__ == "__main__":
    unittest.main()
