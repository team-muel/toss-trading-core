from __future__ import annotations

import argparse
from pathlib import Path

from toss_trading.account.replay import replay_foundation_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay one complete Foundation run from stored raw broker responses.",
    )
    parser.add_argument("--source-db", required=True, help="Source Foundation SQLite database.")
    parser.add_argument(
        "--destination-db",
        required=True,
        help="New SQLite path that does not already exist.",
    )
    parser.add_argument("--source-run-id", default=None, help="Defaults to latest complete run.")
    parser.add_argument(
        "--instrument-master",
        default="data/instrument_master.csv",
        help="Instrument master CSV loaded into the replay database.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = replay_foundation_run(
            source_db_path=args.source_db,
            destination_db_path=args.destination_db,
            source_run_id=args.source_run_id,
            instrument_master_path=args.instrument_master,
        )
    except Exception as exc:
        print("foundation_replay=failed")
        print(f"reason={exc}")
        return 1
    print(result.as_text())
    print(f"destination_db={Path(args.destination_db)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
