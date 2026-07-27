#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

RUN_MODE="${1:-${RESEARCH_RUN_MODE:-daily}}"
if [[ "${RUN_MODE}" != "daily" && "${RUN_MODE}" != "weekly" ]]; then
  echo "research automation mode must be daily or weekly" >&2
  exit 64
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
RUNTIME_ROOT="${RESEARCH_RUNTIME_ROOT:-/home/seoje/toss-trading/research-runtime}"
RUNS_ROOT="${RUNTIME_ROOT}/runs"
JSON_LOG_PATH="${RESEARCH_JSON_LOG_PATH:-${RUNTIME_ROOT}/research_automation.jsonl}"
LOCK_PATH="${RESEARCH_LOCK_PATH:-${RUNTIME_ROOT}/research_automation.lock}"
TOSS_API_LOCK_PATH="${TOSS_API_LOCK_PATH:-/home/seoje/toss-trading/runtime/toss_api.lock}"
GCS_URI="${RESEARCH_GCS_URI:-}"
BIGQUERY_DATASET="${RESEARCH_BIGQUERY_DATASET:-toss_research_reporting}"
BIGQUERY_TABLE="${RESEARCH_BIGQUERY_TABLE:-run_summaries}"
STRATEGY_EXPERIMENT="${RESEARCH_STRATEGY_EXPERIMENT:-}"
CODE_REVISION="${FOUNDATION_CODE_REVISION:-}"
if [[ -z "${CODE_REVISION}" ]] && command -v git >/dev/null 2>&1; then
  CODE_REVISION="$(git -C "${ROOT_DIR}" rev-parse --verify HEAD 2>/dev/null || true)"
fi
if [[ -z "${CODE_REVISION}" ]]; then
  RESOLVED_ROOT="$(readlink -f "${ROOT_DIR}" 2>/dev/null || true)"
  RELEASE_REVISION="$(basename "${RESOLVED_ROOT}")"
  if [[ "${RELEASE_REVISION}" =~ ^[0-9a-f]{7,40}$ ]]; then
    CODE_REVISION="${RELEASE_REVISION}"
  fi
fi
if [[ -z "${CODE_REVISION}" ]]; then
  CODE_REVISION="unknown"
fi
export FOUNDATION_CODE_REVISION="${CODE_REVISION}"

RUN_TIMESTAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
RUN_ID="${RUN_MODE}-${RUN_TIMESTAMP}-${CODE_REVISION:0:12}"
RUN_DIR="${RUNS_ROOT}/${RUN_ID}"
INPUT_DIR="${RUN_DIR}/input"
REPORT_DIR="${RUN_DIR}/reports"
LAKE_DIR="${RUN_DIR}/lake"
mkdir -p \
  "${RUNS_ROOT}" \
  "${INPUT_DIR}" \
  "${REPORT_DIR}" \
  "$(dirname "${JSON_LOG_PATH}")" \
  "$(dirname "${TOSS_API_LOCK_PATH}")"

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  printf '%s' "${value}"
}

json_log() {
  local event="$1"
  local provider="${2:-}"
  local state="${3:-}"
  local reason="${4:-}"
  printf '{"ts":"%s","event":"%s","mode":"%s","run_id":"%s","code_revision":"%s","provider":"%s","state":"%s","reason":"%s"}\n' \
    "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    "$(json_escape "${event}")" \
    "$(json_escape "${RUN_MODE}")" \
    "$(json_escape "${RUN_ID}")" \
    "$(json_escape "${CODE_REVISION}")" \
    "$(json_escape "${provider}")" \
    "$(json_escape "${state}")" \
    "$(json_escape "${reason}")" >> "${JSON_LOG_PATH}"
}

on_error() {
  local exit_code="$?"
  trap - ERR
  json_log "research_automation_failed" "" "failed" "exit_code_${exit_code}"
  exit "${exit_code}"
}
trap on_error ERR

if ! command -v flock >/dev/null 2>&1; then
  json_log "research_automation_failed" "" "failed" "flock_missing"
  exit 70
fi
exec 9>"${LOCK_PATH}"
if ! flock -n 9; then
  json_log "research_automation_lock_busy" "" "skipped" "overlap_prevented"
  exit 0
fi
exec 8>"${TOSS_API_LOCK_PATH}"
if ! flock -n 8; then
  json_log "research_automation_lock_busy" "" "skipped" "toss_api_in_use"
  exit 0
fi

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi
export PYTHONPATH="${PYTHONPATH:-src}"

