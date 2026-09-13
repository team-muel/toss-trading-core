from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_foundation_and_paper_execution_surface_is_not_packaged():
    removed_paths = (
        "scripts/run_foundation_gcp.sh",
        "scripts/run_paper_operation_gcp.sh",
        "deploy/systemd/toss-foundation.service",
        "deploy/systemd/toss-foundation.timer",
        "deploy/systemd/toss-paper-operation.service",
        "deploy/systemd/toss-paper-operation.timer",
        "src/toss_trading/broker/paper.py",
        "src/toss_trading/paper/operation.py",
        "src/toss_trading/cli/foundation_snapshot.py",
        "src/toss_trading/cli/foundation_audit.py",
        "src/toss_trading/cli/foundation_replay.py",
        "src/toss_trading/cli/paper_operation.py",
    )
    assert all(not (ROOT / path).exists() for path in removed_paths)

    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for entry_point in (
        "toss-foundation-snapshot",
        "toss-foundation-audit",
        "toss-foundation-replay",
        "toss-paper-operation",
    ):
        assert entry_point not in project


def test_canonical_alpha_management_has_no_legacy_runtime_reexport():
    legacy_alpha = ROOT / "src/toss_trading/alpha"
    assert not any(legacy_alpha.glob("**/*.py"))
    owner_map = (ROOT / "docs/alpha_management.md").read_text(encoding="utf-8")
    assert "toss_trading.alpha" not in owner_map


def test_research_release_audit_has_no_legacy_paper_compatibility_mode():
    audit = (ROOT / "scripts/audit_active_research_release.sh").read_text(
        encoding="utf-8"
    )
    assert "RESEARCH_AUDIT_REQUIRE_PAPER" not in audit
    assert "paper-runtime" not in audit
    assert "paper_operation" not in audit
