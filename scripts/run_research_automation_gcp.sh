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
PROSPECTIVE_OBSERVATION_LEDGER="${RESEARCH_PROSPECTIVE_OBSERVATION_LEDGER:-${RUNTIME_ROOT}/prospective_collection_observations.jsonl}"
EXECUTION_COST_CALIBRATION="${RESEARCH_EXECUTION_COST_CALIBRATION:-/home/seoje/toss-trading/runtime/research_execution_cost_calibration.json}"
: "${RESEARCH_PORTFOLIO_NOTIONAL_USD:?RESEARCH_PORTFOLIO_NOTIONAL_USD is required}"
PORTFOLIO_NOTIONAL_USD="${RESEARCH_PORTFOLIO_NOTIONAL_USD}"
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
  echo "immutable code revision could not be resolved" >&2
  exit 78
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
export GCP_PROJECT_ID
export TOSS_CLIENT_ID_SECRET
export TOSS_CLIENT_SECRET_SECRET

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
  value="${value%$'\r'}"
  if [[ "${value}" == *$'\r'* || "${value}" == *$'\n'* ]]; then
    echo \
      "optional_secret_rejected env=${env_name} reason=invalid_control_character" \
      >&2
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

"${PYTHON_BIN}" -m toss_trading.cli.research_validate_instruments \
  --universe "data/universe.csv" \
  --instrument-master "data/instrument_master.csv" \
  --instrument-history "data/instrument_history.csv" \
  --corporate-actions "data/corporate_actions.csv" \
  --as-of "$(date -u +%F)" \
  > "${REPORT_DIR}/instrument-identity-qa.json"
json_log "research_instrument_identity_ok" "" "passed" ""

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
flock -u 8

TIINGO_STATE="skipped_license_or_secret_gate"
TIINGO_START_DATE="${START_DATE}"
if [[ "${RUN_MODE}" == "daily" ]]; then
  # Daily research needs the same verified long history as the weekly audit.
  # Only Toss/FRED remain incremental; 15 Tiingo requests still cover the
  # bounded universe, while the response range makes daily backtests possible.
  TIINGO_START_DATE="2004-01-01"
fi
if [[ "${RESEARCH_TIINGO_LICENSE_ACCEPTED:-0}" == "1" ]] \
  && load_optional_secret \
    "TIINGO_API_TOKEN" \
    "${TIINGO_API_TOKEN_SECRET:-tiingo-api-token}"; then
  "${PYTHON_BIN}" -m toss_trading.cli.research_collect_tiingo \
    --universe "data/universe.csv" \
    --instrument-master "data/instrument_master.csv" \
    --start-date "${TIINGO_START_DATE}" \
    --end-date "${THROUGH_DATE}" \
    --output-root "${LAKE_DIR}" \
    --observation-ledger "${PROSPECTIVE_OBSERVATION_LEDGER}" \
    --run-id "${RUN_ID}" \
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

MARKET_DATA_ADVANCED="0"
CURRENT_MARKET_DATE=""
PREVIOUS_MARKET_DATE=""
if [[ "${TIINGO_STATE}" == "collected" ]]; then
  CURRENT_MARKET_DATE="$(
    "${PYTHON_BIN}" -c \
      'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["complete_through_date"])' \
      "${REPORT_DIR}/tiingo-collection.json"
  )"
  PREVIOUS_TIINGO_REPORT="${RUNTIME_ROOT}/latest-daily/reports/tiingo-collection.json"
  if [[ -f "${PREVIOUS_TIINGO_REPORT}" ]]; then
    PREVIOUS_MARKET_DATE="$(
      "${PYTHON_BIN}" -c \
        'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["complete_through_date"])' \
        "${PREVIOUS_TIINGO_REPORT}"
    )"
  fi
  if [[ -z "${PREVIOUS_MARKET_DATE}" \
    || "${CURRENT_MARKET_DATE}" > "${PREVIOUS_MARKET_DATE}" ]]; then
    MARKET_DATA_ADVANCED="1"
  fi
  json_log \
    "research_market_data_checkpoint" \
    "tiingo" \
    "$([[ "${MARKET_DATA_ADVANCED}" == "1" ]] && echo advanced || echo unchanged)" \
    "${PREVIOUS_MARKET_DATE:-none}_to_${CURRENT_MARKET_DATE}"
