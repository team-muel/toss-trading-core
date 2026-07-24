from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from toss_trading.resources import resolve_resource


def load_policy(path: str | Path = "config/default_policy.yaml") -> tuple[dict[str, Any], str]:
    payload = resolve_resource(path).read_bytes()
    policy = yaml.safe_load(payload)
    if not isinstance(policy, dict):
        raise ValueError("policy must be a YAML object")
    runtime = policy.get("runtime")
    guardrails = policy.get("starter_guardrails")
    if not isinstance(runtime, dict) or not isinstance(guardrails, dict):
        raise ValueError("policy requires runtime and starter_guardrails sections")
    if runtime.get("live_trading_enabled") is not False:
        raise ValueError("this foundation only accepts live_trading_enabled=false")
    required = (
        "max_open_orders",
        "block_new_orders_on_any_reconciliation_diff",
        "block_new_orders_after_any_unknown_order_state",
        "require_same_run_reconciliation",
        "require_normal_source_health",
        "require_rate_limit_healthy",
    )
    missing = [key for key in required if key not in guardrails]
    if missing:
        raise ValueError(f"policy missing required guardrails: {missing}")
    return policy, hashlib.sha256(payload).hexdigest()
