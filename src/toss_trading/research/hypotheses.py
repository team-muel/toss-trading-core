from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


POLICY_SCHEMA = "autonomous-research-policy-v1"
HYPOTHESIS_SCHEMA = "strategy-hypothesis-v1"
VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def load_research_policy(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != POLICY_SCHEMA:
        raise ValueError("unsupported autonomous research policy")
    integer_bounds = {
        "max_new_hypotheses_per_week": (1, 10),
        "max_registered_hypotheses": (1, 500),
        "minimum_walk_forward_folds": (2, 20),
        "minimum_prospective_trading_days": (126, 1260),
        "minimum_prospective_rebalances": (6, 60),
        "bootstrap_samples": (200, 10000),
        "bootstrap_block_days": (5, 126),
    }
    for field, (minimum, maximum) in integer_bounds.items():
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"research policy {field} must be an integer")
        if not minimum <= value <= maximum:
            raise ValueError(f"research policy {field} is outside safe bounds")
    alpha = payload.get("familywise_alpha")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise ValueError("research policy familywise_alpha must be numeric")
    if not 0 < float(alpha) <= 0.1:
        raise ValueError("research policy familywise_alpha is outside safe bounds")
    outperformance_ratio = payload.get("minimum_benchmark_outperformance_ratio")
    if isinstance(outperformance_ratio, bool) or not isinstance(
        outperformance_ratio, (int, float)
    ):
        raise ValueError(
            "research policy minimum_benchmark_outperformance_ratio must be numeric"
        )
    if not 0.5 <= float(outperformance_ratio) <= 1.0:
        raise ValueError(
            "research policy minimum_benchmark_outperformance_ratio is outside safe bounds"
        )
    stress = payload.get("cost_stress_multiplier")
    if isinstance(stress, bool) or not isinstance(stress, (int, float)):
        raise ValueError("research policy cost_stress_multiplier must be numeric")
    if not 1.5 <= float(stress) <= 5.0:
        raise ValueError(
            "research policy cost_stress_multiplier is outside safe bounds"
        )
    for field in (
        "require_paper_execution_evidence",
        "require_shadow_execution_evidence",
    ):
        if payload.get(field) is not True:
            raise ValueError(f"research policy {field} must remain enabled")
    return payload


@dataclass(frozen=True)
class StrategyHypothesis:
    schema_version: str
    hypothesis_id: str
    registered_at: str
    created_by_model: str
    strategy_family: str
    thesis: str
    falsification_criteria: tuple[str, ...]
    config: dict[str, Any]
    state: str = "registered"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bounded_text(value: Any, *, field: str, maximum: int = 800) -> str:
    if not isinstance(value, str):
        raise ValueError(f"hypothesis {field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"hypothesis {field} has invalid length")
    return normalized


def hypothesis_from_proposal(
    proposal: dict[str, Any],
    *,
    policy: dict[str, Any],
    model: str,
    registered_at: str | None = None,
) -> StrategyHypothesis:
    if not isinstance(proposal, dict):
        raise ValueError("hypothesis proposal must be an object")
    thesis = _bounded_text(proposal.get("thesis"), field="thesis")
    criteria = proposal.get("falsification_criteria")
    if (
        not isinstance(criteria, list)
        or not 1 <= len(criteria) <= 5
        or any(not isinstance(item, str) for item in criteria)
    ):
        raise ValueError("hypothesis needs one to five falsification criteria")
    normalized_criteria = tuple(
        _bounded_text(item, field="falsification_criteria", maximum=400)
        for item in criteria
    )
    config = proposal.get("config")
    if not isinstance(config, dict):
        raise ValueError("hypothesis config must be an object")
    allowed_fields = {
        "candidate_symbols",
        "cash_symbol",
        "lookback_trading_days",
        "skip_recent_trading_days",
        "top_k",
        "minimum_absolute_momentum",
        "walk_forward_train_days",
        "walk_forward_test_days",
    }
    if set(config) != allowed_fields:
        raise ValueError("hypothesis config fields differ from the bounded strategy DSL")
    candidates = config["candidate_symbols"]
    allowed_candidates = set(policy["allowed_candidate_symbols"])
    if (
        not isinstance(candidates, list)
        or len(candidates) < 2
        or len(candidates) != len(set(candidates))
        or not set(candidates).issubset(allowed_candidates)
    ):
        raise ValueError("hypothesis candidate symbols are outside policy")
    exact_values = {
        "cash_symbol": policy["cash_symbol"],
        "walk_forward_train_days": policy["walk_forward_train_days"],
        "walk_forward_test_days": policy["walk_forward_test_days"],
    }
    for field, expected in exact_values.items():
        if config[field] != expected:
            raise ValueError(f"hypothesis {field} differs from locked policy")
    allowed_values = {
        "lookback_trading_days": policy["allowed_lookback_trading_days"],
        "skip_recent_trading_days": policy["allowed_skip_recent_trading_days"],
        "top_k": policy["allowed_top_k"],
        "minimum_absolute_momentum": policy[
            "allowed_minimum_absolute_momentum"
        ],
    }
    for field, allowed in allowed_values.items():
        if config[field] not in allowed:
            raise ValueError(f"hypothesis {field} is outside policy")
    if config["top_k"] > len(candidates):
        raise ValueError("hypothesis top_k exceeds its candidate universe")

    normalized_config = {
        **config,
        "candidate_symbols": sorted(candidates),
    }
    identity = {
        "strategy_family": policy["strategy_family"],
        "config": normalized_config,
    }
    hypothesis_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "toss-hypothesis:" + hashlib.sha256(_canonical(identity)).hexdigest(),
        )
    )
    return StrategyHypothesis(
        schema_version=HYPOTHESIS_SCHEMA,
        hypothesis_id=hypothesis_id,
        registered_at=registered_at or datetime.now(timezone.utc).isoformat(),
        created_by_model=_bounded_text(model, field="created_by_model", maximum=120),
        strategy_family=str(policy["strategy_family"]),
        thesis=thesis,
        falsification_criteria=normalized_criteria,
        config=normalized_config,
    )


