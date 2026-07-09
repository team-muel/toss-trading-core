from pathlib import Path
import unittest


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
        self.assertIn("foundation_runner_ok", runner)

    def test_secret_loader_does_not_echo_secret_values(self):
        loader = Path("scripts/load_gcp_secrets.sh").read_text(encoding="utf-8")

        self.assertIn("gcloud secrets versions access", loader)
        self.assertIn("gcloud CLI is required", loader)
        self.assertIn("secret_loader_loaded env=", loader)
        self.assertNotIn("echo \"${value}\"", loader)


if __name__ == "__main__":
    unittest.main()
