from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
import yaml

from asset_management.domain.errors import ConfigurationError
from asset_management.orchestration import (
    DecisionRuntime, PricingApplicabilityEvidence, RuntimeAdapterDescriptor,
)
from asset_management.orchestration.pipelines import PipelineEvidenceRepository
from asset_management.orchestration.runtime import ApplicationRuntime
from asset_management.config.loader import load_policy_registry
from asset_management.time.clock import FrozenClock


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 4, 1, tzinfo=timezone.utc)


def test_real_read_only_configuration_boots_and_applies_migrations_once():
    conn = sqlite3.connect(":memory:")
    kwargs = dict(
        config_path=ROOT / "config/application.yaml",
        policy_registry_path=ROOT / "config/policy_registry.yaml",
        schema_root=ROOT / "schemas",
        conn=conn,
        clock=FrozenClock(NOW),
    )
    first = ApplicationRuntime.boot(**kwargs)
    second = ApplicationRuntime.boot(**kwargs)
    assert first.migration_versions == tuple(range(1, 19))
    assert second.migration_versions == ()
    assert first.policies is not None
    assert first.policies.policies["data"].is_effective(NOW)


def test_runtime_exposes_typed_canonical_decision_adapter():
    raw_config = yaml.safe_load((ROOT / "config/application.yaml").read_text(encoding="utf-8"))
    runtime = ApplicationRuntime.start(raw_config, FrozenClock(NOW))
    adapter = runtime.decision_adapter(
        kernel_version="decision-kernel@1",
        descriptor=RuntimeAdapterDescriptor(
            DecisionRuntime.HISTORICAL_REPLAY, "clock@1", "data@1", "broker@1",
            "execution@1", "persistence@1",
        ),
        repository=PipelineEvidenceRepository(sqlite3.connect(":memory:")),
        runtime_run_id="runtime@1",
        pricing_applicability_evidence=PricingApplicabilityEvidence.create(
            scope_key="asset-class:equity", applicable=True, reason=None,
            policy_version="pricing-applicability@1",
        ),
    )
    assert adapter.descriptor.runtime is DecisionRuntime.HISTORICAL_REPLAY


def test_draft_trading_policies_block_non_read_only_boot(tmp_path):
    config = (ROOT / "config/application.yaml").read_text(encoding="utf-8").replace(
        "runtime_mode: READ_ONLY", "runtime_mode: PAPER"
    )
    path = tmp_path / "application.yaml"
    path.write_text(config, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not approved"):
        ApplicationRuntime.boot(
            config_path=path,
            policy_registry_path=ROOT / "config/policy_registry.yaml",
            schema_root=ROOT / "schemas",
            conn=sqlite3.connect(":memory:"),
            clock=FrozenClock(NOW),
        )


def test_policy_document_tampering_is_detected(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    for source in (ROOT / "docs").glob("*_policy.md"):
        (docs / source.name).write_bytes(source.read_bytes())
    registry_dir = tmp_path / "config"
    registry_dir.mkdir()
    registry = registry_dir / "policy_registry.yaml"
    registry.write_bytes((ROOT / "config/policy_registry.yaml").read_bytes())
    (docs / "data_policy.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_policy_registry(registry, repository_root=tmp_path)