class HypothesisLedger:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.hypotheses = self.root / "hypotheses"
        self.evaluations = self.root / "evaluations"
        self.prospective_protocols = self.root / "prospective_protocols"

    def registered(self) -> list[dict[str, Any]]:
        if not self.hypotheses.is_dir():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.hypotheses.glob("*.json"))
        ]

    def register(
        self,
        hypothesis: StrategyHypothesis,
        *,
        output_dir: str | Path | None = None,
    ) -> tuple[Path, bool]:
        self.hypotheses.mkdir(parents=True, exist_ok=True)
        destination = self.hypotheses / f"{hypothesis.hypothesis_id}.json"
        body = json.dumps(
            hypothesis.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"
        created = False
        if destination.exists():
            if destination.read_text(encoding="utf-8") != body:
                raise ValueError("immutable hypothesis content conflict")
        else:
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(body, encoding="utf-8")
            temporary.replace(destination)
            created = True
        if output_dir is not None:
            output = Path(output_dir)
            output.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(destination, output / destination.name)
        return destination, created

    def record_evaluation(
        self,
        *,
        hypothesis_id: str,
        run_id: str,
        result: dict[str, Any],
        output_dir: str | Path | None = None,
    ) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
            raise ValueError("evaluation run_id is unsafe")
        if not (self.hypotheses / f"{hypothesis_id}.json").is_file():
            raise ValueError("evaluation references an unknown hypothesis")
        destination = self.evaluations / hypothesis_id / f"{run_id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "hypothesis-evaluation-v1",
            "hypothesis_id": hypothesis_id,
            "run_id": run_id,
            **result,
        }
        body = json.dumps(payload, sort_keys=True, indent=2) + "\n"
        if destination.exists():
            if destination.read_text(encoding="utf-8") != body:
                raise ValueError("immutable hypothesis evaluation conflict")
        else:
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(body, encoding="utf-8")
            temporary.replace(destination)
        if output_dir is not None:
            output = Path(output_dir) / hypothesis_id
            output.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(destination, output / destination.name)
        return destination

    def prospective_protocol(self, hypothesis_id: str) -> dict[str, Any] | None:
        path = self.prospective_protocols / f"{hypothesis_id}.json"
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "candidate-prospective-protocol-v1":
            raise ValueError("candidate prospective protocol schema is invalid")
        return payload

    def register_prospective_protocol(
        self,
        *,
        hypothesis: dict[str, Any],
        registered_at: str,
        historical_cutoff: str,
        policy: dict[str, Any],
        output_dir: str | Path | None = None,
    ) -> tuple[Path, bool]:
        hypothesis_id = str(hypothesis["hypothesis_id"])
        if not (self.hypotheses / f"{hypothesis_id}.json").is_file():
            raise ValueError("prospective protocol references an unknown hypothesis")
        config = hypothesis.get("config")
        if not isinstance(config, dict):
            raise ValueError("prospective protocol hypothesis config is invalid")
        payload = {
            "schema_version": "candidate-prospective-protocol-v1",
            "hypothesis_id": hypothesis_id,
            "registered_at": registered_at,
            "historical_cutoff": historical_cutoff,
            "minimum_trading_days": int(
                policy["minimum_prospective_trading_days"]
            ),
            "minimum_rebalances": int(
                policy["minimum_prospective_rebalances"]
            ),
            "primary_benchmark": "SPY buy-and-hold",
            "config_sha256": hashlib.sha256(_canonical(config)).hexdigest(),
            "performance_peeking": "metrics_hidden_until_both_minimums",
            "automatic_promotion": False,
        }
        body = json.dumps(payload, sort_keys=True, indent=2) + "\n"
        self.prospective_protocols.mkdir(parents=True, exist_ok=True)
        destination = self.prospective_protocols / f"{hypothesis_id}.json"
        created = False
        if destination.exists():
            if destination.read_text(encoding="utf-8") != body:
                raise ValueError("immutable prospective protocol conflict")
        else:
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(body, encoding="utf-8")
            temporary.replace(destination)
            created = True
        if output_dir is not None:
            output = Path(output_dir)
            output.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(destination, output / destination.name)
        return destination, created


