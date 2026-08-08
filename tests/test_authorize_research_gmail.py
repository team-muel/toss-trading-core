from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "authorize_research_gmail.py"
SPEC = importlib.util.spec_from_file_location("authorize_research_gmail", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_loads_only_installed_desktop_client(tmp_path: Path) -> None:
    client_file = tmp_path / "client.json"
    client_file.write_text(
        json.dumps(
            {"installed": {"client_id": "client-id", "client_secret": "client-secret"}}
        ),
        encoding="utf-8",
    )
    client = MODULE._load_oauth_client(client_file)
    assert client.client_id == "client-id"
    assert client.client_secret == "client-secret"

    client_file.write_text(
        json.dumps({"web": {"client_id": "wrong", "client_secret": "wrong"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="desktop client"):
        MODULE._load_oauth_client(client_file)


def test_authorization_url_is_send_only_and_offline() -> None:
    url = MODULE._authorization_url(
        client_id="client-id",
        redirect_uri="http://127.0.0.1:12345/",
        state="state",
        code_challenge="challenge",
        email="researcher@example.com",
    )
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == [MODULE.GMAIL_SEND_SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["login_hint"] == ["researcher@example.com"]
    assert "gmail.readonly" not in url
    assert "mail.google.com" not in url


def test_store_secret_creates_when_missing_and_writes_via_stdin(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    class Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def fake_run(args: list[str], *, stdin: str | None = None):
        calls.append((args, stdin))
        if args[:2] == ["secrets", "describe"]:
            return Result(1)
        return Result(0)

    monkeypatch.setattr(MODULE, "_run_gcloud", fake_run)
    MODULE._store_secret("project", "secret-name", "sensitive-value")

    assert calls[0][0][:3] == ["secrets", "describe", "secret-name"]
    assert calls[1][0][:3] == ["secrets", "create", "secret-name"]
    assert calls[2][0][:4] == ["secrets", "versions", "add", "secret-name"]
    assert calls[2][1] == "sensitive-value"
    assert all("sensitive-value" not in args for args, _stdin in calls)
