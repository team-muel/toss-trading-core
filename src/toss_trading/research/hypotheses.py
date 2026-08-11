from __future__ import annotations

import hashlib
import json
import re
import shutil
import statistics
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from toss_trading.research.macro import MACRO_SIGNAL_NAMES


POLICY_SCHEMA = "autonomous-research-policy-v2"
HYPOTHESIS_SCHEMA = "strategy-hypothesis-v2"
LEGACY_STRATEGY_FAMILY = "dual_momentum"
FACTOR_NAMES = (
    "momentum",
    "risk_adjusted_momentum",
    "short_term_reversal",
    "low_volatility",
    "trend_acceleration",
)
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
        "proposal_pool_multiplier": (1, 5),
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
    families = payload.get("strategy_families")
    rotation = payload.get("family_rotation")
    if (
        not isinstance(families, list)
        or len(families) < 3
        or len(families) != len(set(families))
        or any(not isinstance(item, str) or not item for item in families)
    ):
        raise ValueError("research policy strategy_families is invalid")
    if not isinstance(rotation, list) or set(rotation) != set(families):
        raise ValueError("research policy family_rotation must cover every family")
    novelty = payload.get("minimum_structural_novelty")
    if isinstance(novelty, bool) or not isinstance(novelty, (int, float)):
        raise ValueError("research policy minimum_structural_novelty must be numeric")
    if not 0.05 <= float(novelty) <= 0.75:
        raise ValueError("research policy minimum_structural_novelty is outside safe bounds")
    allowed_weights = payload.get("allowed_factor_weights")
    if (
        not isinstance(allowed_weights, list)
        or 0.0 not in allowed_weights
        or 1.0 not in allowed_weights
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not 0 <= float(item) <= 1
            for item in allowed_weights
        )
    ):
        raise ValueError("research policy allowed_factor_weights is invalid")
    if "macro_regime" in families:
        macro_weights = payload.get("allowed_macro_signal_weights")
        if (
            not isinstance(macro_weights, list)
            or 0.0 not in macro_weights
            or 1.0 not in macro_weights
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not 0 <= float(item) <= 1
                for item in macro_weights
            )
        ):
            raise ValueError(
                "research policy allowed_macro_signal_weights is invalid"
            )
        lag = payload.get("macro_publication_lag_days")
        if isinstance(lag, bool) or not isinstance(lag, int) or lag not in range(1, 8):
            raise ValueError("research policy macro_publication_lag_days is invalid")
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


def _validate_common_config(
    config: dict[str, Any], *, policy: dict[str, Any]
) -> list[str]:
    candidates = config.get("candidate_symbols")
    allowed_candidates = set(policy["allowed_candidate_symbols"])
    if (
        not isinstance(candidates, list)
        or len(candidates) < 2
        or len(candidates) != len(set(candidates))
        or any(not isinstance(item, str) for item in candidates)
        or not set(candidates).issubset(allowed_candidates)
    ):
        raise ValueError("hypothesis candidate symbols are outside policy")
    for field, expected in {
        "cash_symbol": policy["cash_symbol"],
        "walk_forward_train_days": policy["walk_forward_train_days"],
        "walk_forward_test_days": policy["walk_forward_test_days"],
    }.items():
        if config.get(field) != expected:
            raise ValueError(f"hypothesis {field} differs from locked policy")
    if config.get("top_k") not in policy["allowed_top_k"]:
        raise ValueError("hypothesis top_k is outside policy")
    if int(config["top_k"]) > len(candidates):
        raise ValueError("hypothesis top_k exceeds its candidate universe")
    return sorted(candidates)


