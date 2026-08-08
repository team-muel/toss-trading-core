"""Compare the configured approved OpenAPI hash with Toss' canonical document."""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml


def verify_openapi_document(
    policy: dict[str, Any],
    review: dict[str, Any],
    body: bytes,
) -> list[str]:
    runtime = policy["runtime"]
    errors: list[str] = []
    actual_hash = hashlib.sha256(body).hexdigest()
    try:
        document = json.loads(body)
    except json.JSONDecodeError as exc:
        return [f"document is not valid JSON: {exc}"]

    expected_version = str(runtime["toss_openapi_schema_version"])
    actual_version = str(document.get("info", {}).get("version", ""))
    if actual_version != expected_version:
        errors.append(
            f"version expected={expected_version} actual={actual_version or 'missing'}"
        )
    expected_hash = str(runtime["toss_openapi_schema_hash"])
    if actual_hash != expected_hash:
        errors.append(f"sha256 expected={expected_hash} actual={actual_hash}")
    if review.get("approved_version") != expected_version:
        errors.append("review approved_version differs from policy")
    if review.get("approved_sha256") != expected_hash:
        errors.append("review approved_sha256 differs from policy")
    if review.get("source") != runtime["toss_openapi_schema_hash_source"]:
        errors.append("review source differs from policy")

    paths = document.get("paths")
    if not isinstance(paths, dict):
        errors.append("document paths are missing")
        return errors
    for section in ("required_operations", "documented_but_disabled_operations"):
        operations = review.get(section)
        if not isinstance(operations, dict):
            errors.append(f"review {section} is missing")
            continue
        for path, methods in sorted(operations.items()):
            actual_methods = paths.get(path)
            if not isinstance(actual_methods, dict):
                errors.append(f"missing path {path}")
                continue
            for method in methods:
                if str(method).lower() not in actual_methods:
                    errors.append(f"missing operation {str(method).upper()} {path}")

    capabilities = runtime.get("broker_capabilities", {})
    for name in (
        "standard_order_entry",
        "conditional_order_entry",
        "conditional_order_modify",
    ):
        capability = capabilities.get(name)
        if not isinstance(capability, dict) or capability.get("enabled") is not False:
            errors.append(f"write capability must remain disabled: {name}")
    return errors


def main() -> int:
    policy = yaml.safe_load(Path("config/default_policy.yaml").read_text(encoding="utf-8"))
    runtime = policy["runtime"]
    url = runtime["toss_openapi_schema_hash_source"]
    review = json.loads(
        Path(runtime["toss_openapi_contract_review"]).read_text(encoding="utf-8")
    )
    with urllib.request.urlopen(url, timeout=20) as response:
        body = response.read()
    errors = verify_openapi_document(policy, review, body)
    if errors:
        print("openapi_contract=changed " + "; ".join(errors))
        return 1
    print(
        "openapi_contract=ok "
        f"version={runtime['toss_openapi_schema_version']} "
        f"sha256={hashlib.sha256(body).hexdigest()} "
        "conditional_order_entry=disabled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
