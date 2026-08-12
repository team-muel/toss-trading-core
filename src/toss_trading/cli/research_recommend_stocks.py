from __future__ import annotations

import argparse
import json
from pathlib import Path

from toss_trading.research.stock_recommendations import (
    generate_stock_recommendations,
    load_recommendation_policy,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate immutable research-only recommendations from broad US stock bars."
    )
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--policy", default="config/stock_recommendation_policy.json")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    policy = load_recommendation_policy(args.policy)
    if not policy.get("enabled", False):
        raise ValueError(
            f"stock recommendations are gated: {policy.get('activation_gate')}"
        )
    rows = [
        json.loads(line)
        for line in Path(args.input_jsonl).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    payload = generate_stock_recommendations(
        rows,
        policy=policy,
        as_of_date=args.as_of_date,
        code_revision=args.code_revision,
    )
    destination = Path(args.output_dir) / f"{payload['recommendation_id']}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8")
    if destination.exists() and destination.read_bytes() != body:
        raise FileExistsError("immutable stock recommendation conflict")
    if not destination.exists():
        destination.write_bytes(body)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
