#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
RUNTIME_ROOT="${STOCK_RECOMMENDATION_RUNTIME_ROOT:-/home/seoje/toss-trading/stock-recommendation-runtime}"
JSON_LOG="${RUNTIME_ROOT}/stock_recommendations.jsonl"
LOCK_PATH="${RUNTIME_ROOT}/stock_recommendations.lock"
POLICY="${STOCK_RECOMMENDATION_POLICY:-config/stock_recommendation_policy.json}"
MASSIVE_SECRET="${MASSIVE_API_KEY_SECRET:-massive-api-key}"
mkdir -p "${RUNTIME_ROOT}/input/raw" "${RUNTIME_ROOT}/recommendations"

json_log() {
  printf '{"ts":"%s","event":"%s","state":"%s","reason":"%s"}\n' \
    "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$1" "${2:-}" "${3:-}" >> "${JSON_LOG}"
}
on_error() {
  local exit_code="$?"
  trap - ERR
  json_log "stock_recommendation_failed" "failed" "exit_code_${exit_code}"
  exit "${exit_code}"
}
trap on_error ERR

exec 9>"${LOCK_PATH}"
if ! flock -n 9; then
  json_log "stock_recommendation_lock_busy" "skipped" "overlap_prevented"
  exit 0
fi
if [[ "${RESEARCH_MASSIVE_PERSONAL_TERMS_APPROVED:-0}" != "1" ]]; then
  json_log "stock_recommendation_skipped" "gated" "massive_terms_not_approved"
  exit 0
fi
if [[ "$("${PYTHON_BIN}" - "${POLICY}" <<'PY'
import json
import sys
print("1" if json.load(open(sys.argv[1], encoding="utf-8"))["enabled"] else "0")
PY
)" != "1" ]]; then
  json_log "stock_recommendation_skipped" "gated" "policy_disabled"
  exit 0
fi
if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi
export PYTHONPATH="${PYTHONPATH:-src}"
: "${GCP_PROJECT_ID:=toss-trading-core-lab}"
export MASSIVE_API_KEY
MASSIVE_API_KEY="$(gcloud secrets versions access latest \
  --project="${GCP_PROJECT_ID}" --secret="${MASSIVE_SECRET}")"
if [[ -z "${MASSIVE_API_KEY}" ]]; then
  echo "Massive API key secret is empty" >&2
  exit 78
fi

REFERENCE="${RUNTIME_ROOT}/input/common-stock-reference.json"
if [[ ! -f "${REFERENCE}" ]] || find "${REFERENCE}" -mtime +7 -print -quit | grep -q .; then
  "${PYTHON_BIN}" -m toss_trading.cli.research_collect_massive reference \
    --output "${REFERENCE}" >/dev/null
fi
read -r START_DATE THROUGH_DATE < <(
  "${PYTHON_BIN}" - <<'PY'
from datetime import date, timedelta
through = date.today() - timedelta(days=1)
print((through - timedelta(days=420)).isoformat(), through.isoformat())
PY
)
BAR_JSONL="${RUNTIME_ROOT}/input/common-stock-bars.jsonl"
"${PYTHON_BIN}" -m toss_trading.cli.research_collect_massive grouped \
  --reference "${REFERENCE}" \
  --start-date "${START_DATE}" \
  --through-date "${THROUGH_DATE}" \
  --raw-directory "${RUNTIME_ROOT}/input/raw" \
  --output-jsonl "${BAR_JSONL}" \
  --summary "${RUNTIME_ROOT}/input/grouped-summary.json" >/dev/null
AS_OF_DATE="$("${PYTHON_BIN}" - "${BAR_JSONL}" <<'PY'
import json
import sys
print(max(json.loads(line)["exchange_local_date"] for line in open(sys.argv[1], encoding="utf-8") if line.strip()))
PY
)"
CODE_REVISION="$(basename "$(readlink -f "${ROOT_DIR}")")"
RESULT="$("${PYTHON_BIN}" -m toss_trading.cli.research_recommend_stocks \
  --input-jsonl "${BAR_JSONL}" \
  --policy "${POLICY}" \
  --as-of-date "${AS_OF_DATE}" \
  --code-revision "${CODE_REVISION}" \
  --output-dir "${RUNTIME_ROOT}/recommendations")"
RECOMMENDATION_ID="$("${PYTHON_BIN}" -c 'import json,sys; print(json.load(sys.stdin)["recommendation_id"])' <<<"${RESULT}")"
ln -sfn "recommendations/${RECOMMENDATION_ID}.json" "${RUNTIME_ROOT}/latest.tmp"
mv -Tf "${RUNTIME_ROOT}/latest.tmp" "${RUNTIME_ROOT}/latest"
json_log "stock_recommendation_ok" "complete" "${RECOMMENDATION_ID}"
