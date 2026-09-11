"""Test-only builder for persisted runtime model-governance evidence.

It deliberately uses the same append-only repository and migration as production;
it does not provide a Decision Gate fixture or an in-memory authorization bypass.
"""
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.governance import ModelScope, RuntimeModelRegistryEvidenceRepository
from asset_management.time.clock import FrozenClock, ReplayClock


ROOT = Path(__file__).parents[1]


def persisted_runtime_authorization(registry, *, model_key: str, scope: ModelScope,
                                    as_of: datetime, information_cutoff: datetime):
    conn = sqlite3.connect(":memory:")
    Migrator(conn, FrozenClock(as_of)).migrate(load_migration_catalog(ROOT / "schemas"))
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)", (
        "runtime@pricing-test", as_of.isoformat(), information_cutoff.isoformat(),
        "git:test", information_cutoff.isoformat(),
    ))
    clock = ReplayClock(information_cutoff - timedelta(seconds=5))
    repository = RuntimeModelRegistryEvidenceRepository(conn, clock)
    for transition in registry._transitions:
        for evidence_id in transition.evidence_ids:
            repository.record_review_evidence(
                evidence_id, model_key=transition.model_key, from_status=transition.from_status,
                to_status=transition.to_status, owner=registry.models[transition.model_key].owner,
                evidence={"review": "test-approved", "transition": transition.to_status.value},
            )
    clock.advance_to(information_cutoff - timedelta(seconds=1))
    snapshot = repository.publish_snapshot(registry)
    clock.advance_to(information_cutoff)
    repository.bind_runtime_run("runtime@pricing-test", snapshot)
    return repository, repository.authorize("runtime@pricing-test", model_key=model_key, scope=scope)
