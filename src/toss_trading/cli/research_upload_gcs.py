from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession
from requests import Response
from requests.exceptions import RequestException

_STORAGE_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def _parse_gs_uri(uri: str, *, require_object: bool = False) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError("destination URI must start with gs://")
    bucket, separator, object_name = uri[5:].partition("/")
    if not bucket or (require_object and (not separator or not object_name)):
        raise ValueError("destination URI must include a bucket and object path")
    return bucket, object_name.strip("/")


def _require_success(response: Response, *, operation: str) -> None:
    if 200 <= response.status_code < 300:
        return
    detail = response.text[:500].replace("\n", " ")
    raise RuntimeError(
        f"GCS {operation} failed with HTTP {response.status_code}: {detail}"
    )


def _start_resumable_upload(
    session: AuthorizedSession,
    *,
    bucket: str,
    object_name: str,
    size: int,
    attempts: int = 3,
) -> str:
    endpoint = (
        "https://storage.googleapis.com/upload/storage/v1/b/"
        f"{quote(bucket, safe='')}/o"
    )
    for attempt in range(1, attempts + 1):
        try:
            response = session.post(
                endpoint,
                params={
                    "uploadType": "resumable",
                    "name": object_name,
                    "ifGenerationMatch": "0",
                },
                headers={
                    "Content-Length": "0",
                    "X-Upload-Content-Length": str(size),
                    "X-Upload-Content-Type": "application/octet-stream",
                },
                timeout=(10, 60),
            )
        except RequestException:
            if attempt == attempts:
                raise
            time.sleep(attempt)
            continue
        if response.status_code in _TRANSIENT_STATUS_CODES and attempt < attempts:
            time.sleep(attempt)
            continue
        _require_success(response, operation="resumable-session creation")
        location = response.headers.get("Location")
        if not location:
            raise RuntimeError("GCS resumable-session response omitted Location")
        return location
    raise RuntimeError("GCS resumable-session creation exhausted retries")


def _upload_file(
    session: AuthorizedSession,
    *,
    source: Path,
    bucket: str,
    object_name: str,
    attempts: int = 3,
) -> dict[str, Any]:
    size = source.stat().st_size
    location = _start_resumable_upload(
        session,
        bucket=bucket,
        object_name=object_name,
        size=size,
        attempts=attempts,
    )
    for attempt in range(1, attempts + 1):
        try:
            with source.open("rb") as handle:
                response = session.put(
                    location,
                    data=handle,
                    headers={
                        "Content-Length": str(size),
                        "Content-Type": "application/octet-stream",
                    },
                    timeout=(10, 600),
                )
        except RequestException:
            if attempt == attempts:
                raise
            time.sleep(attempt)
            continue
        if response.status_code in _TRANSIENT_STATUS_CODES and attempt < attempts:
            time.sleep(attempt)
            continue
        _require_success(response, operation="object upload")
        payload = response.json()
        return {
            "source": str(source),
            "object": f"gs://{bucket}/{object_name}",
            "bytes": size,
            "generation": payload.get("generation"),
        }
    raise RuntimeError("GCS object upload exhausted retries")


def upload_tree(
    *,
    source_dir: Path,
    destination_uri: str,
    aliases: list[str],
    workers: int = 16,
) -> dict[str, Any]:
    if not 1 <= workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    source_root = source_dir.resolve(strict=True)
    if not source_root.is_dir():
        raise ValueError("source-dir must be a directory")
    bucket, prefix = _parse_gs_uri(destination_uri)
    if not prefix:
        raise ValueError("tree destination must include an object prefix")

    credentials, _ = google.auth.default(scopes=[_STORAGE_SCOPE])
    uploads: list[tuple[Path, str, str]] = []
    for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
        relative = source.relative_to(source_root).as_posix()
        uploads.append((source, bucket, f"{prefix}/{relative}"))

    for alias in aliases:
        source_text, separator, alias_uri = alias.partition("=")
        if not separator:
            raise ValueError("alias must use SOURCE=gs://BUCKET/OBJECT syntax")
        source = Path(source_text).resolve(strict=True)
        if not source.is_file() or not source.is_relative_to(source_root):
            raise ValueError("alias source must be a file inside source-dir")
        alias_bucket, alias_object = _parse_gs_uri(
            alias_uri,
            require_object=True,
        )
        uploads.append((source, alias_bucket, alias_object))

    thread_state = threading.local()

    def upload(item: tuple[Path, str, str]) -> dict[str, Any]:
        session = getattr(thread_state, "session", None)
        if session is None:
            session = AuthorizedSession(credentials)
            thread_state.session = session
        source, item_bucket, object_name = item
        return _upload_file(
            session,
            source=source,
            bucket=item_bucket,
            object_name=object_name,
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        uploaded = list(executor.map(upload, uploads))

    return {
        "destination_uri": destination_uri,
        "workers": workers,
        "file_count": len(uploaded),
        "bytes": sum(item["bytes"] for item in uploaded),
        "objects": uploaded,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create immutable GCS objects without requiring read access."
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--destination-uri", required=True)
    parser.add_argument("--alias", action="append", default=[])
    parser.add_argument("--workers", type=int, default=16)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = upload_tree(
        source_dir=args.source_dir,
        destination_uri=args.destination_uri,
        aliases=args.alias,
        workers=args.workers,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
