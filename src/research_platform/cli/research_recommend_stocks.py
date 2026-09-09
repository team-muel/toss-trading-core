from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_platform.stock_recommendations import (
    generate_stock_recommendations,
    load_recommendation_policy,
)
from research_platform.variant_perception import (
    load_focused_research_dossiers,
    load_focused_research_policy,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate broad screening candidates and focused-research-gated buy "
            "recommendations from US stock bars."
        )
    )
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--policy", default="config/stock_recommendation_policy.json")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--focused-research-policy",
        default="config/focused_research_policy.json",
    )
    parser.add_argument(
        "--focus-dossier",
        action="append",
        default=[],
        help="Validated focused-research dossier; repeat for multiple symbols.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """The old standalone application entry point is intentionally retired."""
    raise SystemExit("Standalone research execution/delivery retired by AMA-156; use canonical orchestration.")


if __name__ == "__main__":
    raise SystemExit(main())
