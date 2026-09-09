import unittest
from unittest.mock import patch

from asset_management.toss.runtime import TokenBucket


class TokenBucketTest(unittest.TestCase):
    def test_waiting_acquire_rechecks_tokens_under_lock(self):
        bucket = TokenBucket(
            capacity=1,
            refill_per_second=1,
            tokens=0,
            updated_at=0,
        )
        with patch(
            "asset_management.toss.runtime.rate_limit.time.monotonic",
            side_effect=[0, 1],
        ), patch("asset_management.toss.runtime.rate_limit.time.sleep") as sleep:
            waited = bucket.acquire()

        self.assertEqual(waited, 1)
        self.assertEqual(bucket.tokens, 0)
        sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
