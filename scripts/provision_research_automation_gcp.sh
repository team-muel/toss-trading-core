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
BUILD_ARTIFACT_CONDITION="expression=resource.name.startsWith('projects/_/buckets/${BUCKET_NAME}/objects/builds/'),title=BuildArtifactsPrefix,description=Restrict Cloud Build to the builds prefix"
BIGQUERY_LOCATION="${RESEARCH_BIGQUERY_LOCATION:-us-central1}"
BIGQUERY_DATASET="${RESEARCH_BIGQUERY_DATASET:-toss_research_reporting}"
BIGQUERY_TABLE="${RESEARCH_BIGQUERY_TABLE:-run_summaries}"
DASHBOARD_DISPLAY_NAME="Toss Trading - Operations, Data Quality, Strategy"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NOTIFICATION_CHANNEL="${MONITORING_NOTIFICATION_CHANNEL:-}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "${WORK_DIR}"' EXIT

for command in gcloud bq; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "required command is missing: ${command}" >&2
    exit 69
  fi
done

gcloud services enable \
  storage.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  bigquery.googleapis.com \
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
  --role="roles/storage.objectAdmin" \
  --condition=None

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
  --role="roles/storage.objectCreator" \
  --condition="${BUILD_ARTIFACT_CONDITION}"
if ! gcloud storage buckets describe "gs://${BUILD_SOURCE_BUCKET}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUILD_SOURCE_BUCKET}" \
    --project="${PROJECT_ID}" \
    --location=us-central1 \
    --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://${BUILD_SOURCE_BUCKET}" \
  --member="serviceAccount:${BUILD_SERVICE_ACCOUNT}" \
  --role="roles/storage.objectViewer" \
  --condition=None

for secret_name in \
  tiingo-api-token \
  fred-api-key \
  sec-user-agent; do
  if gcloud secrets describe "${secret_name}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "${secret_name}" \
      --project="${PROJECT_ID}" \
      --member="serviceAccount:${SERVICE_ACCOUNT}" \
      --role="roles/secretmanager.secretAccessor" \
      --condition=None
  fi
done

METRIC_DIR="${WORK_DIR}/metrics"
"${PYTHON_BIN}" scripts/render_research_log_metrics.py \
  --output-dir "${METRIC_DIR}"
for metric_path in "${METRIC_DIR}"/*.json; do
  metric_name="$(basename "${metric_path}" .json)"
  if gcloud logging metrics describe "${metric_name}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud logging metrics update "${metric_name}" \
      --project="${PROJECT_ID}" \
      --config-from-file="${metric_path}"
  else
    gcloud logging metrics create "${metric_name}" \
      --project="${PROJECT_ID}" \
      --config-from-file="${metric_path}"
  fi
done

if ! bq --project_id="${PROJECT_ID}" show \
  --dataset "${PROJECT_ID}:${BIGQUERY_DATASET}" >/dev/null 2>&1; then
  bq --project_id="${PROJECT_ID}" \
    --location="${BIGQUERY_LOCATION}" \
    mk --dataset \
    --description="Private Toss research reporting history" \
    "${PROJECT_ID}:${BIGQUERY_DATASET}"
fi
if ! bq --project_id="${PROJECT_ID}" show \
  --table "${PROJECT_ID}:${BIGQUERY_DATASET}.${BIGQUERY_TABLE}" \
  >/dev/null 2>&1; then
  bq --project_id="${PROJECT_ID}" \
    --location="${BIGQUERY_LOCATION}" \
    mk --table \
    --description="Immutable research run quality and strategy summaries" \
    --time_partitioning_field=verified_at \
    --time_partitioning_type=DAY \
    --clustering_fields=mode,strategy_state \
    "${PROJECT_ID}:${BIGQUERY_DATASET}.${BIGQUERY_TABLE}" \
    deploy/bigquery/research_run_summary_schema.json
fi

BIGQUERY_VIEW_SQL="${WORK_DIR}/latest_run_summaries.sql"
"${PYTHON_BIN}" scripts/render_bigquery_reporting.py \
  --output="${BIGQUERY_VIEW_SQL}" \
  --project-id="${PROJECT_ID}" \
  --dataset-id="${BIGQUERY_DATASET}" \
  --table-id="${BIGQUERY_TABLE}"
bq --project_id="${PROJECT_ID}" \
  --location="${BIGQUERY_LOCATION}" \
  query --use_legacy_sql=false < "${BIGQUERY_VIEW_SQL}"
bq --project_id="${PROJECT_ID}" \
  --location="${BIGQUERY_LOCATION}" \
  query --use_legacy_sql=false \
  "GRANT \`roles/bigquery.dataEditor\` ON SCHEMA \`${PROJECT_ID}.${BIGQUERY_DATASET}\` TO 'serviceAccount:${SERVICE_ACCOUNT}';"

INSTANCE_ID="$(gcloud compute instances describe "${INSTANCE_NAME}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --format='value(id)')"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" \
  --format='value(projectNumber)')"
EXISTING_DASHBOARD="$(
  gcloud monitoring dashboards list \
    --project="${PROJECT_ID}" \
    --filter="displayName=\"${DASHBOARD_DISPLAY_NAME}\"" \
    --format='value(name)' \
    --limit=1
)"
DASHBOARD_PATH="${WORK_DIR}/research-visual-report.json"
if [[ -n "${EXISTING_DASHBOARD}" ]]; then
  DASHBOARD_ID="${EXISTING_DASHBOARD##*/}"
  DASHBOARD_ETAG="$(
    gcloud monitoring dashboards describe "${DASHBOARD_ID}" \
      --project="${PROJECT_ID}" \
      --format='value(etag)'
  )"
  "${PYTHON_BIN}" scripts/render_research_dashboard.py \
    --output="${DASHBOARD_PATH}" \
    --project-id="${PROJECT_ID}" \
    --project-number="${PROJECT_NUMBER}" \
    --instance-id="${INSTANCE_ID}" \
    --dataset-id="${BIGQUERY_DATASET}" \
    --etag="${DASHBOARD_ETAG}"
  gcloud monitoring dashboards update "${DASHBOARD_ID}" \
    --project="${PROJECT_ID}" \
    --config-from-file="${DASHBOARD_PATH}"
else
  "${PYTHON_BIN}" scripts/render_research_dashboard.py \
    --output="${DASHBOARD_PATH}" \
    --project-id="${PROJECT_ID}" \
    --project-number="${PROJECT_NUMBER}" \
    --instance-id="${INSTANCE_ID}" \
    --dataset-id="${BIGQUERY_DATASET}"
  gcloud monitoring dashboards create \
    --project="${PROJECT_ID}" \
    --config-from-file="${DASHBOARD_PATH}"
fi

if [[ -n "${NOTIFICATION_CHANNEL}" ]]; then
  RENDERED_DIR="${WORK_DIR}/policies"
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
bq --project_id="${PROJECT_ID}" show \
  "${PROJECT_ID}:${BIGQUERY_DATASET}.${BIGQUERY_TABLE}"
gcloud monitoring dashboards list \
  --project="${PROJECT_ID}" \
  --filter="displayName=\"${DASHBOARD_DISPLAY_NAME}\""
