#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-toss-trading-core-lab}"
ZONE="${GCP_ZONE:-us-central1-a}"
INSTANCE_NAME="${GCP_RESEARCH_INSTANCE_NAME:-personal-research-agent-vm}"
EXPECTED="${RESEARCH_SERVICE_ACCOUNT:-toss-research-runner@${PROJECT_ID}.iam.gserviceaccount.com}"
FOUNDATION="toss-foundation-runner@${PROJECT_ID}.iam.gserviceaccount.com"

ATTACHED="$(gcloud compute instances describe "${INSTANCE_NAME}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --format='value(serviceAccounts.email)')"
if [[ "${ATTACHED}" != "${EXPECTED}" ]]; then
  echo "research_identity=invalid expected=${EXPECTED} actual=${ATTACHED:-missing}" >&2
  exit 1
fi
if [[ "${ATTACHED}" == "${FOUNDATION}" ]]; then
  echo "research_identity=invalid reason=foundation_identity_reuse" >&2
  exit 1
fi

for secret_name in \
  toss-client-id \
  toss-client-secret \
  toss-account-seq \
  toss-api-env \
  toss-broker-base-url; do
  if ! gcloud secrets describe "${secret_name}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    continue
  fi
  count="$(gcloud secrets get-iam-policy "${secret_name}" \
    --project="${PROJECT_ID}" \
    --flatten='bindings[].members' \
    --filter="bindings.members:serviceAccount:${EXPECTED}" \
    --format='value(bindings.members)' | wc -l)"
  if [[ "${count}" -ne 0 ]]; then
    echo "research_identity=invalid reason=foundation_secret_access secret=${secret_name}" >&2
    exit 1
  fi
done

echo "research_identity=ok service_account=${EXPECTED} instance=${INSTANCE_NAME}"
