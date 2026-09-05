"""Redaction boundary for broker payloads."""

SENSITIVE_KEYS = frozenset({
    "access_token", "refresh_token", "id_token", "client_secret", "authorization",
    "accountno", "accountnumber", "account_no", "accountseq", "account_seq",
    "accountid", "account_id", "customerid", "customer_id", "residentnumber", "phone", "email",
})


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {key: ("***REDACTED***" if key.lower() in SENSITIVE_KEYS else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def sanitized_headers(headers: dict[str, str]) -> dict[str, str]:
    """Retain operational headers while removing credentials and cookies entirely."""

    blocked = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}
    return {key: item for key, item in headers.items() if key.lower() not in blocked}