fi

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
    --cache-dir "${RUNTIME_ROOT}/fred-vintage-cache" \
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

BAR_VALIDATION_ARGS=(
  --parquet "${LAKE_DIR}/silver/market_bars/**/*.parquet"
  --data-source-policy "${ROOT_DIR}/config/data_sources.yaml"
  --require-adjustment raw
  --require-adjustment split_adjusted
)
if [[ "${TIINGO_STATE}" == "collected" ]]; then
  BAR_VALIDATION_ARGS+=(
    --require-adjustment total_return
    --cross-provider-source toss-openapi
    --cross-provider-source tiingo-eod
  )
fi
if ! "${PYTHON_BIN}" -m toss_trading.cli.research_validate_bars \
  "${BAR_VALIDATION_ARGS[@]}" \
  > "${REPORT_DIR}/market-bars-qa.json"; then
  json_log "research_validation_failed" "" "failed" "market_bar_qa"
  exit 65
fi

HYPOTHESIS_PLAN_RESULT=""
HYPOTHESIS_EVALUATION_RESULT=""
if [[ "${TIINGO_STATE}" == "collected" \
  && "${RESEARCH_AUTONOMOUS_PLANNING_ENABLED:-1}" == "1" ]]; then
  HYPOTHESIS_PLAN_RESULT="${REPORT_DIR}/hypothesis-plan.json"
  PLAN_LIMIT="0"
  if [[ "${RUN_MODE}" == "daily" \
    && "${MARKET_DATA_ADVANCED}" == "1" ]]; then
    PLAN_LIMIT="1"
  fi
  if [[ -n "${RESEARCH_HYPOTHESIS_MAX_NEW_OVERRIDE:-}" ]]; then
    PLAN_LIMIT="${RESEARCH_HYPOTHESIS_MAX_NEW_OVERRIDE}"
  fi
  if "${PYTHON_BIN}" -m toss_trading.cli.research_plan_hypotheses \
    --policy "${ROOT_DIR}/config/autonomous_research_policy.json" \
    --universe "${ROOT_DIR}/data/universe.csv" \
    --ledger-dir "${RUNTIME_ROOT}/hypothesis-ledger" \
    --output-dir "${LAKE_DIR}/gold/hypotheses" \
    --project-id "${GCP_PROJECT_ID}" \
    --location "${RESEARCH_INTERPRETATION_LOCATION:-global}" \
    --model "${RESEARCH_HYPOTHESIS_MODEL:-gemini-3.1-flash-lite}" \
    --max-new "${PLAN_LIMIT}" \
    --result "${HYPOTHESIS_PLAN_RESULT}" \
    > "${RUNTIME_ROOT}/last-hypothesis-plan.json"; then
    json_log "research_hypothesis_planning_ok" "vertex-ai" "completed" ""
  else
    json_log \
      "research_hypothesis_planning_failed" \
      "vertex-ai" \
      "failed" \
      "bounded_hypothesis_planner"
  fi
fi

