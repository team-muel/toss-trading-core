from __future__ import annotations

import html
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STRATEGY_METRIC_KEYS = (
    "total_return",
    "cagr",
    "annualized_volatility",
    "sharpe_zero_rate",
    "max_drawdown",
    "calmar",
    "turnover",
    "trading_days",
)


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"strategy metric is not numeric: {field}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"strategy metric is not finite: {field}")
    return result


def strategy_snapshot(
    experiment_path: str | Path | None,
) -> dict[str, Any]:
    if experiment_path is None:
        return {
            "state": "not_available",
            "reason": "verified_total_return_history_not_available",
            "strategy": None,
            "experiment_id": None,
            "code_revision": None,
            "metrics": {key: None for key in STRATEGY_METRIC_KEYS},
        }

    path = Path(experiment_path)
    payload = _read_object(path)
    strategy = payload.get("strategy")
    metrics = payload.get("metrics")
    if not isinstance(strategy, str) or not strategy.strip():
        raise ValueError(f"strategy experiment has no strategy name: {path}")
    if not isinstance(metrics, dict):
        raise ValueError(f"strategy experiment has no metrics object: {path}")
    missing = [key for key in STRATEGY_METRIC_KEYS if key not in metrics]
    if missing:
        raise ValueError(f"strategy experiment lacks metrics: {missing}")
    return {
        "state": "available",
        "reason": None,
        "strategy": strategy.strip(),
        "experiment_id": path.stem,
        "code_revision": payload.get("code_revision"),
        "metrics": {
            key: _finite_number(metrics[key], field=key)
            for key in STRATEGY_METRIC_KEYS
        },
    }


def build_research_summary(
    *,
    run_id: str,
    verified_at: str,
    mode: str,
    code_revision: str,
    provider_states: dict[str, str],
    toss: dict[str, Any],
    quality: dict[str, Any],
    artifacts: dict[str, Any],
    strategy_experiment: str | Path | None = None,
) -> dict[str, Any]:
    quality_error_rows = sum(
        int(quality[key])
        for key in (
            "duplicate_rows",
            "invalid_rows",
            "coverage_mismatch_rows",
        )
    )
    summary = {
        "schema_version": "research-visual-report-v1",
        "run_id": run_id,
        "verified_at": verified_at,
        "mode": mode,
        "code_revision": code_revision,
        "ready_for_upload": True,
        "provider_states": dict(sorted(provider_states.items())),
        "toss": {
            "symbols_requested": int(toss["symbols_requested"]),
            "raw_pages": int(toss["raw_pages"]),
            "adjusted_pages": int(toss["adjusted_pages"]),
            "raw_failure_count": len(toss["raw_failures"]),
            "adjusted_failure_count": len(toss["adjusted_failures"]),
        },
        "quality": {
            "adjustments": sorted(quality["adjustments"]),
            "duplicate_rows": int(quality["duplicate_rows"]),
            "invalid_rows": int(quality["invalid_rows"]),
            "coverage_mismatch_rows": int(
                quality["coverage_mismatch_rows"]
            ),
            "error_rows": quality_error_rows,
            "symbol_count": len(quality["symbols"]),
        },
        "artifacts": {
            "source_files": int(artifacts["source_files"]),
            "source_bytes": int(artifacts["source_bytes"]),
            "manifests": int(artifacts["manifests"]),
            "parquet_files": int(artifacts["parquet_files"]),
        },
        "strategy": strategy_snapshot(strategy_experiment),
    }
    return summary


