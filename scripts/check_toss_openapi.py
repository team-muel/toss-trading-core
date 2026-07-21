"""Compare the configured approved OpenAPI hash with Toss' canonical document."""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

import yaml


def main() -> int:
    policy = yaml.safe_load(Path("config/default_policy.yaml").read_text(encoding="utf-8"))
    runtime = policy["runtime"]
    url = runtime["toss_openapi_schema_hash_source"]
    expected = runtime["toss_openapi_schema_hash"]
    with urllib.request.urlopen(url, timeout=20) as response:
        actual = hashlib.sha256(response.read()).hexdigest()
    if actual != expected:
        print(f"openapi_contract=changed expected={expected} actual={actual}")
        return 1
    print(f"openapi_contract=ok sha256={actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
