from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from toss_trading.research.hypotheses import (
    HypothesisLedger,
    VertexHypothesisPlanner,
    hypothesis_from_proposal,
    load_research_policy,
    structural_novelty,
)


def _universe_symbols(path: str | Path) -> list[str]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    symbols = sorted(
        {
            str(row.get("symbol") or "").strip().upper()
            for row in rows
            if str(row.get("symbol") or "").strip()
        }
    )
    if not symbols:
        raise ValueError("research universe contains no symbols")
    return symbols


def plan_hypotheses(
    *,
    policy_path: str | Path,
    universe_path: str | Path,
    ledger_dir: str | Path,
    output_dir: str | Path,
    project_id: str,
    location: str,
    model: str,
    planner: VertexHypothesisPlanner | None = None,
    now: datetime | None = None,
    max_new: int | None = None,
) -> dict[str, Any]:
    policy = load_research_policy(policy_path)
    if policy.get("enabled") is not True:
        return {"state": "disabled", "created": [], "reused": []}
    ledger = HypothesisLedger(ledger_dir)
    registered = ledger.registered()
    current_time = now or datetime.now(timezone.utc)
    current_week = current_time.isocalendar()[:2]
    registered_this_week = 0
    for item in registered:
        value = item.get("registered_at")
        if not isinstance(value, str):
            continue
        try:
            registered_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if registered_time.isocalendar()[:2] == current_week:
            registered_this_week += 1
    weekly_remaining = max(
        0,
        int(policy["max_new_hypotheses_per_week"]) - registered_this_week,
    )
    if max_new is not None:
        if max_new < 0:
            raise ValueError("max_new cannot be negative")
        weekly_remaining = min(weekly_remaining, max_new)
    if max_new == 0:
        return {
            "state": "cadence_audit",
            "created": [],
            "reused": [],
            "registered_count": len(registered),
            "registered_this_week": registered_this_week,
            "model": model,
        }
    if weekly_remaining == 0:
        return {
            "state": "weekly_limit_reached",
            "created": [],
            "reused": [],
            "registered_count": len(registered),
            "registered_this_week": registered_this_week,
            "model": model,
        }
    limit = int(policy["max_registered_hypotheses"])
    if len(registered) >= limit:
        return {
            "state": "capacity_reached",
            "created": [],
            "reused": [],
            "registered_count": len(registered),
        }
    planner = planner or VertexHypothesisPlanner(
        project_id=project_id,
        location=location,
        model=model,
    )
    planning_policy = {
        **policy,
        "max_new_hypotheses_per_week": weekly_remaining,
    }
    rotation = list(policy["family_rotation"])
    family_counts = {
        family: sum(
            1 for item in registered if item.get("strategy_family") == family
        )
        for family in policy["strategy_families"]
    }
    target_families = sorted(
        policy["strategy_families"],
        key=lambda family: (family_counts[family], rotation.index(family)),
    )[: max(1, min(weekly_remaining, len(rotation)))]
    planning_policy["target_strategy_families"] = target_families
    proposals = planner.propose(
        policy=planning_policy,
        registered=registered,
        available_symbols=_universe_symbols(universe_path),
    )
    known = {str(item["hypothesis_id"]): item for item in registered}
    created: list[str] = []
    reused: list[str] = []
    rejected_near_duplicates: list[dict[str, Any]] = []
    rejected_invalid: list[dict[str, str]] = []
    created_families: dict[str, str] = {}
    registered_at = current_time.isoformat()
    for proposal in proposals:
        try:
            hypothesis = hypothesis_from_proposal(
                proposal,
                policy=policy,
                model=model,
                registered_at=registered_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            rejected_invalid.append(
                {
                    "strategy_family": str(
                        proposal.get("strategy_family", "unknown")
                        if isinstance(proposal, dict)
                        else "unknown"
                    ),
                    "reason_type": type(exc).__name__,
                }
            )
            continue
        if hypothesis.hypothesis_id in known:
            if hypothesis.hypothesis_id not in reused:
                reused.append(hypothesis.hypothesis_id)
            continue
        if (
            hypothesis.strategy_family != str(policy.get("strategy_family"))
            and hypothesis.strategy_family not in target_families
        ):
            continue
        novelty, nearest_id = structural_novelty(
            hypothesis,
            list(known.values()),
        )
        if novelty < float(policy["minimum_structural_novelty"]):
            rejected_near_duplicates.append(
                {
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "strategy_family": hypothesis.strategy_family,
                    "nearest_hypothesis_id": nearest_id,
                    "structural_novelty": novelty,
                }
            )
            continue
        if len(known) + len(created) >= limit:
            break
        ledger.register(hypothesis, output_dir=output_dir)
        created.append(hypothesis.hypothesis_id)
        created_families[hypothesis.hypothesis_id] = hypothesis.strategy_family
        known[hypothesis.hypothesis_id] = hypothesis.to_dict()
    return {
        "state": "completed",
        "created": created,
        "reused": reused,
        "registered_count": len(registered) + len(created),
        "registered_this_week": registered_this_week + len(created),
        "model": model,
        "target_strategy_families": target_families,
        "family_counts_before": family_counts,
        "created_families": created_families,
        "rejected_near_duplicates": rejected_near_duplicates,
        "rejected_invalid": rejected_invalid,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ask Vertex AI for policy-bounded, non-executable strategy hypotheses."
    )
    parser.add_argument("--policy", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--ledger-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--project-id", default=os.environ.get("GCP_PROJECT_ID"))
    parser.add_argument("--location", default="global")
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument(
        "--max-new",
        type=int,
        help="Limit new registrations in this run; zero performs a ledger audit only.",
    )
    parser.add_argument("--result", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.project_id:
        raise ValueError("--project-id or GCP_PROJECT_ID is required")
    exit_code = 0
    try:
        result = plan_hypotheses(
            policy_path=args.policy,
            universe_path=args.universe,
            ledger_dir=args.ledger_dir,
            output_dir=args.output_dir,
            project_id=args.project_id,
            location=args.location,
            model=args.model,
            max_new=args.max_new,
        )
    except Exception as exc:
        result = {
            "state": "failed",
            "created": [],
            "reused": [],
            "registered_count": None,
            "model": args.model,
            "failure_reason_type": type(exc).__name__,
        }
        exit_code = 1
    destination = Path(args.result)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