: "${GCP_PROJECT_ID:=toss-trading-core-lab}"
: "${TOSS_CLIENT_ID_SECRET:=toss-client-id}"
: "${TOSS_CLIENT_SECRET_SECRET:=toss-client-secret}"
: "${TOSS_ACCOUNT_SEQ_SECRET:=toss-account-seq}"
: "${TOSS_API_ENV_SECRET:=toss-api-env}"
: "${TOSS_BROKER_BASE_URL_SECRET:=toss-broker-base-url}"
export GCP_PROJECT_ID
export TOSS_CLIENT_ID_SECRET
export TOSS_CLIENT_SECRET_SECRET
export TOSS_ACCOUNT_SEQ_SECRET
export TOSS_API_ENV_SECRET
export TOSS_BROKER_BASE_URL_SECRET

# shellcheck disable=SC1091
source "scripts/load_gcp_secrets.sh"

load_optional_secret() {
  local env_name="$1"
  local secret_name="$2"
  if [[ -n "${!env_name:-}" ]]; then
    return 0
  fi
  if [[ -z "${secret_name}" ]]; then
    return 1
  fi
  local value
  if ! value="$(gcloud secrets versions access latest \
    --project="${GCP_PROJECT_ID}" \
    --secret="${secret_name}" 2>/dev/null)"; then
    return 1
  fi
  if [[ -z "${value}" ]]; then
    return 1
  fi
  export "${env_name}=${value}"
}

read -r START_DATE THROUGH_DATE REALTIME_START REALTIME_END < <(
  "${PYTHON_BIN}" -m toss_trading.cli.research_automation window \
    --mode "${RUN_MODE}" \
    --format fields
)

json_log "research_automation_start" "" "running" ""
PROVIDER_STATES=("toss=collected")

"${PYTHON_BIN}" -m toss_trading.cli.research_collect_toss collect \
  --universe "data/universe.csv" \
  --start-date "${START_DATE}" \
  --skip-unavailable-symbols \
  --raw \
  --output "${INPUT_DIR}/toss-candles-raw.json" \
  > "${REPORT_DIR}/toss-raw-collection.json"

"${PYTHON_BIN}" -m toss_trading.cli.research_collect_toss collect \
  --universe "data/universe.csv" \
  --start-date "${START_DATE}" \
  --skip-unavailable-symbols \
  --adjusted \
  --output "${INPUT_DIR}/toss-candles-adjusted.json" \
  > "${REPORT_DIR}/toss-adjusted-collection.json"

"${PYTHON_BIN}" -m toss_trading.cli.research_collect_toss ingest \
  --input "${INPUT_DIR}/toss-candles-raw.json" \
  --output-root "${LAKE_DIR}" \
  --through-date "${THROUGH_DATE}" \
  --code-revision "${CODE_REVISION}" \
  > "${REPORT_DIR}/toss-raw-ingest.json"

"${PYTHON_BIN}" -m toss_trading.cli.research_collect_toss ingest \
  --input "${INPUT_DIR}/toss-candles-adjusted.json" \
  --output-root "${LAKE_DIR}" \
  --through-date "${THROUGH_DATE}" \
  --code-revision "${CODE_REVISION}" \
  > "${REPORT_DIR}/toss-adjusted-ingest.json"

"${PYTHON_BIN}" -m toss_trading.cli.research_collect_toss_reference \
  --universe "data/universe.csv" \
  --output-root "${LAKE_DIR}" \
  --code-revision "${CODE_REVISION}" \
  > "${REPORT_DIR}/toss-reference-collection.json"
json_log "research_provider_ok" "toss" "collected" ""

TIINGO_STATE="skipped_license_or_secret_gate"
if [[ "${RESEARCH_TIINGO_LICENSE_ACCEPTED:-0}" == "1" ]] \
  && load_optional_secret \
    "TIINGO_API_TOKEN" \
    "${TIINGO_API_TOKEN_SECRET:-tiingo-api-token}"; then
  "${PYTHON_BIN}" -m toss_trading.cli.research_collect_tiingo \
    --universe "data/universe.csv" \
    --start-date "${START_DATE}" \
    --end-date "${THROUGH_DATE}" \
    --output-root "${LAKE_DIR}" \
    --code-revision "${CODE_REVISION}" \
    > "${REPORT_DIR}/tiingo-collection.json"
  TIINGO_STATE="collected"
  json_log "research_provider_ok" "tiingo" "collected" ""
else
  json_log \
    "research_provider_skipped" \
    "tiingo" \
    "skipped" \
    "license_acceptance_and_secret_required"
fi
PROVIDER_STATES+=("tiingo=${TIINGO_STATE}")

SEC_STATE="skipped_schedule_or_contact_gate"
if [[ "${RUN_MODE}" == "weekly" && "${RESEARCH_SEC_CONTACT_APPROVED:-0}" == "1" ]] \
  && load_optional_secret \
    "SEC_USER_AGENT" \
    "${SEC_USER_AGENT_SECRET:-sec-user-agent}"; then
  "${PYTHON_BIN}" -m toss_trading.cli.research_collect_sec \
    --instrument-master "data/instrument_master.csv" \
    --include-companyfacts \
    --output-root "${LAKE_DIR}" \
    --code-revision "${CODE_REVISION}" \
    > "${REPORT_DIR}/sec-collection.json"
  SEC_STATE="collected"
  json_log "research_provider_ok" "sec-edgar" "collected" ""
