#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
RESEARCH_RUNTIME_ROOT="${RESEARCH_RUNTIME_ROOT:-/home/seoje/toss-trading/research-runtime}"
PAPER_RUNTIME_ROOT="${PAPER_RUNTIME_ROOT:-/home/seoje/toss-trading/paper-runtime}"
LOCK_PATH="${PAPER_LOCK_PATH:-${PAPER_RUNTIME_ROOT}/paper_operation.lock}"
JSON_LOG_PATH="${PAPER_JSON_LOG_PATH:-${PAPER_RUNTIME_ROOT}/paper_operation.jsonl}"
CALIBRATION_PATH="${RESEARCH_EXECUTION_COST_CALIBRATION:-/home/seoje/toss-trading/runtime/research_execution_cost_calibration.json}"
CALIBRATION_SECRET="${RESEARCH_EXECUTION_COST_CALIBRATION_SECRET:-}"
LATEST_DAILY="${RESEARCH_RUNTIME_ROOT}/latest-daily"

mkdir -p "${PAPER_RUNTIME_ROOT}/input" "${PAPER_RUNTIME_ROOT}/reports" \
  "$(dirname "${LOCK_PATH}")" "$(dirname "${JSON_LOG_PATH}")"

json_log() {
  local event="$1"
  local state="${2:-}"
  local reason="${3:-}"
  printf '{"ts":"%s","event":"%s","state":"%s","reason":"%s"}\n' \
    "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "${event}" "${state}" "${reason}" \
    >> "${JSON_LOG_PATH}"
}

on_error() {
  local exit_code="$?"
  trap - ERR
  json_log "paper_operation_failed" "failed" "exit_code_${exit_code}"
  exit "${exit_code}"
}
trap on_error ERR

if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required" >&2
  exit 70
fi
exec 9>"${LOCK_PATH}"
if ! flock -n 9; then
  json_log "paper_operation_lock_busy" "skipped" "overlap_prevented"
  exit 0
fi

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi
export PYTHONPATH="${PYTHONPATH:-src}"

if [[ ! -f "${LATEST_DAILY}/reports/reporting-summary.json" ]]; then
  echo "latest completed daily research summary is missing" >&2
  exit 78
fi
mapfile -t artifacts < <(
  find -L "${LATEST_DAILY}/lake/gold/experiments" \
    -mindepth 1 -maxdepth 1 -type f -name '*.json' -print
)
if [[ "${#artifacts[@]}" -ne 1 ]]; then
  echo "paper operation requires exactly one baseline artifact" >&2
  exit 78
fi

if [[ -n "${CALIBRATION_SECRET}" ]]; then
  : "${GCP_PROJECT_ID:?GCP_PROJECT_ID is required to load calibration}"
  temporary="${CALIBRATION_PATH}.tmp"
  gcloud secrets versions access latest \
    --project="${GCP_PROJECT_ID}" \
    --secret="${CALIBRATION_SECRET}" > "${temporary}"
  chmod 0600 "${temporary}"
  mv -f "${temporary}" "${CALIBRATION_PATH}"
fi
if [[ ! -f "${CALIBRATION_PATH}" ]]; then
  echo "sanitized execution cost calibration is missing" >&2
  exit 78
fi

source_run_id="$(
  "${PYTHON_BIN}" - "${LATEST_DAILY}/reports/reporting-summary.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["run_id"])
PY
)"
output="${PAPER_RUNTIME_ROOT}/reports/${source_run_id}.json"
json_log "paper_operation_start" "running" "${source_run_id}"
"${PYTHON_BIN}" -m toss_trading.cli.paper_operation \
  --strategy-artifact "${artifacts[0]}" \
  --source-summary "${LATEST_DAILY}/reports/reporting-summary.json" \
  --lake-root "${LATEST_DAILY}/lake" \
  --paper-db "${PAPER_RUNTIME_ROOT}/paper_broker.sqlite" \
  --planner-ledger "${PAPER_RUNTIME_ROOT}/paper_planner.sqlite" \
  --cost-calibration "${CALIBRATION_PATH}" \
  --output "${output}" >/dev/null
ln -sfn "reports/${source_run_id}.json" "${PAPER_RUNTIME_ROOT}/latest-report.tmp"
mv -Tf "${PAPER_RUNTIME_ROOT}/latest-report.tmp" "${PAPER_RUNTIME_ROOT}/latest-report"
json_log "paper_operation_ok" "complete" "${source_run_id}"
