#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${TOSS_RUNTIME_ROOT:-/home/seoje/toss-trading}"
CURRENT="$(readlink -f "${ROOT}/current")"
CURRENT_REVISION="$(basename "${CURRENT}")"
RUNTIME="${RESEARCH_RUNTIME_ROOT:-${ROOT}/research-runtime}"
REQUIRE_PAPER="${RESEARCH_AUDIT_REQUIRE_PAPER:-0}"

if [[ ! "${CURRENT_REVISION}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "research_release_audit=failed reason=invalid_current_release" >&2
  exit 78
fi

verify_run() {
  local mode="$1"
  local link="${RUNTIME}/latest-${mode}"
  local run_dir
  run_dir="$(readlink -f "${link}")"
  if [[ ! -d "${run_dir}" ]]; then
    echo "research_release_audit=failed reason=missing_${mode}_run" >&2
    exit 1
  fi
  if ! grep -Eq '"ready_for_upload"[[:space:]]*:[[:space:]]*true' \
    "${run_dir}/run-status.json"; then
    echo "research_release_audit=failed reason=${mode}_not_ready" >&2
    exit 1
  fi
  (cd "${run_dir}" && sha256sum -c SHA256SUMS >/dev/null)
  local revision
  revision="$(
    python3 - "${run_dir}/reports/reporting-summary.json" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload["strategy"]["code_revision"])
PY
  )"
  if [[ "${revision}" != "${CURRENT_REVISION}" ]]; then
    echo \
      "research_release_audit=failed reason=${mode}_revision_mismatch expected=${CURRENT_REVISION} actual=${revision}" \
      >&2
    exit 1
  fi
  printf '%s_run=%s\n' "${mode}" "$(basename "${run_dir}")"
}

verify_run daily
verify_run weekly

for timer in \
  toss-research-daily.timer \
  toss-research-weekly.timer \
  toss-research-prune.timer; do
  systemctl is-enabled "${timer}" >/dev/null
done
if [[ "$(systemctl show toss-research-prune.service -p Result --value)" != "success" ]]; then
  echo "research_release_audit=failed reason=prune_not_verified" >&2
  exit 1
fi

if [[ "${REQUIRE_PAPER}" == "1" ]]; then
  paper_report="${ROOT}/paper-runtime/latest-report"
  if [[ ! -f "${paper_report}" ]]; then
    echo "research_release_audit=failed reason=paper_report_missing" >&2
    exit 1
  fi
  python3 - "${paper_report}" "${CURRENT_REVISION}" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("source_code_revision") != sys.argv[2]:
    raise SystemExit("paper report revision mismatch")
if payload.get("live_orders_enabled") is not False:
    raise SystemExit("paper report enabled live orders")
if payload.get("reconciliation", {}).get("status") != "ok":
    raise SystemExit("paper reconciliation is not ok")
PY
fi

echo "research_release_audit=ok revision=${CURRENT_REVISION} paper_required=${REQUIRE_PAPER}"
