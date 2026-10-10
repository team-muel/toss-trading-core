import base64
from datetime import datetime, timezone

import pytest
from asset_management.data.immutable import ImmutableDatasetStore, digest
from asset_management.data.calendar_ingress import admit_calendar_publication


def capture(store, **changes):
    raw=b"%PDF-1.4 public-calendar"
    url="https://www.nyse.com/publicdocs/calendar.pdf"
    value={"source":"official-exchange-calendar", "dataset":"nyse-calendar",
        "mode":"RESEARCH", "http_method":"GET", "status_code":200,
        "endpoint":url, "final_url":url, "request":{"url":url}, "body_base64":base64.b64encode(raw).decode(),
        "body_sha256":digest(raw), "received_at":"2026-10-11T00:00:01+00:00",
        "http_date":"Sun, 11 Oct 2026 00:00:00 GMT"}
    value.update(changes)
    return store.catalog("unadmitted-http-responses",value)


def admit(store, identifier):
    return admit_calendar_publication(store,identifier,
        license_tag="purpose=synthetic;redistribution=forbidden;retention=perpetual",
        code_revision="git:abcdef0")


def test_publication_bronze_only_and_replay_requires_source(tmp_path):
    store=ImmutableDatasetStore(tmp_path,credentials_classified=True)
    identifier=capture(store);manifest=admit(store,identifier)
    assert admit(store,identifier)==manifest
    assert store.read(manifest.manifest_id)[1]["response_receipt"]==identifier
    assert manifest.dataset=="calendar-publication" and manifest.layer=="bronze"
    store.layout.resolve("catalog",f"unadmitted-http-responses/{identifier}.json").unlink()
    with pytest.raises(OSError):store.read(manifest.manifest_id)


@pytest.mark.parametrize("changes", [{"status_code":True}, {"mode":"LIVE"},
    {"endpoint":"http://www.nyse.com/calendar"}, {"request":{"url":"https://www.nasdaq.com/other"}},
    {"body_sha256":"0"*64}, {"received_at":"2026-10-11T00:00:01"},
    {"http_date":"Sun, 11 Oct 2026 00:00:02 GMT"}])
def test_invalid_calendar_capture_not_published(tmp_path,changes):
    store=ImmutableDatasetStore(tmp_path,credentials_classified=True)
    with pytest.raises(ValueError):admit(store,capture(store,**changes))
    assert not list((tmp_path/"bronze").glob("*.json"))


@pytest.mark.parametrize("changes", [{"final_url":None}, {"final_url":"https://example.com/calendar"},
    {"endpoint":"https://user:pass@www.nyse.com/calendar"}, {"dataset":""},
    {"body_base64":"bad!"}, {"body_base64":None}, {"http_date":None}])
def test_incomplete_or_untrusted_transport_not_published(tmp_path,changes):
    store=ImmutableDatasetStore(tmp_path,credentials_classified=True)
    with pytest.raises(ValueError):admit(store,capture(store,**changes))
    assert not list((tmp_path/"bronze").glob("*.json"))


def test_credential_echo_in_document_rejected(tmp_path):
    store=ImmutableDatasetStore(tmp_path,secrets=("private-value",),credentials_classified=True)
    raw=b"%PDF-1.4 private-value"
    identifier=capture(store,body_base64=base64.b64encode(raw).decode(),body_sha256=digest(raw))
    with pytest.raises(ValueError,match="CALENDAR_SECRET_ECHO"):admit(store,identifier)
    assert not list((tmp_path/"bronze").glob("*.json"))


def test_unclassified_binary_source_rejected(tmp_path):
    store=ImmutableDatasetStore(tmp_path)
    with pytest.raises(ValueError,match="CREDENTIALS_NOT_CLASSIFIED"):admit(store,capture(store))
