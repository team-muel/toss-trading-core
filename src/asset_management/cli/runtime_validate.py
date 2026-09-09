"""Fail-closed validation entry point for the canonical application runtime."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from asset_management.orchestration.runtime import ApplicationRuntime
from asset_management.time.clock import FrozenClock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the canonical READ_ONLY asset-management runtime."
    )
    parser.add_argument("--config", type=Path, default=Path("config/application.yaml"))
    parser.add_argument(
        "--policy-registry", type=Path, default=Path("config/policy_registry.yaml")
    )
    parser.add_argument("--schema-root", type=Path, default=Path("schemas"))
    parser.add_argument(
        "--as-of",
        default="2026-09-04T00:00:00+00:00",
        help="UTC validation instant; must be ISO-8601 and is not wall-clock time.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        instant = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit(f"invalid --as-of: {error}") from error
    if instant.tzinfo is None:
        raise SystemExit("--as-of must include a UTC offset")
    clock = FrozenClock(instant.astimezone(timezone.utc))
    with sqlite3.connect(":memory:") as conn:
        runtime = ApplicationRuntime.boot(
            config_path=args.config,
            policy_registry_path=args.policy_registry,
            schema_root=args.schema_root,
            conn=conn,
            clock=clock,
        )
    if runtime.config.runtime_mode != "READ_ONLY":
        raise SystemExit("canonical runtime validation only permits READ_ONLY mode")
    print("asset_management_runtime=ok")
    print(f"runtime_mode={runtime.config.runtime_mode}")
    print(f"migration_versions={','.join(map(str, runtime.migration_versions))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
