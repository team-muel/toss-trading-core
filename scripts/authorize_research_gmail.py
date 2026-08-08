#!/usr/bin/env python3
"""Authorize a personal Gmail sender and store OAuth credentials in Secret Manager."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SECRET_NAMES = {
    "client_id": "toss-research-gmail-oauth-client-id",
    "client_secret": "toss-research-gmail-oauth-client-secret",
    "refresh_token": "toss-research-gmail-oauth-refresh-token",
}


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str


def _load_oauth_client(path: Path) -> OAuthClient:
    payload = json.loads(path.read_text(encoding="utf-8"))
    installed = payload.get("installed")
    if not isinstance(installed, dict):
        raise ValueError("OAuth client JSON must contain an 'installed' desktop client")
    client_id = installed.get("client_id")
    client_secret = installed.get("client_secret")
    if not isinstance(client_id, str) or not client_id:
        raise ValueError("OAuth client JSON is missing installed.client_id")
    if not isinstance(client_secret, str) or not client_secret:
        raise ValueError("OAuth client JSON is missing installed.client_secret")
    return OAuthClient(client_id=client_id, client_secret=client_secret)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    email: str,
) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GMAIL_SEND_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "login_hint": email,
        }
    )
    return f"{AUTHORIZATION_URL}?{query}"


def _exchange_code(
    *,
    client: OAuthClient,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("ascii")
    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload: Any = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OAuth token exchange failed with HTTP {exc.code}") from exc
    refresh_token = payload.get("refresh_token") if isinstance(payload, dict) else None
    if not isinstance(refresh_token, str) or not refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token; revoke the prior grant and retry"
        )
    return refresh_token


def _gcloud_binary() -> str:
    binary = shutil.which("gcloud.cmd") or shutil.which("gcloud")
    if not binary:
        raise RuntimeError("gcloud CLI was not found on PATH")
    return binary


def _run_gcloud(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_gcloud_binary(), *args],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def _store_secret(project_id: str, name: str, value: str) -> None:
    describe = _run_gcloud(
        ["secrets", "describe", name, "--project", project_id, "--format=value(name)"]
    )
    if describe.returncode != 0:
        create = _run_gcloud(
            [
                "secrets",
                "create",
                name,
                "--project",
                project_id,
                "--replication-policy=automatic",
            ]
        )
        if create.returncode != 0:
            raise RuntimeError(f"could not create Secret Manager secret {name}")
    add = _run_gcloud(
        ["secrets", "versions", "add", name, "--project", project_id, "--data-file=-"],
        stdin=value,
    )
    if add.returncode != 0:
        raise RuntimeError(f"could not add a version to Secret Manager secret {name}")


def _receive_authorization_code(
    *,
    client_id: str,
    state: str,
    code_challenge: str,
    email: str,
    open_browser: bool = True,
) -> tuple[str, str]:
    result: dict[str, str] = {}
    completed = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            callback_state = query.get("state", [""])[0]
            callback_code = query.get("code", [""])[0]
            callback_error = query.get("error", [""])[0]
            if not callback_code and not callback_error:
                body = (
                    "<h1>Gmail authorization is ready</h1>"
                    "<p>Waiting for the Google authorization callback.</p>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            result["state"] = callback_state
            result["code"] = callback_code
            result["error"] = callback_error
            body = (
                "<h1>Gmail authorization received</h1>"
                "<p>You can close this tab and return to the terminal.</p>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            completed.set()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler) as server:
        redirect_uri = f"http://127.0.0.1:{server.server_port}/"
        url = _authorization_url(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
            email=email,
        )
        print("Opening Google authorization in your browser...")
        if not open_browser or not webbrowser.open(url):
            print(f"Open this URL manually:\n{url}")
        while not completed.is_set():
            server.handle_request()

    if result.get("error"):
        raise RuntimeError(f"Google authorization failed: {result['error']}")
    if not secrets.compare_digest(result.get("state", ""), state):
        raise RuntimeError("OAuth callback state did not match")
    if not result.get("code"):
        raise RuntimeError("OAuth callback did not contain an authorization code")
    return result["code"], redirect_uri


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Authorize Gmail send-only access and store credentials in GCP"
    )
    parser.add_argument("--client-json", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorization URL instead of opening the default browser",
    )
    args = parser.parse_args()

    client = _load_oauth_client(args.client_json)
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _pkce_pair()
    code, redirect_uri = _receive_authorization_code(
        client_id=client.client_id,
        state=state,
        code_challenge=code_challenge,
        email=args.email,
        open_browser=not args.no_browser,
    )
    refresh_token = _exchange_code(
        client=client,
        code=code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )

    values = {
        "client_id": client.client_id,
        "client_secret": client.client_secret,
        "refresh_token": refresh_token,
    }
    for key, secret_name in SECRET_NAMES.items():
        _store_secret(args.project_id, secret_name, values[key])

    print(
        "Gmail send-only authorization is stored in Secret Manager; "
        "no credential values were printed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
