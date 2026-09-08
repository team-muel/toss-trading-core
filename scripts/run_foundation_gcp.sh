#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PROFILE="${FOUNDATION_AUDIT_PROFILE:-v0-empty-safe}"
DB_PATH="${FOUNDATION_DB_PATH:-runtime/foundation_account_state.sqlite}"
REPORT_PATH="${FOUNDATION_REPORT_PATH:-runtime/foundation_account_state_report.txt}"
JSON_LOG_PATH="${FOUNDATION_JSON_LOG_PATH:-runtime/foundation_runner.jsonl}"
BACKUP_DIR="${FOUNDATION_BACKUP_DIR:-runtime/backups}"
LOCAL_BACKUP_RETENTION_DAYS="${FOUNDATION_LOCAL_BACKUP_RETENTION_DAYS:-14}"
LOCK_PATH="${FOUNDATION_LOCK_PATH:-runtime/foundation_runner.lock}"
TOSS_API_LOCK_PATH="${TOSS_API_LOCK_PATH:-runtime/toss_api.lock}"
BUYING_POWER_CURRENCY="${FOUNDATION_BUYING_POWER_CURRENCY:-USD}"
MAX_ORDER_DETAILS="${FOUNDATION_MAX_ORDER_DETAILS:-1}"
INCLUDE_CLOSED_ORDERS="${FOUNDATION_INCLUDE_CLOSED_ORDERS:-1}"
CLOSED_ORDER_LOOKBACK_DAYS="${FOUNDATION_CLOSED_ORDER_LOOKBACK_DAYS:-7}"
TARGET_ORDER_ID="${FOUNDATION_TARGET_ORDER_ID:-}"
COST_CALIBRATION_PATH="${FOUNDATION_RESEARCH_COST_CALIBRATION_PATH:-runtime/research_execution_cost_calibration.json}"
COST_CALIBRATION_SECRET="${FOUNDATION_RESEARCH_COST_CALIBRATION_SECRET:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
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

mkdir -p \
  "$(dirname "${DB_PATH}")" \
  "$(dirname "${REPORT_PATH}")" \
  "$(dirname "${JSON_LOG_PATH}")" \
  "$(dirname "${LOCK_PATH}")" \
  "$(dirname "${TOSS_API_LOCK_PATH}")" \
  "$(dirname "${COST_CALIBRATION_PATH}")" \
  "${BACKUP_DIR}"

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
  local exit_code="${2:-}"
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  if [[ -n "${exit_code}" ]]; then
    printf '{"ts":"%s","event":"%s","profile":"%s","db":"%s","code_revision":"%s","exit_code":%s}\n' \
      "${ts}" \
      "$(json_escape "${event}")" \
      "$(json_escape "${PROFILE}")" \
      "$(json_escape "${DB_PATH}")" \
      "$(json_escape "${CODE_REVISION}")" \
      "${exit_code}" >> "${JSON_LOG_PATH}"
  else
    printf '{"ts":"%s","event":"%s","profile":"%s","db":"%s","code_revision":"%s"}\n' \
      "${ts}" \
      "$(json_escape "${event}")" \
      "$(json_escape "${PROFILE}")" \
      "$(json_escape "${DB_PATH}")" \
      "$(json_escape "${CODE_REVISION}")" >> "${JSON_LOG_PATH}"
  fi
}

on_error() {
  local exit_code="$?"
  trap - ERR
  json_log "foundation_runner_failed" "${exit_code}"
  exit "${exit_code}"
}

backup_sqlite() {
  local backup_path="$1"
  "${PYTHON_BIN}" - "${DB_PATH}" "${backup_path}" <<'PY'
import pathlib
import sqlite3
import sys

source_path, backup_path = sys.argv[1:3]
pathlib.Path(backup_path).parent.mkdir(parents=True, exist_ok=True)
source = sqlite3.connect(source_path)
target = sqlite3.connect(backup_path)
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
}

trap on_error ERR

if ! command -v flock >/dev/null 2>&1; then
  json_log "foundation_runner_failed" "70"
  echo "flock is required to prevent overlapping foundation runner executions" >&2
  exit 70
fi

exec 9>"${LOCK_PATH}"
if ! flock -n 9; then
  json_log "foundation_runner_lock_busy"
  exit 0