def summary_to_bigquery_row(
    summary: dict[str, Any],
    *,
    ingested_at: str | None = None,
) -> dict[str, Any]:
    providers = summary["provider_states"]
    toss = summary["toss"]
    quality = summary["quality"]
    artifacts = summary["artifacts"]
    strategy = summary["strategy"]
    metrics = strategy["metrics"]
    row = {
        "schema_version": summary["schema_version"],
        "run_id": summary["run_id"],
        "verified_at": summary["verified_at"],
        "ingested_at": ingested_at
        or datetime.now(timezone.utc).isoformat(),
        "mode": summary["mode"],
        "code_revision": summary["code_revision"],
        "ready_for_upload": summary["ready_for_upload"],
        "provider_toss": providers.get("toss"),
        "provider_tiingo": providers.get("tiingo"),
        "provider_fred_alfred": providers.get("fred-alfred"),
        "provider_sec_edgar": providers.get("sec-edgar"),
        "toss_symbols_requested": toss["symbols_requested"],
        "toss_raw_pages": toss["raw_pages"],
        "toss_adjusted_pages": toss["adjusted_pages"],
        "toss_raw_failure_count": toss["raw_failure_count"],
        "toss_adjusted_failure_count": toss["adjusted_failure_count"],
        "quality_adjustments": ",".join(quality["adjustments"]),
        "quality_duplicate_rows": quality["duplicate_rows"],
        "quality_invalid_rows": quality["invalid_rows"],
        "quality_coverage_mismatch_rows": quality[
            "coverage_mismatch_rows"
        ],
        "quality_error_rows": quality["error_rows"],
        "quality_symbol_count": quality["symbol_count"],
        "artifact_source_files": artifacts["source_files"],
        "artifact_source_bytes": artifacts["source_bytes"],
        "artifact_manifests": artifacts["manifests"],
        "artifact_parquet_files": artifacts["parquet_files"],
        "strategy_state": strategy["state"],
        "strategy_reason": strategy["reason"],
        "strategy_name": strategy["strategy"],
        "strategy_experiment_id": strategy["experiment_id"],
        "strategy_code_revision": strategy["code_revision"],
    }
    for key in STRATEGY_METRIC_KEYS:
        row[f"strategy_{key}"] = metrics[key]
    return row


def build_monitoring_event(summary: dict[str, Any]) -> dict[str, Any]:
    row = summary_to_bigquery_row(
        summary,
        ingested_at=summary["verified_at"],
    )
    event = {
        "ts": summary["verified_at"],
        "event": "research_reporting_summary",
        "run_id": summary["run_id"],
        "mode": summary["mode"],
        "code_revision": summary["code_revision"],
        "ready_for_upload": summary["ready_for_upload"],
        "quality_error_rows": row["quality_error_rows"],
        "quality_symbol_count": row["quality_symbol_count"],
        "toss_page_count": (
            row["toss_raw_pages"] + row["toss_adjusted_pages"]
        ),
        "artifact_source_bytes": row["artifact_source_bytes"],
        "strategy_state": row["strategy_state"],
    }
    if row["strategy_state"] == "available":
        for key in STRATEGY_METRIC_KEYS:
            event[f"strategy_{key}"] = row[f"strategy_{key}"]
        event["strategy_name"] = row["strategy_name"]
        event["strategy_experiment_id"] = row["strategy_experiment_id"]
    else:
        event["strategy_reason"] = row["strategy_reason"]
    return event


