from __future__ import annotations

import argparse
import json
from pathlib import Path

from toss_trading.research.variant_perception import (
    build_focused_research_dossier,
    load_focused_research_policy,
    render_focused_research_memo,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and freeze an institutional Variant Perception dossier."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--policy", default="config/focused_research_policy.json"
    )
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    dossier = build_focused_research_dossier(
        payload,
        policy=load_focused_research_policy(args.policy),
        code_revision=args.code_revision,
    )
    destination = Path(args.output_dir) / f"{dossier['dossier_id']}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(dossier, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    if destination.exists() and destination.read_bytes() != body:
        raise FileExistsError("immutable focused research dossier conflict")
    if not destination.exists():
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_bytes(body)
        temporary.replace(destination)
    memo_destination = destination.with_suffix(".md")
    memo_body = render_focused_research_memo(dossier).encode("utf-8")
    if memo_destination.exists() and memo_destination.read_bytes() != memo_body:
        raise FileExistsError("immutable focused research memo conflict")
    if not memo_destination.exists():
        temporary = memo_destination.with_suffix(".md.tmp")
        temporary.write_bytes(memo_body)
        temporary.replace(memo_destination)
    print(json.dumps(dossier, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
