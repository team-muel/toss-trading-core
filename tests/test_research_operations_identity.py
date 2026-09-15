import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_research_operations_identity.py"
SPEC = importlib.util.spec_from_file_location("research_operations_identity", SCRIPT)
identity_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(identity_check)


def test_repository_operations_target_only_the_approved_project():
    identity = json.loads(
        (ROOT / "config/research_operations_identity.json").read_text(encoding="utf-8")
    )

    identity_check.validate_repository(ROOT, identity)
    assert identity_check.main(["--check-repository"]) == 0


@pytest.mark.parametrize("project_id", ("", "toss-trading-core-lab", "another-project"))
def test_project_identity_rejects_missing_legacy_or_foreign_project(project_id):
    identity = identity_check.load_identity(ROOT / "config/research_operations_identity.json")

    with pytest.raises(ValueError, match="research operations project is not approved"):
        identity_check.require_approved_project(project_id, identity)


def test_legacy_project_name_is_not_detected_inside_the_approved_project_id():
    source = "project=toss-trading-core-lab-508411"

    assert not identity_check.contains_project_identifier(source, "toss-trading-core-lab")
    assert identity_check.contains_project_identifier(
        "project=toss-trading-core-lab", "toss-trading-core-lab"
    )
