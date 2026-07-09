#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PROFILE="${FOUNDATION_AUDIT_PROFILE:-v0-empty-safe}"
DB_PATH="${FOUNDATION_DB_PATH:-runtime/foundation_account_state.sqlite}"
REPORT_PATH="${FOUNDATION_REPORT_PATH:-runtime/foundation_account_state_report.txt}"
JSON_LOG_PATH="${FOUNDATION_JSON_LOG_PATH:-runtime/foundation_runner.jsonl}"
BUYING_POWER_CURRENCY="${FOUNDATION_BUYING_POWER_CURRENCY:-USD}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "$(dirname "${DB_PATH}")" "$(dirname "${REPORT_PATH}")" "$(dirname "${JSON_LOG_PATH}")"

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

export PYTHONPATH="${PYTHONPATH:-src}"

SECRET_ARGS=()
if [[ "${FOUNDATION_LOAD_GCP_SECRETS:-0}" == "1" ]]; then
  SECRET_ARGS+=(--load-gcp-secrets)
  if [[ -n "${GCP_PROJECT_ID:-}" ]]; then
    SECRET_ARGS+=(--gcp-project-id "${GCP_PROJECT_ID}")
  fi
fi

echo "{\"event\":\"foundation_runner_start\",\"profile\":\"${PROFILE}\",\"db\":\"${DB_PATH}\"}" >> "${JSON_LOG_PATH}"

"${PYTHON_BIN}" -m toss_trading.cli.foundation_snapshot \
  --db "${DB_PATH}" \
  --report "${REPORT_PATH}" \
  --buying-power-currency "${BUYING_POWER_CURRENCY}" \
  --json-log "${JSON_LOG_PATH}" \
  "${SECRET_ARGS[@]}"

"${PYTHON_BIN}" -m toss_trading.cli.foundation_audit \
  --db "${DB_PATH}" \
  --profile "${PROFILE}" \
  --json-log "${JSON_LOG_PATH}"

echo "{\"event\":\"foundation_runner_ok\",\"profile\":\"${PROFILE}\",\"db\":\"${DB_PATH}\"}" >> "${JSON_LOG_PATH}"
