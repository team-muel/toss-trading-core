#!/usr/bin/env bash
# Run from an authenticated operator environment such as GCP Cloud Shell.
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PROJECT_ID="${GCP_PROJECT_ID:-toss-trading-core-lab}"
ZONE="${GCP_ZONE:-us-central1-a}"
INSTANCE_NAME="${GCP_INSTANCE_NAME:-personal-research-agent-vm}"
ADDRESS_NAME="${GCP_RESEARCH_ADDRESS_NAME:-toss-research-static-ip}"
MACHINE_TYPE="${GCP_RESEARCH_MACHINE_TYPE:-e2-micro}"
BOOT_DISK_SIZE="${GCP_RESEARCH_BOOT_DISK_SIZE:-30GB}"
BUCKET_NAME="${RESEARCH_GCS_BUCKET:-toss-trading-core-lab-research-data}"
BUILD_SOURCE_BUCKET="${CLOUD_BUILD_SOURCE_BUCKET:-${PROJECT_ID}_cloudbuild}"
RESEARCH_SERVICE_ACCOUNT_NAME="${RESEARCH_SERVICE_ACCOUNT_NAME:-toss-research-runner}"
SERVICE_ACCOUNT="${RESEARCH_SERVICE_ACCOUNT:-${RESEARCH_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"
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

if [[ "${SERVICE_ACCOUNT}" == "toss-foundation-runner@${PROJECT_ID}.iam.gserviceaccount.com" ]]; then
  echo "research provisioning refuses the Foundation service account" >&2
  exit 78
fi

if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${RESEARCH_SERVICE_ACCOUNT_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="Toss research runtime"
fi

gcloud services enable \
  compute.googleapis.com \
  storage.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  bigquery.googleapis.com \
  gmail.googleapis.com \
  aiplatform.googleapis.com \
  --project="${PROJECT_ID}"

REGION="${ZONE%-*}"
if ! gcloud compute addresses describe "${ADDRESS_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" >/dev/null 2>&1; then
  gcloud compute addresses create "${ADDRESS_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}"
fi
RESEARCH_IP="$(gcloud compute addresses describe "${ADDRESS_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(address)')"
if ! gcloud compute instances describe "${INSTANCE_NAME}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" >/dev/null 2>&1; then
  gcloud compute instances create "${INSTANCE_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --machine-type="${MACHINE_TYPE}" \
    --network-interface="network=default,address=${RESEARCH_IP}" \
    --service-account="${SERVICE_ACCOUNT}" \
    --scopes=cloud-platform \
    --image-family=ubuntu-2404-lts-amd64 \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size="${BOOT_DISK_SIZE}" \
    --boot-disk-type=pd-balanced \
    --metadata=enable-osconfig=TRUE \
    --shielded-secure-boot \
    --shielded-vtpm \
    --shielded-integrity-monitoring
fi

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/aiplatform.user" \
  --condition=None
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/logging.logWriter" \
  --condition=None
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/monitoring.metricWriter" \
  --condition=None

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
gcloud storage buckets remove-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/storage.objectAdmin" \
  --condition=None >/dev/null 2>&1 || true
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/storage.objectCreator" \
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
  toss-research-client-id \
  toss-research-client-secret \
  tiingo-api-token \
  fred-api-key \
  massive-api-key \
  sec-user-agent \
  toss-research-gmail-oauth-client-id \
  toss-research-gmail-oauth-client-secret \
  toss-research-gmail-oauth-refresh-token; do
  if gcloud secrets describe "${secret_name}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "${secret_name}" \
      --project="${PROJECT_ID}" \
      --member="serviceAccount:${SERVICE_ACCOUNT}" \
      --role="roles/secretmanager.secretAccessor" \
      --condition=None
  fi
done
if ! gcloud secrets describe massive-api-key \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud secrets create massive-api-key \
    --project="${PROJECT_ID}" \
    --replication-policy=automatic
  gcloud secrets add-iam-policy-binding massive-api-key \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None
fi

CALIBRATION_SECRET="research-execution-cost-calibration"
if ! gcloud secrets describe "${CALIBRATION_SECRET}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud secrets create "${CALIBRATION_SECRET}" \
    --project="${PROJECT_ID}" \
    --replication-policy=automatic
fi
gcloud secrets add-iam-policy-binding "${CALIBRATION_SECRET}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None
FOUNDATION_SERVICE_ACCOUNT="toss-foundation-runner@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud secrets add-iam-policy-binding "${CALIBRATION_SECRET}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${FOUNDATION_SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretVersionAdder" \
  --condition=None
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
  "${PROJECT_ID}:${BIGQUERY_DATASET}.${BIGQUERY_TABLE}" \
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
    else
      gcloud alpha monitoring policies update "${existing}" \
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
echo "research_instance=${INSTANCE_NAME} service_account=${SERVICE_ACCOUNT} static_ip=${RESEARCH_IP}"
echo "toss_allowlist_action=register_static_ip_${RESEARCH_IP}_for_research_client"
