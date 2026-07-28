#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="${TOSS_HISTORY_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUTPUT_DIR="${TOSS_HISTORY_OUTPUT_DIR:-${ROOT_DIR}/output}"
CURRENT_RELEASE="${FOUNDATION_CURRENT_RELEASE:-/home/seoje/toss-trading/current}"
SECRET_LOADER="${TOSS_SECRET_LOADER:-${CURRENT_RELEASE}/scripts/load_gcp_secrets.sh}"
TOSS_API_LOCK_PATH="${TOSS_API_LOCK_PATH:-/home/seoje/toss-trading/runtime/toss_api.lock}"

mkdir -p "${OUTPUT_DIR}"
rm -f \
  "${OUTPUT_DIR}/DONE" \
  "${OUTPUT_DIR}/EXIT_CODE" \
  "${OUTPUT_DIR}/SHA256SUMS"
: > "${OUTPUT_DIR}/RUNNING"

on_exit() {
  local status="$?"
  printf '%s\n' "${status}" > "${OUTPUT_DIR}/EXIT_CODE"
  rm -f "${OUTPUT_DIR}/RUNNING"
  printf 'toss_history_collector_exit status=%s\n' "${status}" >&2
}
trap on_exit EXIT

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

if ! command -v flock >/dev/null 2>&1; then
  printf 'toss_history_collector_error=flock_missing\n' >&2
  exit 70
fi
mkdir -p "$(dirname "${TOSS_API_LOCK_PATH}")"
exec 8>"${TOSS_API_LOCK_PATH}"
if ! flock -n 8; then
  printf 'toss_history_collector_error=toss_api_lock_busy\n' >&2
  exit 75
fi

# shellcheck disable=SC1090
source "${SECRET_LOADER}"

printf 'toss_history_collector_stage=credentials_loaded root=%s\n' "${ROOT_DIR}" >&2
if [[ ! -f "${ROOT_DIR}/src/toss_trading/__init__.py" ]]; then
  printf 'toss_history_collector_error=python_source_missing\n' >&2
  exit 3
fi

printf 'toss_history_collector_stage=raw_start\n' >&2
PYTHONPATH="${ROOT_DIR}/src" python3 -m toss_trading.cli.research_collect_toss collect \
  --universe "${ROOT_DIR}/data/universe.csv" \
  --start-date "${TOSS_HISTORY_START_DATE:-2004-01-01}" \
  --max-pages "${TOSS_HISTORY_MAX_PAGES:-100}" \
  --skip-unavailable-symbols \
  --raw \
  --output "${OUTPUT_DIR}/toss-candles-raw.json"

printf 'toss_history_collector_stage=adjusted_start\n' >&2
PYTHONPATH="${ROOT_DIR}/src" python3 -m toss_trading.cli.research_collect_toss collect \
  --universe "${ROOT_DIR}/data/universe.csv" \
  --start-date "${TOSS_HISTORY_START_DATE:-2004-01-01}" \
  --max-pages "${TOSS_HISTORY_MAX_PAGES:-100}" \
  --skip-unavailable-symbols \
  --adjusted \
  --output "${OUTPUT_DIR}/toss-candles-adjusted.json"

sha256sum \
  "${OUTPUT_DIR}/toss-candles-raw.json" \
  "${OUTPUT_DIR}/toss-candles-adjusted.json" \
  > "${OUTPUT_DIR}/SHA256SUMS"
touch "${OUTPUT_DIR}/DONE"