def _default_authorized_session() -> Any:
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=[VERTEX_SCOPE])
    return AuthorizedSession(credentials)


@dataclass
class VertexHypothesisPlanner:
    project_id: str
    model: str
    location: str = "global"
    session_factory: Callable[[], Any] = _default_authorized_session

    def _endpoint(self) -> str:
        if not self.project_id.strip():
            raise ValueError("Vertex AI project id is required")
        if not _MODEL_PATTERN.fullmatch(self.model) or not _MODEL_PATTERN.fullmatch(
            self.location
        ):
            raise ValueError("Vertex AI planner model or location is invalid")
        return (
            "https://aiplatform.googleapis.com/v1/projects/"
            f"{self.project_id}/locations/{self.location}/publishers/google/"
            f"models/{self.model}:generateContent"
        )

    def propose(
        self,
        *,
        policy: dict[str, Any],
        registered: list[dict[str, Any]],
        available_symbols: list[str],
    ) -> list[dict[str, Any]]:
        count = int(policy["max_new_hypotheses_per_week"])
        context = {
            "policy": policy,
            "available_symbols": sorted(set(available_symbols)),
            "already_registered_configs": [item["config"] for item in registered],
        }
        prompt = (
            "다음은 코드 실행 권한이 없는 제한된 정량 연구 제안 환경이다. "
            "정책에 열거된 값만 사용하여 서로 다른 dual momentum 가설을 최대 "
            f"{count}개 제안하라. 수익을 보장하거나 미래를 예측하지 말고, 경제적 "
            "논리와 명확한 반증 조건을 한국어로 작성하라. 이미 등록된 config는 "
            "반복하지 말라. JSON 컨텍스트:\n"
            + json.dumps(context, ensure_ascii=False, sort_keys=True)
        )
        config_schema = {
            "type": "OBJECT",
            "required": [
                "candidate_symbols",
                "cash_symbol",
                "lookback_trading_days",
                "skip_recent_trading_days",
                "top_k",
                "minimum_absolute_momentum",
                "walk_forward_train_days",
                "walk_forward_test_days",
            ],
            "properties": {
                "candidate_symbols": {
                    "type": "ARRAY",
                    "minItems": 2,
                    "items": {
                        "type": "STRING",
                        "enum": sorted(policy["allowed_candidate_symbols"]),
                    },
                },
                "cash_symbol": {
                    "type": "STRING",
                    "enum": [policy["cash_symbol"]],
                },
                "lookback_trading_days": {"type": "INTEGER"},
                "skip_recent_trading_days": {"type": "INTEGER"},
                "top_k": {"type": "INTEGER"},
                "minimum_absolute_momentum": {"type": "NUMBER"},
                "walk_forward_train_days": {"type": "INTEGER"},
                "walk_forward_test_days": {"type": "INTEGER"},
            },
        }
        response = self.session_factory().post(
            self._endpoint(),
            json={
                "systemInstruction": {
                    "parts": [
                        {
                            "text": (
                                "당신은 전략 실행자가 아니라 제한된 가설 제안자다. "
                                "임의 코드, 새 데이터 소스, 정책 밖 파라미터를 만들지 않는다."
                            )
                        }
                    ]
                },
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 3000,
                    "responseMimeType": "application/json",
                    "responseSchema": {
                        "type": "OBJECT",
                        "required": ["hypotheses"],
                        "properties": {
                            "hypotheses": {
                                "type": "ARRAY",
                                "maxItems": count,
                                "items": {
                                    "type": "OBJECT",
                                    "required": [
                                        "thesis",
                                        "falsification_criteria",
                                        "config",
                                    ],
                                    "properties": {
                                        "thesis": {"type": "STRING"},
                                        "falsification_criteria": {
                                            "type": "ARRAY",
                                            "items": {"type": "STRING"},
                                        },
                                        "config": config_schema,
                                    },
                                },
                            }
                        },
                    },
                },
            },
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise RuntimeError("Vertex AI returned no hypothesis candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(
            str(item.get("text", "")) for item in parts if isinstance(item, dict)
        )
        decoded = json.loads(text)
        hypotheses = decoded.get("hypotheses")
        if not isinstance(hypotheses, list) or len(hypotheses) > count:
            raise ValueError("Vertex AI hypothesis count exceeds policy")
        return hypotheses