def render_visual_report(summary: dict[str, Any]) -> str:
    def escaped(value: Any) -> str:
        return html.escape(str(value), quote=True)

    def number(value: Any) -> str:
        if value is None:
            return "미측정"
        return f"{float(value):,.4f}"

    quality = summary["quality"]
    strategy = summary["strategy"]
    metrics = strategy["metrics"]
    providers = "".join(
        (
            '<div class="provider"><span>'
            f"{escaped(name)}</span><strong>{escaped(state)}</strong></div>"
        )
        for name, state in summary["provider_states"].items()
    )
    quality_state = (
        "PASS" if quality["error_rows"] == 0 else "REVIEW"
    )
    strategy_state = (
        escaped(strategy["strategy"])
        if strategy["state"] == "available"
        else "총수익률 데이터 확보 전 · 미측정"
    )
    strategy_cards = "".join(
        (
            '<div class="metric strategy-metric"><span>'
            f"{escaped(label)}</span><strong>{number(metrics[key])}</strong></div>"
        )
        for key, label in (
            ("total_return", "총수익률"),
            ("cagr", "CAGR"),
            ("sharpe_zero_rate", "Sharpe (Rf=0)"),
            ("max_drawdown", "최대 낙폭"),
        )
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Toss Trading 통합 보고서</title>
<style>
:root{{--bg:#07111f;--panel:#0f1d2e;--line:#203249;--text:#eef5ff;
--muted:#93a7bf;--good:#36d399;--warn:#fbbf24;--accent:#5ca9ff}}
*{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(145deg,#07111f,
#0a1728);color:var(--text);font-family:Inter,"Noto Sans KR",system-ui,sans-serif}}
main{{max-width:1120px;margin:auto;padding:40px 24px 64px}} h1{{margin:0 0 8px;
font-size:clamp(28px,4vw,44px)}} .sub{{color:var(--muted);margin-bottom:28px}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}}
.panel{{grid-column:span 12;background:rgba(15,29,46,.94);border:1px solid
var(--line);border-radius:18px;padding:20px;box-shadow:0 18px 45px #0005}}
.third{{grid-column:span 4}} .half{{grid-column:span 6}} .strategy-metric{{
grid-column:span 3;display:block}} .strategy-metric strong{{display:block;
font-size:24px;margin-top:8px}} h2{{font-size:17px;
margin:0 0 16px;color:#cfe4ff}} .hero{{font-size:34px;font-weight:800}}
.ok{{color:var(--good)}} .metric,.provider{{display:flex;align-items:center;
justify-content:space-between;padding:11px 0;border-bottom:1px solid var(--line)}}
.metric:last-child,.provider:last-child{{border:0}} span{{color:var(--muted)}}
strong{{font-variant-numeric:tabular-nums}} .note{{color:var(--muted);
line-height:1.7;margin:0}} code{{color:#b9d9ff}} footer{{color:var(--muted);
margin-top:24px;font-size:13px}} @media(max-width:760px){{.third,.half{{
grid-column:span 12}}.strategy-metric{{grid-column:span 6}}}}
</style>
</head>
<body><main>
<h1>Toss Trading 통합 보고서</h1>
<div class="sub">{escaped(summary["verified_at"])} · {escaped(summary["run_id"])}</div>
<div class="grid">
  <section class="panel third"><h2>수집 실행</h2>
    <div class="hero ok">정상</div>
    <p class="note">{escaped(summary["mode"])} · {escaped(summary["code_revision"])}</p>
  </section>
  <section class="panel third"><h2>데이터 품질</h2>
    <div class="hero {'ok' if quality_state == 'PASS' else ''}">{quality_state}</div>
    <p class="note">오류 행 {quality["error_rows"]:,} · 종목 {quality["symbol_count"]:,}</p>
  </section>
  <section class="panel third"><h2>전략 성과</h2>
    <div class="hero" style="font-size:20px">{strategy_state}</div>
    <p class="note">검증되지 않은 값은 0이 아니라 미측정으로 표시합니다.</p>
  </section>
  <section class="panel half"><h2>공급자 상태</h2>{providers}</section>
  <section class="panel half"><h2>품질 세부 지표</h2>
    <div class="metric"><span>중복 행</span><strong>{quality["duplicate_rows"]:,}</strong></div>
    <div class="metric"><span>유효성 오류 행</span><strong>{quality["invalid_rows"]:,}</strong></div>
    <div class="metric"><span>coverage 불일치 행</span><strong>{quality["coverage_mismatch_rows"]:,}</strong></div>
    <div class="metric"><span>조정 유형</span><strong>{escaped(", ".join(quality["adjustments"]))}</strong></div>
  </section>
  <section class="panel"><h2>전략 기준선 성과</h2>
    <div class="grid">{strategy_cards}</div>
    <p class="note">성과는 total-return 일봉, 입력 manifest, 코드 revision이 모두
    검증된 불변 experiment가 연결된 경우에만 표시합니다.</p>
  </section>
</div>
<footer>이 HTML은 실행 산출물의 서명 대상입니다. 장기 이력은 BigQuery,
운영·품질·성과 추이는 Cloud Monitoring 대시보드에서 확인합니다.</footer>
</main></body></html>
"""
