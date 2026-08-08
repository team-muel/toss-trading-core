#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${TOSS_RUNTIME_ROOT:-/home/seoje/toss-trading}"
RUNS_ROOT="${ROOT}/research-runtime/runs"
RELEASES_ROOT="${ROOT}/releases"
CURRENT_RELEASE="$(readlink -f "${ROOT}/current")"
PREVIOUS_RELEASE=""
if [[ -L "${ROOT}/previous" ]]; then
  PREVIOUS_RELEASE="$(readlink -f "${ROOT}/previous")"
fi
APPLY=0
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=1
elif [[ $# -gt 0 ]]; then
  echo "usage: $0 [--apply]" >&2
  exit 64
fi

safe_remove() {
  local expected_root="$1"
  local candidate="$2"
  local resolved
  resolved="$(readlink -f "${candidate}")"
  case "${resolved}" in
    "${expected_root}/"*) ;;
    *)
      echo "unsafe prune target rejected: ${resolved}" >&2
      exit 65
      ;;
  esac
  if [[ "${APPLY}" == "1" ]]; then
    rm -rf --one-file-system -- "${resolved}"
    printf 'pruned path=%s\n' "${resolved}"
  else
    printf 'would_prune path=%s\n' "${resolved}"
  fi
}

prune_verified_runs() {
  local pattern="$1"
  local days="$2"
  local run
  [[ -d "${RUNS_ROOT}" ]] || return 0
  while IFS= read -r -d '' run; do
    [[ -f "${run}/run-status.json" && -f "${run}/SHA256SUMS" ]] || continue
    grep -Eq '"ready_for_upload"[[:space:]]*:[[:space:]]*true' \
      "${run}/run-status.json" || continue
    safe_remove "${RUNS_ROOT}" "${run}"
  done < <(
    find "${RUNS_ROOT}" -mindepth 1 -maxdepth 1 -type d \
      -name "${pattern}" -mtime "+${days}" -print0
  )
}

prune_verified_runs "daily-*" 14
prune_verified_runs "weekly-*" 90

if [[ -d "${RELEASES_ROOT}" ]]; then
  kept_previous=0
  if [[ -n "${PREVIOUS_RELEASE}" && -d "${PREVIOUS_RELEASE}" ]]; then
    kept_previous=1
  fi
  while IFS= read -r release; do
    [[ -n "${release}" ]] || continue
    if [[ "$(readlink -f "${release}")" == "${CURRENT_RELEASE}" ]]; then
      continue
    fi
    if [[ -n "${PREVIOUS_RELEASE}" \
      && "$(readlink -f "${release}")" == "${PREVIOUS_RELEASE}" ]]; then
      continue
    fi
    kept_previous=$((kept_previous + 1))
    if [[ "${kept_previous}" -le 2 ]]; then
      continue
    fi
    safe_remove "${RELEASES_ROOT}" "${release}"
  done < <(
    find "${RELEASES_ROOT}" -mindepth 1 -maxdepth 1 -type d \
      -regextype posix-extended -regex '.*/[0-9a-f]{7,40}' \
      -printf '%T@ %p\n' \
      | sort -rn \
      | cut -d' ' -f2-
  )
fi
