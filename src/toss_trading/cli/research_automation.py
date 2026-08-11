from __future__ import annotations

import argparse
import json

from toss_trading.research.automation import (
    parse_provider_states,
    resolve_collection_window,
    verify_research_run,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan and verify the GCP research-data automation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    window = subparsers.add_parser("window")
    window.add_argument("--mode", choices=("daily", "weekly"), required=True)
    window.add_argument(
        "--format",
        choices=("json", "fields"),
        default="json",
    )

    verify = subparsers.add_parser("verify")
    verify.add_argument("--run-dir", required=True)
    verify.add_argument("--mode", choices=("daily", "weekly"), required=True)
    verify.add_argument("--code-revision", required=True)
    verify.add_argument("--provider-state", action="append", default=[])
    verify.add_argument("--strategy-experiment")
    verify.add_argument("--hypothesis-plan")
    verify.add_argument("--hypothesis-evaluation")
    verify.add_argument("--tiingo-collection")
    verify.add_argument("--data-source-policy")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "window":
        window = resolve_collection_window(args.mode)
        if args.format == "fields":
            print(
                window.start_date,
                window.through_date,
                window.realtime_start,
                window.realtime_end,
            )
        else:
            print(json.dumps(window.__dict__, sort_keys=True))
        return 0

    status = verify_research_run(
        args.run_dir,
        mode=args.mode,
        code_revision=args.code_revision,
        provider_states=parse_provider_states(args.provider_state),
        strategy_experiment=args.strategy_experiment,
        hypothesis_plan=args.hypothesis_plan,
        hypothesis_evaluation=args.hypothesis_evaluation,
        tiingo_collection=args.tiingo_collection,
        data_source_policy=args.data_source_policy,
    )
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
