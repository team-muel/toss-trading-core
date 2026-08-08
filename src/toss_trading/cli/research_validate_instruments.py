from __future__ import annotations

import argparse
import json
from datetime import date

from toss_trading.data.universe import (
    load_instrument_mappings,
    load_universe,
    validate_universe_mapping,
)
from toss_trading.research.instruments import (
    load_corporate_actions,
    load_instrument_history,
    resolve_provider_symbol,
    validate_instrument_history,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the point-in-time ETF identity registry."
    )
    parser.add_argument("--universe", default="data/universe.csv")
    parser.add_argument("--instrument-master", default="data/instrument_master.csv")
    parser.add_argument("--instrument-history", default="data/instrument_history.csv")
    parser.add_argument("--corporate-actions", default="data/corporate_actions.csv")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument(
        "--provider",
        action="append",
        default=["toss-openapi", "tiingo-eod"],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of = date.fromisoformat(args.as_of).isoformat()
    universe = load_universe(args.universe)
    mappings = load_instrument_mappings(args.instrument_master)
    aliases = load_instrument_history(args.instrument_history)
    actions = load_corporate_actions(args.corporate_actions)
    validate_universe_mapping(universe, mappings)
    validate_instrument_history(mappings, aliases, actions)
    enabled = sorted(item.symbol for item in universe if item.enabled)
    resolutions = {
        provider: {
            symbol: resolve_provider_symbol(
                aliases,
                canonical_symbol=symbol,
                provider=provider,
                as_of=as_of,
            )
            for symbol in enabled
        }
        for provider in dict.fromkeys(args.provider)
    }
    print(
        json.dumps(
            {
                "ok": True,
                "as_of": as_of,
                "enabled_instruments": len(enabled),
                "history_records": len(aliases),
                "corporate_actions": len(actions),
                "provider_symbols": resolutions,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
