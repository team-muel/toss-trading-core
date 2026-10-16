import pytest
from toss_trading.runtime.rate_limit import TokenBucket


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-1", "invalid"])
def test_invalid_headers_do_not_poison_bucket_or_retry_delay(value):
    bucket = TokenBucket(capacity=5, refill_per_second=1, tokens=2)
    bucket.update_from_headers({"X-RateLimit-Limit": value, "X-RateLimit-Remaining": value})
    assert bucket.capacity == 5
    assert bucket.tokens == 2
    assert bucket.retry_after_seconds({"Retry-After": value}) is None


def test_official_exhausted_bucket_headers_and_delay():
    bucket = TokenBucket(capacity=5, refill_per_second=1, tokens=2)
    bucket.update_from_headers({"x-ratelimit-limit": "10", "x-ratelimit-remaining": "0"})
    assert bucket.capacity == 10
    assert bucket.tokens == 0
    assert bucket.retry_after_seconds({"retry-after": "1"}) == 1
