"""Redaction boundary for broker payloads."""

SENSITIVE_KEYS = frozenset({"access_token", "refresh_token", "accountseq", "account_no"})


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {key: ("***REDACTED***" if key.lower() in SENSITIVE_KEYS else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
