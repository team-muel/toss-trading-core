import pytest

from asset_management.broker.rate_limit import TokenBucket


@pytest.mark.parametrize("value", ["NaN", "Inf", "-Inf", "-1", "invalid"])
def test_invalid_provider_headers_preserve_canonical_bucket(value):
    bucket = TokenBucket(capacity=10, refill_per_second=1, tokens=3)
    bucket.update_from_headers({"X-RateLimit-Limit": value, "X-RateLimit-Remaining": value})
    assert bucket.capacity == 10
    assert bucket.tokens == 3
    assert bucket.retry_after_seconds({"Retry-After": value}) is None


def test_canonical_headers_honor_valid_exhaustion_and_delay():
    bucket = TokenBucket(capacity=10, refill_per_second=1, tokens=3)
    bucket.update_from_headers({"X-RateLimit-Limit": "5", "X-RateLimit-Remaining": "0"})
    assert bucket.capacity == 5
    assert bucket.tokens == 0
    assert bucket.retry_after_seconds({"Retry-After": "2"}) == 2