if [[ ( "${RUN_MODE}" == "weekly" \
    || "${MARKET_DATA_ADVANCED}" == "1" ) \
  && "${TIINGO_STATE}" == "collected" \
  && "${RESEARCH_AUTONOMOUS_PLANNING_ENABLED:-1}" == "1" ]]; then
  if [[ ! -f "${EXECUTION_COST_CALIBRATION}" ]]; then
    json_log \
      "research_cost_calibration_failed" \
      "toss-account-commission" \
      "failed" \
      "sanitized_cost_calibration_missing"
    exit 65
  fi
  HYPOTHESIS_EVALUATION_RESULT="${REPORT_DIR}/hypothesis-evaluation.json"
  PROSPECTIVE_CUTOFF_ARGS=()
  if [[ -n "${PREVIOUS_MARKET_DATE}" ]]; then
    PROSPECTIVE_CUTOFF_ARGS=(--prospective-cutoff "${PREVIOUS_MARKET_DATE}")
  fi
  if "${PYTHON_BIN}" -m toss_trading.cli.research_evaluate_hypotheses \
    --policy "${ROOT_DIR}/config/autonomous_research_policy.json" \
    --ledger-dir "${RUNTIME_ROOT}/hypothesis-ledger" \
    --output-dir "${LAKE_DIR}/gold/hypothesis_evaluations" \
    --run-id "${RUN_ID}" \
    --cadence "${RUN_MODE}" \
    "${PROSPECTIVE_CUTOFF_ARGS[@]}" \
    --parquet "${LAKE_DIR}/silver/market_bars/**/*.parquet" \
    --manifest-root "${LAKE_DIR}/catalog/manifests" \
    --code-revision "${CODE_REVISION}" \
    --instrument-master "${ROOT_DIR}/data/instrument_master.csv" \
    --cost-calibration "${EXECUTION_COST_CALIBRATION}" \
    --portfolio-notional-usd "${PORTFOLIO_NOTIONAL_USD}" \
    --result "${HYPOTHESIS_EVALUATION_RESULT}" \
    > "${RUNTIME_ROOT}/last-hypothesis-evaluation.json"; then
    json_log "research_hypothesis_evaluation_ok" "candidate-engine" "completed" ""
  else
    HYPOTHESIS_EVALUATION_RESULT=""
    json_log \
      "research_hypothesis_evaluation_failed" \
      "candidate-engine" \
      "failed" \
      "historical_candidate_evaluation"
  fi
fi

if [[ -z "${STRATEGY_EXPERIMENT}" \
  && ( "${RUN_MODE}" == "weekly" \
    || "${MARKET_DATA_ADVANCED}" == "1" ) \
  && "${TIINGO_STATE}" == "collected" ]]; then
  STRATEGY_CUTOFF_ARGS=()
  if [[ -n "${PREVIOUS_MARKET_DATE}" ]]; then
    STRATEGY_CUTOFF_ARGS=(--as-of-date "${PREVIOUS_MARKET_DATE}")
  fi
  "${PYTHON_BIN}" -m toss_trading.cli.research_backtest \
    --parquet "${LAKE_DIR}/silver/market_bars/**/*.parquet" \
    "${STRATEGY_CUTOFF_ARGS[@]}" \
    --candidate SPY \
    --candidate QQQ \
    --candidate VTV \
    --candidate XLP \
    --candidate XLU \
    --candidate TLT \
    --candidate GLD \
    --cash-symbol SGOV \
    --manifest-root "${LAKE_DIR}/catalog/manifests" \
    --align-common-history \
    --instrument-master "${ROOT_DIR}/data/instrument_master.csv" \
    --cost-calibration "${EXECUTION_COST_CALIBRATION}" \
    --portfolio-notional-usd "${PORTFOLIO_NOTIONAL_USD}" \
    --validation-protocol \
    "${ROOT_DIR}/config/research_validation_protocol.json" \
    --prospective-observation-ledger \
    "${PROSPECTIVE_OBSERVATION_LEDGER}" \
    --output-root "${LAKE_DIR}" \
    --code-revision "${CODE_REVISION}" \
    > "${REPORT_DIR}/strategy-backtest.json"
  STRATEGY_EXPERIMENT="$(
    "${PYTHON_BIN}" -c \
      'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["experiment_record"])' \
      "${REPORT_DIR}/strategy-backtest.json"
  )"
  if [[ -z "${STRATEGY_EXPERIMENT}" \
    || ! -f "${STRATEGY_EXPERIMENT}" ]]; then
    json_log \
      "research_strategy_artifact_failed" \
      "broad_etf_dual_momentum_v1" \
      "failed" \
      "experiment_artifact_missing"
    exit 65
  fi
  json_log \
    "research_strategy_artifact_ok" \
    "broad_etf_dual_momentum_v1" \
    "available" \
    "$(basename "${STRATEGY_EXPERIMENT}" .json)"
