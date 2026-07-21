from __future__ import annotations

import os
from dataclasses import dataclass


SECRET_ENV_MAP = {
    "TOSS_CLIENT_ID": "TOSS_CLIENT_ID_SECRET",
    "TOSS_CLIENT_SECRET": "TOSS_CLIENT_SECRET_SECRET",
    "TOSS_ACCOUNT_SEQ": "TOSS_ACCOUNT_SEQ_SECRET",
    "TOSS_BROKER_BASE_URL": "TOSS_BROKER_BASE_URL_SECRET",
    "TOSS_API_ENV": "TOSS_API_ENV_SECRET",
}


@dataclass(frozen=True)
class SecretLoadResult:
    loaded_env_names: list[str]
    skipped_env_names: list[str]


def _secret_version_path(project_id: str, secret_name: str, version: str) -> str:
    if "/" in secret_name:
        return secret_name
    return f"projects/{project_id}/secrets/{secret_name}/versions/{version}"


def load_gcp_secret_environment(
    *,
    project_id: str | None = None,
    version: str = "latest",
    overwrite: bool = False,
) -> SecretLoadResult:
    """Loads Toss runtime environment variables from GCP Secret Manager.

    Secret names are provided through *_SECRET environment variables. For
    example, set TOSS_CLIENT_SECRET_SECRET=toss-client-secret to load the
    TOSS_CLIENT_SECRET env var from that Secret Manager secret.
    """

    project_id = project_id or os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        raise RuntimeError("GCP_PROJECT_ID is required for Secret Manager loading")

    try:
        from google.cloud import secretmanager
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-secret-manager is required for Secret Manager loading"
        ) from exc

    client = secretmanager.SecretManagerServiceClient()
    loaded: list[str] = []
    skipped: list[str] = []

    for env_name, secret_env_name in SECRET_ENV_MAP.items():
        secret_name = os.environ.get(secret_env_name)
        if not secret_name:
            skipped.append(env_name)
            continue
        if os.environ.get(env_name) and not overwrite:
            skipped.append(env_name)
            continue

        response = client.access_secret_version(
            request={
                "name": _secret_version_path(project_id, secret_name, version),
            }
        )
        value = response.payload.data.decode("utf-8").strip()
        if value:
            os.environ[env_name] = value
            loaded.append(env_name)
        else:
            skipped.append(env_name)

    return SecretLoadResult(loaded_env_names=loaded, skipped_env_names=skipped)