def _normalize_legacy_config(
    config: dict[str, Any], *, policy: dict[str, Any]
) -> dict[str, Any]:
    fields = {
        "candidate_symbols",
        "cash_symbol",
        "lookback_trading_days",
        "skip_recent_trading_days",
        "top_k",
        "minimum_absolute_momentum",
        "walk_forward_train_days",
        "walk_forward_test_days",
    }
    if set(config) != fields:
        raise ValueError("hypothesis config fields differ from the bounded strategy DSL")
    candidates = _validate_common_config(config, policy=policy)
    for field, allowed in {
        "lookback_trading_days": policy["allowed_lookback_trading_days"],
        "skip_recent_trading_days": policy["allowed_skip_recent_trading_days"],
        "minimum_absolute_momentum": policy["allowed_minimum_absolute_momentum"],
    }.items():
        if config[field] not in allowed:
            raise ValueError(f"hypothesis {field} is outside policy")
    return {**config, "candidate_symbols": candidates}


def _normalize_factor_config(
    config: dict[str, Any], *, policy: dict[str, Any], strategy_family: str
) -> dict[str, Any]:
    fields = {
        "candidate_symbols",
        "cash_symbol",
        "factor_weights",
        "long_lookback_trading_days",
        "short_lookback_trading_days",
        "volatility_window_trading_days",
        "skip_recent_trading_days",
        "top_k",
        "weighting",
        "rebalance_frequency",
        "regime_filter",
        "minimum_composite_score",
        "walk_forward_train_days",
        "walk_forward_test_days",
    }
    if set(config) != fields:
        raise ValueError("hypothesis config fields differ from the bounded factor DSL")
    candidates = _validate_common_config(config, policy=policy)
    for field, allowed in {
        "long_lookback_trading_days": policy["allowed_long_lookback_trading_days"],
        "short_lookback_trading_days": policy["allowed_short_lookback_trading_days"],
        "volatility_window_trading_days": policy[
            "allowed_volatility_window_trading_days"
        ],
        "skip_recent_trading_days": policy["allowed_skip_recent_trading_days"],
        "weighting": policy["allowed_weighting"],
        "rebalance_frequency": policy["allowed_rebalance_frequency"],
        "regime_filter": policy["allowed_regime_filter"],
        "minimum_composite_score": policy["allowed_minimum_composite_score"],
    }.items():
        if config[field] not in allowed:
            raise ValueError(f"hypothesis {field} is outside policy")
    if int(config["long_lookback_trading_days"]) <= int(
        config["short_lookback_trading_days"]
    ):
        raise ValueError("hypothesis long lookback must exceed short lookback")
    weights = config.get("factor_weights")
    if not isinstance(weights, dict) or set(weights) != set(FACTOR_NAMES):
        raise ValueError("hypothesis factor_weights differs from the bounded factors")
    allowed_weights = policy["allowed_factor_weights"]
    if any(value not in allowed_weights for value in weights.values()):
        raise ValueError("hypothesis factor weight is outside policy")
    active = {name for name, value in weights.items() if float(value) > 0}
    required_single = {
        "cross_sectional_momentum": "momentum",
        "risk_adjusted_momentum": "risk_adjusted_momentum",
        "short_term_reversal": "short_term_reversal",
        "low_volatility": "low_volatility",
        "trend_acceleration": "trend_acceleration",
    }
    if strategy_family in required_single and active != {required_single[strategy_family]}:
        raise ValueError("hypothesis factor weights do not match its strategy family")
    if strategy_family == "multi_factor_composite" and not 2 <= len(active) <= 3:
        raise ValueError("multi-factor hypothesis must activate two or three factors")
    normalized_weights = {
        name: float(weights[name]) for name in sorted(FACTOR_NAMES)
    }
    return {
        **config,
        "candidate_symbols": candidates,
        "factor_weights": normalized_weights,
    }