elif [[ -z "${STRATEGY_EXPERIMENT}" ]]; then
  json_log \
    "research_strategy_artifact_skipped" \
    "broad_etf_dual_momentum_v1" \
    "skipped" \
    "new_total_return_market_date_required"
fi

VERIFY_ARGS=(
  --run-dir "${RUN_DIR}"
  --mode "${RUN_MODE}"
  --code-revision "${CODE_REVISION}"
  --data-source-policy "${ROOT_DIR}/config/data_sources.yaml"
)
if [[ -f "${REPORT_DIR}/tiingo-collection.json" ]]; then
  VERIFY_ARGS+=(--tiingo-collection "${REPORT_DIR}/tiingo-collection.json")
fi
for provider_state in "${PROVIDER_STATES[@]}"; do
  VERIFY_ARGS+=(--provider-state "${provider_state}")
done
if [[ -n "${STRATEGY_EXPERIMENT}" ]]; then
  VERIFY_ARGS+=(--strategy-experiment "${STRATEGY_EXPERIMENT}")
fi
if [[ -n "${HYPOTHESIS_PLAN_RESULT}" \
  && -f "${HYPOTHESIS_PLAN_RESULT}" ]]; then
  VERIFY_ARGS+=(--hypothesis-plan "${HYPOTHESIS_PLAN_RESULT}")
fi
if [[ -n "${HYPOTHESIS_EVALUATION_RESULT}" \
  && -f "${HYPOTHESIS_EVALUATION_RESULT}" ]]; then
  VERIFY_ARGS+=(--hypothesis-evaluation "${HYPOTHESIS_EVALUATION_RESULT}")
fi
"${PYTHON_BIN}" -m toss_trading.cli.research_automation verify \
  "${VERIFY_ARGS[@]}" \
  > "${RUNTIME_ROOT}/last-verification.json"
"${PYTHON_BIN}" -m toss_trading.cli.research_reporting event \
  --summary "${REPORT_DIR}/reporting-summary.json" \
  >> "${JSON_LOG_PATH}"
mapfile -t STRATEGY_GATE < <(
  "${PYTHON_BIN}" -c \
    'import json,sys; s=json.load(open(sys.argv[1], encoding="utf-8")).get("strategy", {}); print(s.get("artifact_state", s.get("state", "not_available"))); print(s.get("methodology_state", "incomplete")); print(s.get("promotion_state", "blocked")); print(s.get("promotion_reason") or "")' \
    "${REPORT_DIR}/reporting-summary.json"
)
STRATEGY_ARTIFACT_STATE="${STRATEGY_GATE[0]:-not_available}"
STRATEGY_METHODOLOGY_STATE="${STRATEGY_GATE[1]:-incomplete}"
STRATEGY_PROMOTION_STATE="${STRATEGY_GATE[2]:-blocked}"
STRATEGY_PROMOTION_REASON="${STRATEGY_GATE[3]:-unknown}"
if [[ "${STRATEGY_ARTIFACT_STATE}" == "available" \
  && "${STRATEGY_METHODOLOGY_STATE}" == "collecting" \
  && "${STRATEGY_PROMOTION_STATE}" == "blocked" ]]; then
  json_log \
    "research_strategy_promotion_pending" \
    "broad_etf_dual_momentum_v1" \
    "collecting" \
    "${STRATEGY_PROMOTION_REASON}"
elif [[ "${STRATEGY_ARTIFACT_STATE}" == "available" \
  && "${STRATEGY_PROMOTION_STATE}" == "blocked" ]]; then
  json_log \
    "research_strategy_promotion_blocked" \
    "broad_etf_dual_momentum_v1" \
    "blocked" \
    "${STRATEGY_PROMOTION_REASON}"
