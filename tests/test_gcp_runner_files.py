from pathlib import Path
import unittest

import yaml


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
            "deploy/ops-agent/toss-foundation.yaml",
            "deploy/monitoring/foundation-backup-upload-heartbeat.yaml",
            "deploy/monitoring/foundation-runner-lock-busy.yaml",
            "deploy/monitoring/foundation-runner-failed.yaml",
            "deploy/monitoring/foundation-snapshot-failed.yaml",
            "deploy/monitoring/foundation-audit-failed.yaml",
            "deploy/monitoring/foundation-runner-heartbeat.yaml",
            "deploy/monitoring/log-metrics.yaml",
            "deploy/systemd/toss-foundation.service",
            "deploy/systemd/toss-foundation.timer",
        ]:
            self.assertTrue(Path(path).exists(), path)

    def test_runner_sources_secret_loader_and_writes_jsonl(self):
        runner = Path("scripts/run_foundation_gcp.sh").read_text(encoding="utf-8")

        self.assertIn("source \"scripts/load_gcp_secrets.sh\"", runner)
        self.assertIn("FOUNDATION_JSON_LOG_PATH", runner)
        self.assertIn("FOUNDATION_BACKUP_DIR", runner)
        self.assertIn("FOUNDATION_LOCAL_BACKUP_RETENTION_DAYS", runner)
        self.assertIn("FOUNDATION_CODE_REVISION", runner)
        self.assertIn("FOUNDATION_LOCK_PATH", runner)
        self.assertIn("TOSS_API_LOCK_PATH", runner)
        self.assertIn("FOUNDATION_MAX_ORDER_DETAILS", runner)
        self.assertIn("FOUNDATION_INCLUDE_CLOSED_ORDERS", runner)
        self.assertIn("FOUNDATION_TARGET_ORDER_ID", runner)
        self.assertIn("umask 077", runner)
        self.assertIn("--max-order-details", runner)
        self.assertIn("--target-order-id", runner)
        self.assertIn("--include-closed-orders", runner)
        self.assertIn("command -v flock", runner)
        self.assertIn("exec 9>\"${LOCK_PATH}\"", runner)
        self.assertIn("flock -n 9", runner)
        self.assertIn("flock -n 8", runner)
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

    def test_foundation_runner_recovers_revision_from_release_path(self):
        runner = Path("scripts/run_foundation_gcp.sh").read_text(encoding="utf-8")

        self.assertIn('readlink -f "${ROOT_DIR}"', runner)
        self.assertIn('^[0-9a-f]{7,40}$', runner)
        self.assertIn('CODE_REVISION="${RELEASE_REVISION}"', runner)

    def test_systemd_runner_is_read_only_and_hardened(self):
        service = Path("deploy/systemd/toss-foundation.service").read_text(
            encoding="utf-8"
        )
        timer = Path("deploy/systemd/toss-foundation.timer").read_text(
            encoding="utf-8"
        )

        self.assertIn("UMask=0077", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("ReadWritePaths=/home/seoje/toss-trading/runtime", service)
        self.assertIn("FOUNDATION_AUDIT_PROFILE=v0-empty-safe", service)
        self.assertIn("FOUNDATION_LOAD_GCP_SECRETS=1", service)
        self.assertIn(
            "Environment=PATH=/home/seoje/toss-trading/current/.venv/bin:"
            "/snap/google-cloud-cli/current/bin:",
            service,
        )
        self.assertNotIn(":/snap/bin:", service)
        self.assertIn("OnUnitActiveSec=6h", timer)
        self.assertIn("Persistent=true", timer)

    def test_ops_agent_collects_and_parses_foundation_jsonl(self):
        config = Path("deploy/ops-agent/toss-foundation.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "/home/seoje/toss-trading/runtime/foundation_runner.jsonl", config
        )
        self.assertIn("type: parse_json", config)
        self.assertIn("toss_foundation_pipeline", config)

    def test_additional_monitoring_policies_cover_backup_and_overlap(self):
        backup_policy = Path(
            "deploy/monitoring/foundation-backup-upload-heartbeat.yaml"
        ).read_text(encoding="utf-8")
        lock_policy = Path(
            "deploy/monitoring/foundation-runner-lock-busy.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("foundation_runner_backup_upload_ok_count", backup_policy)
        self.assertIn("conditionAbsent", backup_policy)
        self.assertIn("foundation_runner_lock_busy_count", lock_policy)
        self.assertIn("conditionThreshold", lock_policy)

    def test_deployment_yaml_is_parseable(self):
        paths = [
            "deploy/ops-agent/toss-foundation.yaml",
            "deploy/monitoring/foundation-backup-upload-heartbeat.yaml",
            "deploy/monitoring/foundation-runner-lock-busy.yaml",
            "deploy/monitoring/foundation-runner-failed.yaml",
            "deploy/monitoring/foundation-snapshot-failed.yaml",
            "deploy/monitoring/foundation-audit-failed.yaml",
            "deploy/monitoring/foundation-runner-heartbeat.yaml",
            "deploy/monitoring/log-metrics.yaml",
        ]
        for path in paths:
            payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
            self.assertIsInstance(payload, dict, path)

        policy_paths = [
            path
            for path in paths
            if path.startswith("deploy/monitoring/foundation-")
        ]
        self.assertEqual(len(policy_paths), 6)
        for path in policy_paths:
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn("__INSTANCE_ID__", text)
            self.assertIn("__NOTIFICATION_CHANNEL__", text)

        metrics = yaml.safe_load(
            Path("deploy/monitoring/log-metrics.yaml").read_text(encoding="utf-8")
        )["metrics"]
        self.assertEqual(len(metrics), 10)
        self.assertEqual(
            {item["event"] for item in metrics},
            (
                set(STABLE_CLOUD_MONITORING_EVENTS)
                | {
                    "foundation_runner_backup_ok",
                    "foundation_runner_backup_upload_ok",
                    "foundation_runner_lock_busy",
                }
            )
            - {
                "foundation_runner_start",
                "foundation_snapshot_start",
                "foundation_audit_start",
            },
        )


if __name__ == "__main__":
    unittest.main()