else
  json_log \
    "research_provider_skipped" \
    "sec-edgar" \
    "skipped" \
    "weekly_schedule_and_approved_contact_required"
fi
PROVIDER_STATES+=("sec-edgar=${SEC_STATE}")

FRED_STATE="skipped_rights_or_secret_gate"
if [[ "${RESEARCH_FRED_SERIES_RIGHTS_APPROVED:-0}" == "1" ]] \
  && load_optional_secret \
    "FRED_API_KEY" \
    "${FRED_API_KEY_SECRET:-fred-api-key}"; then
  "${PYTHON_BIN}" -m toss_trading.cli.research_collect_fred \
    --series-registry "config/fred_series.csv" \
    --realtime-start "${REALTIME_START}" \
    --realtime-end "${REALTIME_END}" \
    --output-root "${LAKE_DIR}" \
    --code-revision "${CODE_REVISION}" \
    > "${REPORT_DIR}/fred-collection.json"
  FRED_STATE="collected"
  json_log "research_provider_ok" "fred-alfred" "collected" ""
else
  json_log \
    "research_provider_skipped" \
    "fred-alfred" \
    "skipped" \
    "series_rights_review_and_secret_required"
fi
PROVIDER_STATES+=("fred-alfred=${FRED_STATE}")

if ! "${PYTHON_BIN}" -m toss_trading.cli.research_validate_bars \
  --parquet "${LAKE_DIR}/silver/market_bars/**/*.parquet" \
  --require-adjustment raw \
  --require-adjustment split_adjusted \
  > "${REPORT_DIR}/market-bars-qa.json"; then
  json_log "research_validation_failed" "" "failed" "market_bar_qa"
  exit 65
fi

VERIFY_ARGS=(
  --run-dir "${RUN_DIR}"
  --mode "${RUN_MODE}"
  --code-revision "${CODE_REVISION}"
)
for provider_state in "${PROVIDER_STATES[@]}"; do
  VERIFY_ARGS+=(--provider-state "${provider_state}")
done
if [[ -n "${STRATEGY_EXPERIMENT}" ]]; then
  VERIFY_ARGS+=(--strategy-experiment "${STRATEGY_EXPERIMENT}")
fi
"${PYTHON_BIN}" -m toss_trading.cli.research_automation verify \
  "${VERIFY_ARGS[@]}" \
  > "${RUNTIME_ROOT}/last-verification.json"
"${PYTHON_BIN}" -m toss_trading.cli.research_reporting event \
  --summary "${REPORT_DIR}/reporting-summary.json" \
  >> "${JSON_LOG_PATH}"
json_log "research_validation_ok" "" "passed" ""

if [[ -z "${GCS_URI}" ]]; then
  echo "RESEARCH_GCS_URI is required after local verification" >&2
  exit 66
fi
gcloud storage rsync "${RUN_DIR}" "${GCS_URI%/}/runs/${RUN_ID}" --recursive
gcloud storage cp \
  "${RUN_DIR}/run-status.json" \
  "${GCS_URI%/}/status/latest-${RUN_MODE}.json"
gcloud storage cp \
  "${REPORT_DIR}/reporting-summary.json" \
  "${GCS_URI%/}/reports/latest-${RUN_MODE}.json"
gcloud storage cp \
  "${REPORT_DIR}/visual-report.html" \
  "${GCS_URI%/}/reports/latest-${RUN_MODE}.html"
json_log "research_backup_upload_ok" "" "uploaded" "${GCS_URI%/}/runs/${RUN_ID}"

if ! "${PYTHON_BIN}" -m toss_trading.cli.research_reporting \
  upload-bigquery \
  --summary "${REPORT_DIR}/reporting-summary.json" \
  --project-id "${GCP_PROJECT_ID}" \
  --dataset-id "${BIGQUERY_DATASET}" \
  --table-id "${BIGQUERY_TABLE}" \
  > "${RUNTIME_ROOT}/last-bigquery-upload.json"; then
  json_log \
    "research_reporting_upload_failed" \
    "bigquery" \
    "failed" \
    "${BIGQUERY_DATASET}.${BIGQUERY_TABLE}"
  exit 67
fi
json_log \
  "research_reporting_upload_ok" \
  "bigquery" \
  "inserted" \
  "${BIGQUERY_DATASET}.${BIGQUERY_TABLE}"

ln -sfn "${RUN_DIR}" "${RUNTIME_ROOT}/latest-${RUN_MODE}"
json_log "research_automation_ok" "" "completed" ""