elif [[ "${STRATEGY_PROMOTION_STATE}" == "eligible" ]]; then
  json_log \
    "research_strategy_promotion_eligible" \
    "broad_etf_dual_momentum_v1" \
    "eligible" \
    ""
fi
json_log "research_validation_ok" "" "passed" ""

if [[ -z "${GCS_URI}" ]]; then
  echo "RESEARCH_GCS_URI is required after local verification" >&2
  exit 66
fi
"${PYTHON_BIN}" -m toss_trading.cli.research_upload_gcs \
  --source-dir "${RUN_DIR}" \
  --destination-uri "${GCS_URI%/}/runs/${RUN_ID}" \
  --workers 16 \
  --alias \
    "${RUN_DIR}/run-status.json=${GCS_URI%/}/status/${RUN_ID}.json" \
  --alias \
    "${REPORT_DIR}/reporting-summary.json=${GCS_URI%/}/reports/${RUN_ID}.json" \
  --alias \
    "${REPORT_DIR}/visual-report.html=${GCS_URI%/}/reports/${RUN_ID}.html" \
  > "${RUNTIME_ROOT}/last-gcs-upload.json"
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
  json_log "research_automation_failed" "bigquery" "failed" "reporting_upload"
  exit 67
fi
json_log \
  "research_reporting_upload_ok" \
  "bigquery" \
  "inserted" \
  "${BIGQUERY_DATASET}.${BIGQUERY_TABLE}"

SEND_RESEARCH_EMAIL="1"
if [[ "${RUN_MODE}" == "daily" \
  && "${MARKET_DATA_ADVANCED}" != "1" \
  && "${RESEARCH_EMAIL_FORCE:-0}" != "1" ]]; then
  SEND_RESEARCH_EMAIL="0"
