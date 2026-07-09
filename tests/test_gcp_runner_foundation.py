import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from toss_trading.broker.toss import TossApiError
from toss_trading.runtime import JsonlLogger, TokenBucket, load_gcp_secret_environment


class GcpRunnerFoundationTest(unittest.TestCase):
    def test_jsonl_logger_writes_structured_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner.jsonl"
            logger = JsonlLogger(path)

            logger.emit("foundation_runner_start", profile="v0-empty-safe")

            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["event"], "foundation_runner_start")
            self.assertEqual(rows[0]["profile"], "v0-empty-safe")
            self.assertIn("ts", rows[0])

    def test_secret_manager_loader_sets_env_without_printing_values(self):
        class FakePayload:
            data = b"secret-value\n"

        class FakeResponse:
            payload = FakePayload()

        class FakeClient:
            def access_secret_version(self, request):
                self.last_request = request
                return FakeResponse()

        fake_secretmanager = types.SimpleNamespace(
            SecretManagerServiceClient=lambda: FakeClient()
        )
        fake_google_cloud = types.SimpleNamespace(secretmanager=fake_secretmanager)

        with patch.dict(
            sys.modules,
            {
                "google": types.SimpleNamespace(cloud=fake_google_cloud),
                "google.cloud": fake_google_cloud,
                "google.cloud.secretmanager": fake_secretmanager,
            },
        ):
            with patch.dict(
                os.environ,
                {
                    "GCP_PROJECT_ID": "project-1",
                    "TOSS_CLIENT_ID_SECRET": "toss-client-id",
                },
                clear=True,
            ):
                result = load_gcp_secret_environment()

                self.assertEqual(os.environ["TOSS_CLIENT_ID"], "secret-value")
                self.assertIn("TOSS_CLIENT_ID", result.loaded_env_names)
                self.assertIn("TOSS_CLIENT_SECRET", result.skipped_env_names)

    def test_toss_api_error_formats_nested_error_without_raw_body_dump(self):
        error = TossApiError(
            endpoint="/api/v1/buying-power",
            status_code=400,
            body={
                "error": {
                    "requestId": "req-1",
                    "code": "invalid-request",
                    "message": "요청 필드가 올바르지 않습니다.",
                    "data": {"field": "currency"},
                }
            },
        )

        text = str(error)

        self.assertIn("Toss API error 400", text)
        self.assertIn("code=invalid-request", text)
        self.assertIn("request_id=req-1", text)
        self.assertIn("field=currency", text)
        self.assertNotIn("{'error'", text)

    def test_token_bucket_updates_from_rate_limit_headers(self):
        bucket = TokenBucket(capacity=20, refill_per_second=10, tokens=10, updated_at=0)

        bucket.update_from_headers(
            {
                "X-RateLimit-Limit": "50",
                "X-RateLimit-Remaining": "7",
            }
        )

        self.assertEqual(bucket.capacity, 50)
        self.assertEqual(bucket.tokens, 7)


if __name__ == "__main__":
    unittest.main()