def _normalize_macro_config(
    config: dict[str, Any], *, policy: dict[str, Any]
) -> dict[str, Any]:
    fields = {
        "risk_on_symbols",
        "defensive_symbols",
        "cash_symbol",
        "macro_signal_weights",
        "signal_lookback_months",
        "minimum_regime_score",
        "rebalance_frequency",
        "publication_lag_days",
        "walk_forward_train_days",
        "walk_forward_test_days",
    }
    if set(config) != fields:
        raise ValueError("hypothesis config fields differ from the bounded macro DSL")
    risk_on = config.get("risk_on_symbols")
    defensive = config.get("defensive_symbols")
    allowed_risk = set(policy["allowed_candidate_symbols"])
    allowed_defensive = set(policy["allowed_macro_defensive_symbols"])
    if (
        not isinstance(risk_on, list)
        or len(risk_on) < 2
        or len(risk_on) != len(set(risk_on))
        or not set(risk_on).issubset(allowed_risk)
    ):
        raise ValueError("macro risk-on symbols are outside policy")
    if (
        not isinstance(defensive, list)
        or not defensive
        or len(defensive) != len(set(defensive))
        or not set(defensive).issubset(allowed_defensive)
        or policy["cash_symbol"] not in defensive
        or set(risk_on) & set(defensive)
    ):
        raise ValueError("macro defensive symbols are outside policy")
    for field, expected in {
        "cash_symbol": policy["cash_symbol"],
        "rebalance_frequency": "monthly",
        "publication_lag_days": policy["macro_publication_lag_days"],
        "walk_forward_train_days": policy["walk_forward_train_days"],
        "walk_forward_test_days": policy["walk_forward_test_days"],
    }.items():
        if config.get(field) != expected:
            raise ValueError(f"macro hypothesis {field} differs from locked policy")
    if config.get("signal_lookback_months") not in policy[
        "allowed_macro_signal_lookback_months"
    ]:
        raise ValueError("macro signal lookback is outside policy")
    if config.get("minimum_regime_score") not in policy[
        "allowed_macro_regime_score"
    ]:
        raise ValueError("macro regime threshold is outside policy")
    weights = config.get("macro_signal_weights")
    if not isinstance(weights, dict) or set(weights) != set(MACRO_SIGNAL_NAMES):
        raise ValueError("macro signal weights differ from the bounded signals")
    allowed_weights = policy["allowed_macro_signal_weights"]
    if any(value not in allowed_weights for value in weights.values()):
        raise ValueError("macro signal weight is outside policy")
    if not 1 <= sum(float(value) > 0 for value in weights.values()) <= 4:
        raise ValueError("macro hypothesis must activate one to four signals")
    return {
        **config,
        "risk_on_symbols": sorted(risk_on),
        "defensive_symbols": sorted(defensive),
        "macro_signal_weights": {
            name: float(weights[name]) for name in sorted(MACRO_SIGNAL_NAMES)
        },
    }


def structural_novelty(
    proposal: StrategyHypothesis, registered: list[dict[str, Any]]
) -> tuple[float, str | None]:
    """Return minimum normalized config distance within the same family."""

    comparable = [
        item
        for item in registered
        if item.get("strategy_family") == proposal.strategy_family
        and isinstance(item.get("config"), dict)
    ]
    if not comparable:
        return 1.0, None
    current = proposal.config
    symbol_fields = ("candidate_symbols", "risk_on_symbols", "defensive_symbols")
    weight_fields = ("factor_weights", "macro_signal_weights")
    dimensions = sorted(set(current) - set(symbol_fields) - set(weight_fields))
    scores: list[tuple[float, str]] = []
    current_symbols = set().union(
        *(set(current.get(field, [])) for field in symbol_fields)
    )
    current_weights = next(
        (
            current.get(field)
            for field in weight_fields
            if isinstance(current.get(field), dict)
        ),
        {},
    )
    for item in comparable:
        other = item["config"]
        other_symbols = set().union(
            *(set(other.get(field, [])) for field in symbol_fields)
        )
        union = current_symbols | other_symbols
        symbol_distance = (
            1.0 - len(current_symbols & other_symbols) / len(union) if union else 0.0
        )
        scalar_distances = [
            0.0 if current.get(field) == other.get(field) else 1.0
            for field in dimensions
        ]
        other_weights = next(
            (
                other.get(field)
                for field in weight_fields
                if isinstance(other.get(field), dict)
            ),
            {},
        )
        weight_names = sorted(set(current_weights) | set(other_weights))
        weight_distance = (
            sum(
                abs(float(current_weights.get(name, 0.0)) - float(other_weights.get(name, 0.0)))
                for name in weight_names
            )
            / len(weight_names)
            if weight_names
            else 0.0
        )
        distance = statistics.fmean(
            [symbol_distance, weight_distance, *scalar_distances]
        )
        scores.append((distance, str(item.get("hypothesis_id"))))
    return min(scores, key=lambda item: item[0])


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
    strategy_family = str(proposal.get("strategy_family") or LEGACY_STRATEGY_FAMILY)
    if strategy_family == LEGACY_STRATEGY_FAMILY:
        normalized_config = _normalize_legacy_config(config, policy=policy)
    else:
        if strategy_family not in policy["strategy_families"]:
            raise ValueError("hypothesis strategy_family is outside policy")
        normalized_config = (
            _normalize_macro_config(config, policy=policy)
            if strategy_family == "macro_regime"
            else _normalize_factor_config(
                config,
                policy=policy,
                strategy_family=strategy_family,
            )
        )
    identity = {"strategy_family": strategy_family, "config": normalized_config}
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
        strategy_family=strategy_family,
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


