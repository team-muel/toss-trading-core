#!/usr/bin/env bash
# Load Toss runtime secrets from GCP Secret Manager into the current shell.
#
# Usage:
#   source scripts/load_gcp_secrets.sh
#
# This script must be sourced. If it is executed as a child process, exported
# values disappear when the script exits.

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "load_gcp_secrets.sh must be sourced: source scripts/load_gcp_secrets.sh" >&2
  exit 2
fi

set -Eeuo pipefail

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID is required}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI is required to load GCP Secret Manager values" >&2
  return 2
fi

SECRET_VERSION="${GCP_SECRET_VERSION:-latest}"
OVERWRITE="${GCP_SECRET_OVERWRITE:-0}"

_load_secret_env() {
  local env_name="$1"
  local secret_name_env="$2"
  local secret_name="${!secret_name_env:-}"

  if [[ -z "${secret_name}" ]]; then
    echo "secret_loader_skip env=${env_name} reason=missing_${secret_name_env}" >&2
    return 0
  fi

  if [[ -n "${!env_name:-}" && "${OVERWRITE}" != "1" ]]; then
    echo "secret_loader_skip env=${env_name} reason=already_set" >&2
    return 0
  fi

  local value
  value="$(gcloud secrets versions access "${SECRET_VERSION}" \
    --project="${GCP_PROJECT_ID}" \
    --secret="${secret_name}")"

  if [[ -z "${value}" ]]; then
    echo "secret_loader_skip env=${env_name} reason=empty_secret" >&2
    return 0
  fi

  export "${env_name}=${value}"
  echo "secret_loader_loaded env=${env_name}" >&2
}

_load_secret_env "TOSS_CLIENT_ID" "TOSS_CLIENT_ID_SECRET"
_load_secret_env "TOSS_CLIENT_SECRET" "TOSS_CLIENT_SECRET_SECRET"
_load_secret_env "TOSS_ACCOUNT_SEQ" "TOSS_ACCOUNT_SEQ_SECRET"
_load_secret_env "TOSS_API_ENV" "TOSS_API_ENV_SECRET"
_load_secret_env "TOSS_BROKER_BASE_URL" "TOSS_BROKER_BASE_URL_SECRET"