fi
exec 8>"${TOSS_API_LOCK_PATH}"
if ! flock -n 8; then
  json_log "foundation_runner_lock_busy"
  exit 0
fi

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

export PYTHONPATH="${PYTHONPATH:-src}"

if [[ "${FOUNDATION_LOAD_GCP_SECRETS:-0}" == "1" ]]; then
  # shellcheck disable=SC1091
  source "scripts/load_gcp_secrets.sh"
fi

json_log "foundation_runner_start"

SNAPSHOT_ARGS=(
  --db "${DB_PATH}"
  --report "${REPORT_PATH}"
  --buying-power-currency "${BUYING_POWER_CURRENCY}"
  --max-order-details "${MAX_ORDER_DETAILS}"
  --closed-order-lookback-days "${CLOSED_ORDER_LOOKBACK_DAYS}"
  --json-log "${JSON_LOG_PATH}"
  --code-revision "${CODE_REVISION}"
)
if [[ -n "${TARGET_ORDER_ID}" ]]; then
  SNAPSHOT_ARGS+=(--target-order-id "${TARGET_ORDER_ID}")
fi
if [[ "${INCLUDE_CLOSED_ORDERS}" == "1" ]]; then
  SNAPSHOT_ARGS+=(--include-closed-orders)
fi

"${PYTHON_BIN}" -m toss_trading.cli.foundation_snapshot "${SNAPSHOT_ARGS[@]}"

"${PYTHON_BIN}" -m toss_trading.cli.foundation_audit \
  --db "${DB_PATH}" \
  --profile "${PROFILE}" \
  --json-log "${JSON_LOG_PATH}"

"${PYTHON_BIN}" -m toss_trading.cli.research_export_cost_model \
  --db "${DB_PATH}" \
  --output "${COST_CALIBRATION_PATH}" \
  --policy "${ROOT_DIR}/config/research_validation_protocol.json" \
  --as-of "$(date -u +%F)" \
  > "${COST_CALIBRATION_PATH}.status.json"
json_log "foundation_research_cost_calibration_ok"
if [[ -n "${COST_CALIBRATION_SECRET}" ]]; then
  PUBLISHED_DATE_PATH="${COST_CALIBRATION_PATH}.published-date"
  CURRENT_CALIBRATION_DATE="$(date -u +%F)"
  PUBLISHED_DATE=""
  if [[ -f "${PUBLISHED_DATE_PATH}" ]]; then
    PUBLISHED_DATE="$(<"${PUBLISHED_DATE_PATH}")"
  fi
  if [[ "${PUBLISHED_DATE}" != "${CURRENT_CALIBRATION_DATE}" ]]; then
    gcloud secrets versions add "${COST_CALIBRATION_SECRET}" \
      --project="${GCP_PROJECT_ID}" \
      --data-file="${COST_CALIBRATION_PATH}" >/dev/null
    printf '%s\n' "${CURRENT_CALIBRATION_DATE}" > "${PUBLISHED_DATE_PATH}.tmp"
    mv -f "${PUBLISHED_DATE_PATH}.tmp" "${PUBLISHED_DATE_PATH}"
    json_log "foundation_research_cost_calibration_published"
  fi
fi

BACKUP_TS="$(date -u +"%Y%m%dT%H%M%SZ")"
BACKUP_PATH="${BACKUP_DIR}/foundation_account_state_${PROFILE}_${BACKUP_TS}.sqlite"
backup_sqlite "${BACKUP_PATH}"
json_log "foundation_runner_backup_ok"

if [[ -n "${FOUNDATION_GCS_BACKUP_URI:-}" ]]; then
  gcloud storage cp "${BACKUP_PATH}" \
    "${FOUNDATION_GCS_BACKUP_URI%/}/$(basename "${BACKUP_PATH}")"
  json_log "foundation_runner_backup_upload_ok"
fi

if [[ "${LOCAL_BACKUP_RETENTION_DAYS}" =~ ^[0-9]+$ ]]; then
  find "${BACKUP_DIR}" -type f \
    -name 'foundation_account_state_*.sqlite' \
    -mtime "+${LOCAL_BACKUP_RETENTION_DAYS}" \
    -delete
else
  echo "FOUNDATION_LOCAL_BACKUP_RETENTION_DAYS must be a nonnegative integer" >&2
  exit 64
fi

json_log "foundation_runner_ok"