fi
if [[ "${RESEARCH_EMAIL_ENABLED:-0}" == "1" \
  && "${SEND_RESEARCH_EMAIL}" == "1" ]]; then
  if ! load_optional_secret \
      "GMAIL_OAUTH_CLIENT_ID" \
      "${GMAIL_OAUTH_CLIENT_ID_SECRET:-toss-research-gmail-oauth-client-id}" \
    || ! load_optional_secret \
      "GMAIL_OAUTH_CLIENT_SECRET" \
      "${GMAIL_OAUTH_CLIENT_SECRET_SECRET:-toss-research-gmail-oauth-client-secret}" \
    || ! load_optional_secret \
      "GMAIL_OAUTH_REFRESH_TOKEN" \
      "${GMAIL_OAUTH_REFRESH_TOKEN_SECRET:-toss-research-gmail-oauth-refresh-token}"; then
    json_log \
      "research_email_failed" \
      "gmail" \
      "failed" \
      "oauth_secret_missing"
    json_log "research_automation_failed" "gmail" "failed" "email_delivery"
    exit 68
  fi
  export GMAIL_OAUTH_CLIENT_ID
  export GMAIL_OAUTH_CLIENT_SECRET
  export GMAIL_OAUTH_REFRESH_TOKEN
  PREVIOUS_SUMMARY_ARGS=()
  PREVIOUS_SUMMARY_PATH="${RUNTIME_ROOT}/latest-${RUN_MODE}/reports/reporting-summary.json"
  if [[ -f "${PREVIOUS_SUMMARY_PATH}" ]]; then
    PREVIOUS_SUMMARY_ARGS=(--previous-summary "${PREVIOUS_SUMMARY_PATH}")
  fi
  INTERPRETATION_DIR="${RUNTIME_ROOT}/interpretations"
  INTERPRETATION_PATH="${INTERPRETATION_DIR}/${RUN_ID}.json"
  mkdir -p "${INTERPRETATION_DIR}"
  if ! "${PYTHON_BIN}" -m toss_trading.cli.research_reporting \
    interpret \
    --summary "${REPORT_DIR}/reporting-summary.json" \
    "${PREVIOUS_SUMMARY_ARGS[@]}" \
    --output "${INTERPRETATION_PATH}" \
    --project-id "${GCP_PROJECT_ID}" \
    --location "${RESEARCH_INTERPRETATION_LOCATION:-global}" \
    --model "${RESEARCH_INTERPRETATION_MODEL:-gemini-3.1-flash-lite}" \
    > "${RUNTIME_ROOT}/last-interpretation.json"; then
    json_log \
      "research_interpretation_failed" \
      "vertex-ai" \
      "failed" \
      "interpretation_artifact"
    json_log "research_automation_failed" "vertex-ai" "failed" "interpretation"
    exit 68
  fi
  INTERPRETATION_SOURCE="$(
    "${PYTHON_BIN}" -c \
      'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["source"])' \
      "${INTERPRETATION_PATH}"
  )"
  if [[ "${INTERPRETATION_SOURCE}" == "vertex_ai" ]]; then
    json_log "research_interpretation_ok" "vertex-ai" "interpreted" ""
  else
    json_log \
      "research_interpretation_failed" \
      "vertex-ai" \
      "fallback" \
      "fact_only_report"
  fi
  if ! "${PYTHON_BIN}" -m toss_trading.cli.research_reporting \
    email \
    --summary "${REPORT_DIR}/reporting-summary.json" \
    "${PREVIOUS_SUMMARY_ARGS[@]}" \
    --interpretation "${INTERPRETATION_PATH}" \
    --sender "${RESEARCH_EMAIL_SENDER:?RESEARCH_EMAIL_SENDER is required}" \
    --recipient "${RESEARCH_EMAIL_RECIPIENT:?RESEARCH_EMAIL_RECIPIENT is required}" \
    --delivery-ledger "${RUNTIME_ROOT}/research_email.sqlite" \
    > "${RUNTIME_ROOT}/last-email-delivery.json"; then
    json_log "research_email_failed" "gmail" "failed" "gmail_api_delivery"
    json_log "research_automation_failed" "gmail" "failed" "email_delivery"
    exit 68
  fi
  json_log "research_email_ok" "gmail" "sent_or_already_sent" ""
elif [[ "${RESEARCH_EMAIL_ENABLED:-0}" == "1" ]]; then
  json_log \
    "research_email_skipped" \
    "gmail" \
    "no_material_change" \
    "market_data_complete_through_${CURRENT_MARKET_DATE:-unknown}"
else
  json_log "research_email_skipped" "gmail" "disabled" "oauth_not_configured"
fi

"${PYTHON_BIN}" -m toss_trading.cli.research_record_observation \
  --ledger "${PROSPECTIVE_OBSERVATION_LEDGER}" \
  --run-id "${RUN_ID}" \
  --code-revision "${CODE_REVISION}" \
  > "${REPORT_DIR}/prospective-observation-commit.json"
ln -sfn "${RUN_DIR}" "${RUNTIME_ROOT}/latest-${RUN_MODE}"
json_log "research_automation_ok" "" "completed" ""
if [[ "${RUN_MODE}" == "weekly" ]]; then
  json_log "research_weekly_automation_ok" "" "completed" ""
elif [[ "${RUN_MODE}" == "daily" ]]; then
  WEEKLY_SUMMARY="${RUNTIME_ROOT}/latest-weekly/reports/reporting-summary.json"
  if ! "${PYTHON_BIN}" -c \
    'import datetime as d,json,sys; p=json.load(open(sys.argv[1], encoding="utf-8")); t=d.datetime.fromisoformat(p["verified_at"].replace("Z","+00:00")); raise SystemExit(0 if d.datetime.now(d.timezone.utc)-t <= d.timedelta(days=8) else 1)' \
    "${WEEKLY_SUMMARY}" 2>/dev/null; then
    json_log \
      "research_weekly_stale" \
      "weekly" \
      "stale" \
      "no_successful_weekly_run_within_eight_days"
  fi
fi