def _macro_vertex_config_schema(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "OBJECT",
        "required": [
            "risk_on_symbols",
            "defensive_symbols",
            "cash_symbol",
            "macro_signal_weights",
            "signal_lookback_months",
            "minimum_regime_score",
            "rebalance_frequency",
            "publication_lag_days",
            "walk_forward_train_days",
            "walk_forward_test_days",
        ],
        "properties": {
            "risk_on_symbols": {
                "type": "ARRAY",
                "minItems": 2,
                "items": {
                    "type": "STRING",
                    "enum": sorted(policy["allowed_candidate_symbols"]),
                },
            },
            "defensive_symbols": {
                "type": "ARRAY",
                "minItems": 1,
                "items": {
                    "type": "STRING",
                    "enum": sorted(policy["allowed_macro_defensive_symbols"]),
                },
            },
            "cash_symbol": {"type": "STRING", "enum": [policy["cash_symbol"]]},
            "macro_signal_weights": {
                "type": "OBJECT",
                "required": list(MACRO_SIGNAL_NAMES),
                "properties": {
                    name: {"type": "NUMBER"} for name in MACRO_SIGNAL_NAMES
                },
            },
            "signal_lookback_months": {"type": "INTEGER"},
            "minimum_regime_score": {"type": "NUMBER"},
            "rebalance_frequency": {"type": "STRING", "enum": ["monthly"]},
            "publication_lag_days": {"type": "INTEGER"},
            "walk_forward_train_days": {"type": "INTEGER"},
            "walk_forward_test_days": {"type": "INTEGER"},
        },
    }


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
        pool_count = min(15, count * int(policy.get("proposal_pool_multiplier", 1)))
        target_families = policy.get("target_strategy_families")
        if not isinstance(target_families, list) or not target_families:
            target_families = list(policy["strategy_families"])
        context = {
            "target_strategy_families": target_families,
            "bounded_parameter_policy": {
                key: value
                for key, value in policy.items()
                if key.startswith("allowed_")
                or key
                in {
                    "cash_symbol",
                    "macro_publication_lag_days",
                    "walk_forward_train_days",
                    "walk_forward_test_days",
                }
            },
            "available_symbols": sorted(set(available_symbols)),
            "already_registered": [
                {
                    "strategy_family": item.get("strategy_family"),
                    "thesis": item.get("thesis"),
                    "config": item.get("config"),
                }
                for item in registered
            ],
        }
        prompt = (
            "다음은 코드 실행 권한이 없는 제한된 정량 연구 제안 환경이다. "
            "정책에 열거된 값만 사용하여 서로 다른 dual momentum 가설을 최대 "
            f"{count}개 제안하라. 수익을 보장하거나 미래를 예측하지 말고, 경제적 "
            "논리와 명확한 반증 조건을 한국어로 작성하라. 이미 등록된 config는 "
            "반복하지 말라. JSON 컨텍스트:\n"
            + json.dumps(context, ensure_ascii=False, sort_keys=True)
        )
        prompt = (
            "코드 실행 권한이 없는 제한된 퀀트 연구 제안 환경이다. "
            "지정된 연구 계열과 허용값만 사용하여 경제적 논리가 서로 다른 ETF 전략 가설을 "
            f"최대 {pool_count}개 제안하라. 기존 가설과 거의 같은 우주·주기·요인 조합은 피하고, "
            "수익을 보장하거나 미래를 예측한다고 표현하지 마라. 각 가설은 왜 시장 이상현상이 "
            "지속될 수 있는지와 어떤 증거가 나오면 폐기할지를 한국어로 명시해야 한다. "
            "multi_factor_composite는 정확히 2~3개 요인만 활성화하라. JSON 컨텍스트:\n"
            + json.dumps(context, ensure_ascii=False, sort_keys=True)
        )
        config_schema = {
            "type": "OBJECT",
            "required": [
                "candidate_symbols",
                "cash_symbol",
                "factor_weights",
                "long_lookback_trading_days",
                "short_lookback_trading_days",
                "volatility_window_trading_days",
                "skip_recent_trading_days",
                "top_k",
                "weighting",
                "rebalance_frequency",
                "regime_filter",
                "minimum_composite_score",
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
                "factor_weights": {
                    "type": "OBJECT",
                    "required": list(FACTOR_NAMES),
                    "properties": {
                        name: {
                            "type": "NUMBER",
                        }
                        for name in FACTOR_NAMES
                    },
                },
                "long_lookback_trading_days": {
                    "type": "INTEGER",
                },
                "short_lookback_trading_days": {
                    "type": "INTEGER",
                },
                "volatility_window_trading_days": {
                    "type": "INTEGER",
                },
                "skip_recent_trading_days": {
                    "type": "INTEGER",
                },
                "top_k": {
                    "type": "INTEGER",
                },
                "weighting": {
                    "type": "STRING",
                    "enum": policy["allowed_weighting"],
                },
                "rebalance_frequency": {
                    "type": "STRING",
                    "enum": policy["allowed_rebalance_frequency"],
                },
                "regime_filter": {
                    "type": "STRING",
                    "enum": policy["allowed_regime_filter"],
                },
                "minimum_composite_score": {
                    "type": "NUMBER",
                },
                "walk_forward_train_days": {
                    "type": "INTEGER",
                },
                "walk_forward_test_days": {
                    "type": "INTEGER",
                },
            },
        }
        if target_families == ["macro_regime"]:
            config_schema = _macro_vertex_config_schema(policy)
        prompt = (
            "코드를 실행할 수 없는 제한된 정량 연구 제안 환경이다. "
            f"지정된 연구 계열 {target_families}과 정책의 허용값만 사용하여 "
            f"서로 구조적으로 다른 ETF 전략 가설을 최대 {pool_count}개 제안하라. "
            "수익을 보장하거나 미래를 예측한다고 표현하지 말고, 각 가설에는 "
            "경제적 논리와 명확한 반증 조건을 한국어로 작성하라. "
            "거시경제 가설은 ALFRED 빈티지로 당시 공개된 값만 사용하며, "
            "미래 수정치나 임의의 발표 지연을 만들지 않는다. JSON 컨텍스트:\n"
            + json.dumps(context, ensure_ascii=False, sort_keys=True)
        )
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
                    "temperature": 0.55,
                    "maxOutputTokens": 6000,
                    "responseMimeType": "application/json",
                    "responseSchema": {
                        "type": "OBJECT",
                        "required": ["hypotheses"],
                        "properties": {
                            "hypotheses": {
                                "type": "ARRAY",
                                "maxItems": pool_count,
                                "items": {
                                    "type": "OBJECT",
                                    "required": [
                                        "strategy_family",
                                        "thesis",
                                        "falsification_criteria",
                                        "config",
                                    ],
                                    "properties": {
                                        "strategy_family": {
                                            "type": "STRING",
                                            "enum": target_families,
                                        },
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
        if not isinstance(hypotheses, list) or len(hypotheses) > pool_count:
            raise ValueError("Vertex AI hypothesis count exceeds policy")
        return hypotheses
