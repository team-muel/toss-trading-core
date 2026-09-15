import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_research_operations_destination.py"
SPEC = importlib.util.spec_from_file_location("research_operations_destination", SCRIPT)
destination_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(destination_check)


def _registry(bucket_name: str = "approved-research-artifacts") -> dict:
    return {
        "schema_version": "research-operations-bucket-registry@1",
        "approved_project_id": "toss-trading-core-lab-508411",
        "approved_project_number": "347216292745",
        "approved_buckets": [
            {
                "bucket_name": bucket_name,
                "project_id": "toss-trading-core-lab-508411",
                "approval_evidence_id": "AMA-168-approved-bucket-example",
            }
        ],
    }


def test_repository_destination_paths_have_registry_guards():
    destination_check.validate_repository(ROOT)
    assert destination_check.main(["--check-repository"]) == 0


def test_empty_checked_in_approval_fails_closed_before_any_gcloud_lookup(monkeypatch):
    def unexpected_gcloud(*args, **kwargs):
        raise AssertionError("unapproved destination must not reach gcloud")

    monkeypatch.setattr(destination_check.subprocess, "run", unexpected_gcloud)
    assert destination_check.main(["--bucket", "caller-selected-bucket"]) == 1
    assert (
        destination_check.main(
            [
                "--uri",
                "gs://caller-selected-bucket/research",
                "--verify-bucket-project",
            ]
        )
        == 1
    )


def test_exact_approved_bucket_and_canonical_uri_are_accepted_when_registered(tmp_path):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(_registry()), encoding="utf-8")

    assert (
        destination_check.main(
            ["--bucket", "approved-research-artifacts", "--registry", str(registry_path)]
        )
        == 0
    )
    assert (
        destination_check.main(
            [
                "--uri",
                "gs://approved-research-artifacts/research/runs",
                "--registry",
                str(registry_path),
            ]
        )
        == 0
    )


def test_registered_bucket_is_rechecked_against_actual_gcp_project(monkeypatch, tmp_path):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(_registry()), encoding="utf-8")

    class Result:
        stdout = "347216292745\n"

    monkeypatch.setattr(destination_check.subprocess, "run", lambda *args, **kwargs: Result())
    assert (
        destination_check.main(
            [
                "--bucket",
                "approved-research-artifacts",
                "--verify-bucket-project",
                "--registry",
                str(registry_path),
            ]
        )
        == 0
    )

    Result.stdout = "foreign-project-number\n"
    assert (
        destination_check.main(
            [
                "--bucket",
                "approved-research-artifacts",
                "--verify-bucket-project",
                "--registry",
                str(registry_path),
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "uri",
    (
        "https://approved-research-artifacts/research",
        "gs:///approved-research-artifacts/research",
        "gs://approved-research-artifacts//research",
        "gs://approved-research-artifacts/research/../escape",
        "gs://approved-research-artifacts/research?override=1",
    ),
)
def test_malformed_or_noncanonical_uri_is_rejected(uri):
    with pytest.raises(ValueError):
        destination_check.parse_gs_uri(uri)


def test_caller_override_cannot_select_a_different_bucket_even_when_one_is_approved(tmp_path):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(_registry()), encoding="utf-8")

    assert (
        destination_check.main(
            ["--bucket", "caller-selected-bucket", "--registry", str(registry_path)]
        )
        == 1
    )


def test_build_source_override_is_subject_to_the_same_registry(tmp_path):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(_registry()), encoding="utf-8")

    assert (
        destination_check.main(
            ["--bucket", "caller-build-source-bucket", "--registry", str(registry_path)]
        )
        == 1
    )
    assert (
        destination_check.main(
            [
                "--uri",
                "gs://caller-selected-bucket/research",
                "--registry",
                str(registry_path),
            ]
        )
        == 1
    )


def test_cross_project_bucket_record_is_invalid_even_if_the_name_matches(tmp_path):
    registry_path = tmp_path / "registry.json"
    foreign = _registry()
    foreign["approved_buckets"][0]["project_id"] = "foreign-project"
    registry_path.write_text(json.dumps(foreign), encoding="utf-8")

    assert (
        destination_check.main(
            ["--bucket", "approved-research-artifacts", "--registry", str(registry_path)]
        )
        == 1
    )
