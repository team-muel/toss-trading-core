#!/usr/bin/env bash
# Run from an authenticated operator environment such as GCP Cloud Shell.
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PROJECT_ID="${GCP_PROJECT_ID:-toss-trading-core-lab}"
ZONE="${GCP_ZONE:-us-central1-a}"
INSTANCE_NAME="${GCP_INSTANCE_NAME:-personal-agent-vm}"
BUCKET_NAME="${RESEARCH_GCS_BUCKET:-toss-trading-core-lab-research-data}"
BUILD_SOURCE_BUCKET="${CLOUD_BUILD_SOURCE_BUCKET:-${PROJECT_ID}_cloudbuild}"
SERVICE_ACCOUNT="${RESEARCH_SERVICE_ACCOUNT:-toss-foundation-runner@${PROJECT_ID}.iam.gserviceaccount.com}"
BUILD_SERVICE_ACCOUNT_NAME="${RESEARCH_BUILD_SERVICE_ACCOUNT_NAME:-toss-research-build}"
BUILD_SERVICE_ACCOUNT="${BUILD_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NOTIFICATION_CHANNEL="${MONITORING_NOTIFICATION_CHANNEL:-}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "required command is missing: gcloud" >&2
  exit 69
fi

gcloud services enable \
  storage.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  --project="${PROJECT_ID}"

if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --project="${PROJECT_ID}" \
    --location=us-central1 \
    --uniform-bucket-level-access
fi
gcloud storage buckets update "gs://${BUCKET_NAME}" \
  --versioning \
  --lifecycle-file="deploy/storage/research-lifecycle.json"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/storage.objectAdmin"

if ! gcloud iam service-accounts describe "${BUILD_SERVICE_ACCOUNT}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${BUILD_SERVICE_ACCOUNT_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="Toss research Cloud Build"
fi
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${BUILD_SERVICE_ACCOUNT}" \
  --role="roles/logging.logWriter" \
  --condition=None
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${BUILD_SERVICE_ACCOUNT}" \
  --role="roles/storage.objectCreator"
if ! gcloud storage buckets describe "gs://${BUILD_SOURCE_BUCKET}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUILD_SOURCE_BUCKET}" \
    --project="${PROJECT_ID}" \
    --location=us-central1 \
    --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://${BUILD_SOURCE_BUCKET}" \
  --member="serviceAccount:${BUILD_SERVICE_ACCOUNT}" \
  --role="roles/storage.objectViewer"

for secret_name in \
  tiingo-api-token \
  fred-api-key \
  sec-user-agent; do
  if gcloud secrets describe "${secret_name}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "${secret_name}" \
      --project="${PROJECT_ID}" \
      --member="serviceAccount:${SERVICE_ACCOUNT}" \
      --role="roles/secretmanager.secretAccessor"
  fi
done

while IFS=$'\t' read -r metric_name event_name; do
  if ! gcloud logging metrics describe "${metric_name}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud logging metrics create "${metric_name}" \
      --project="${PROJECT_ID}" \
      --description="Toss research automation ${event_name} events" \
      --log-filter="resource.type=\"gce_instance\" AND jsonPayload.event=\"${event_name}\""
  fi
done < <(
  "${PYTHON_BIN}" - <<'PY'
from pathlib import Path
import yaml

payload = yaml.safe_load(
    Path("deploy/monitoring-research/log-metrics.yaml").read_text(encoding="utf-8")
)
for item in payload["metrics"]:
    print(f"{item['name']}\t{item['event']}")
PY
)

if [[ -n "${NOTIFICATION_CHANNEL}" ]]; then
  INSTANCE_ID="$(gcloud compute instances describe "${INSTANCE_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --format='value(id)')"
  RENDERED_DIR="$(mktemp -d)"
  trap 'rm -rf -- "${RENDERED_DIR}"' EXIT
  "${PYTHON_BIN}" scripts/render_research_monitoring.py \
    --output-dir "${RENDERED_DIR}" \
    --instance-id "${INSTANCE_ID}" \
    --notification-channel "${NOTIFICATION_CHANNEL}"
  for policy_path in "${RENDERED_DIR}"/research-*.yaml; do
    display_name="$("${PYTHON_BIN}" - "${policy_path}" <<'PY'
import sys
import yaml

print(yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["displayName"])
PY
)"
    existing="$(
      gcloud alpha monitoring policies list \
        --project="${PROJECT_ID}" \
        --filter="displayName=\"${display_name}\"" \
        --format='value(name)' \
        --limit=1
    )"
    if [[ -z "${existing}" ]]; then
      gcloud alpha monitoring policies create \
        --project="${PROJECT_ID}" \
        --policy-from-file="${policy_path}"
    fi
  done
else
  echo "monitoring_policy_skip reason=MONITORING_NOTIFICATION_CHANNEL_missing" >&2
fi

gcloud storage buckets describe "gs://${BUCKET_NAME}" \
  --project="${PROJECT_ID}" \
  --format='yaml(name,location,uniformBucketLevelAccess,versioning,lifecycle)'
gcloud logging metrics list \
  --project="${PROJECT_ID}" \
  --filter='name:research_'
