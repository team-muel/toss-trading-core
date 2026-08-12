#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-toss-trading-core-lab}"
ZONE="${GCP_ZONE:-us-central1-a}"
INSTANCE_NAME="${GCP_RESEARCH_INSTANCE_NAME:-personal-research-agent-vm}"
EXPECTED="${RESEARCH_SERVICE_ACCOUNT:-toss-research-runner@${PROJECT_ID}.iam.gserviceaccount.com}"
FOUNDATION="toss-foundation-runner@${PROJECT_ID}.iam.gserviceaccount.com"
ADDRESS_NAME="${GCP_RESEARCH_ADDRESS_NAME:-toss-research-static-ip}"
REGION="${ZONE%-*}"

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

INSTANCE_IP="$(gcloud compute instances describe "${INSTANCE_NAME}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --format='value(networkInterfaces[0].accessConfigs[0].natIP)')"
EXPECTED_IP="$(gcloud compute addresses describe "${ADDRESS_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(address)')"
if [[ -z "${INSTANCE_IP}" || "${INSTANCE_IP}" != "${EXPECTED_IP}" ]]; then
  echo "research_identity=invalid reason=static_ip_mismatch" >&2
  exit 1
fi

for broad_role in roles/owner roles/editor roles/secretmanager.secretAccessor; do
  count="$(gcloud projects get-iam-policy "${PROJECT_ID}" \
    --flatten='bindings[].members' \
    --filter="bindings.role=${broad_role} AND bindings.members=serviceAccount:${EXPECTED}" \
    --format='value(bindings.role)' | wc -l)"
  if [[ "${count}" -ne 0 ]]; then
    echo "research_identity=invalid reason=broad_project_role role=${broad_role}" >&2
    exit 1
  fi
done

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

if ! gcloud compute ssh "${INSTANCE_NAME}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --tunnel-through-iap \
  --quiet \
  --command="if systemctl list-unit-files --no-legend 'toss-foundation*' | grep -q .; then exit 1; fi"; then
  echo "research_identity=invalid reason=foundation_unit_present" >&2
  exit 1
fi

echo "research_identity=ok service_account=${EXPECTED} instance=${INSTANCE_NAME} static_ip=${INSTANCE_IP}"
