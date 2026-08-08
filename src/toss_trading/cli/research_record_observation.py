from __future__ import annotations

import argparse
import json
import os

from toss_trading.research.prospective import append_run_completion
from toss_trading.research.providers import utc_now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Commit prospective collection evidence after a successful run."
    )
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION", "unknown"),
    )
    parser.add_argument("--completed-at", default=None)
    args = parser.parse_args(argv)
    record = append_run_completion(
        args.ledger,
        run_id=args.run_id,
        code_revision=args.code_revision,
        completed_at=args.completed_at or utc_now(),
    )
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
