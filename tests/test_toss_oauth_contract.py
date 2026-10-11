from unittest.mock import patch

import pytest
from toss_trading.broker.credentials import TossCredentials
from toss_trading.broker.toss import TossApiResult, TossReadOnlyAdapter


def adapter():
    result = TossReadOnlyAdapter(TossCredentials("https://example.invalid", "fixture-client", "fixture-secret", None))
    result._access_token = "previous-token"
    result._access_token_expires_at = 1000
    return result


@pytest.mark.parametrize("field,value", [
    ("access_token", True), ("access_token", 123), ("access_token", ""),
    ("access_token", "token\r\nHeader"), ("token_type", "Basic"), ("token_type", None),
    ("expires_in", True), ("expires_in", "86400"), ("expires_in", 1.5),
    ("expires_in", 0), ("expires_in", -1), ("expires_in", 2**63),
])
def test_invalid_oauth_response_clears_cache_and_leaks_no_token(field, value):
    client = adapter()
    body = {"access_token": "synthetic-token", "token_type": "Bearer", "expires_in": 86400}
    body[field] = value
    with patch.object(client, "_send", return_value=TossApiResult("/oauth2/token", 200, body, "")):
        with pytest.raises(RuntimeError, match="violates OAuth contract") as error:
            client.refresh_token()
    assert "synthetic-token" not in str(error.value)
    assert client._access_token is None
    assert client._access_token_expires_at == 0


def test_refresh_transport_failure_clears_previous_token():
    client = adapter()
    with patch.object(client, "_send", side_effect=RuntimeError("transport failure")):
        with pytest.raises(RuntimeError):
            client.refresh_token()
    assert client._access_token is None


def test_valid_oauth_contract_preserves_expiry_margin():
    client = adapter()
    body = {"access_token": "synthetic-token", "token_type": "Bearer", "expires_in": 86400}
    with patch.object(client, "_send", return_value=TossApiResult("/oauth2/token", 200, body, "")), patch("toss_trading.broker.toss.time.monotonic", return_value=100):
        client.refresh_token()
    assert client._access_token == "synthetic-token"
    assert client._access_token_expires_at == 86470
