"""Validated composition root. Domain modules never import orchestration."""

from dataclasses import dataclass
from typing import Mapping
from pathlib import Path
import sqlite3

import yaml

from asset_management.config.loader import load_policy_registry
from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.config.schemas import PolicyRegistry
from asset_management.config.validation import ValidatedConfig, validate_startup_config
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import (
    ModelRegistry, ModelScope, RuntimeModelAuthorization, RuntimeModelRegistryEvidenceRepository,
)
from asset_management.time.clock import Clock
from .decision_kernel import (
    DecisionKernel, DecisionRuntimeAdapter, PricingApplicabilityEvidence, RuntimeAdapterDescriptor,
)
from .pipelines import PipelineEvidenceRepository


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    config: ValidatedConfig
    clock: Clock
    policies: PolicyRegistry | None = None
    migration_versions: tuple[int, ...] = ()
    model_registry_evidence: RuntimeModelRegistryEvidenceRepository | None = None

    @classmethod
    def start(cls, raw_config: Mapping[str, object], clock: Clock) -> "ApplicationRuntime":
        """Validate every startup invariant before constructing the runtime."""

        return cls(validate_startup_config(raw_config), clock)

    @classmethod
    def boot(
        cls,
        *,
        config_path: str | Path,
        policy_registry_path: str | Path,
        schema_root: str | Path,
        conn: sqlite3.Connection,
        clock: Clock,
    ) -> "ApplicationRuntime":
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            from asset_management.domain.errors import ConfigurationError

            raise ConfigurationError("application configuration must be an object")
        config = validate_startup_config(raw)
        policies = load_policy_registry(policy_registry_path)
        required = ("data", "temporal", "promotion")
        if config.runtime_mode != "READ_ONLY":
            required += ("investment", "risk", "execution", "tax")
        policies.require_effective(required, clock.now_utc())
        catalog = load_migration_catalog(schema_root)
        applied = Migrator(conn, clock).migrate(catalog)
        return cls(config, clock, policies, applied, RuntimeModelRegistryEvidenceRepository(conn, clock))

    def record_model_review_evidence(self, evidence_id: str, **review) -> str:
        """Persist the reviewed lifecycle transition before registry publication."""
        return self._model_registry_evidence().record_review_evidence(evidence_id, **review)

    def publish_model_registry_snapshot(self, registry: ModelRegistry) -> str:
        """Persist governance evidence before any runtime may select it."""
        return self._model_registry_evidence().publish_snapshot(registry)

    def select_model_registry(self, runtime_run_id: str, model_registry_snapshot_id: str) -> str:
        """Bind one pre-existing model-registry snapshot to one runtime run."""
        return self._model_registry_evidence().bind_runtime_run(runtime_run_id, model_registry_snapshot_id)

    def authorize_runtime_model(self, runtime_run_id: str, *, model_key: str,
                                scope: ModelScope) -> RuntimeModelAuthorization:
        return self._model_registry_evidence().authorize(runtime_run_id, model_key=model_key, scope=scope)

    def _model_registry_evidence(self) -> RuntimeModelRegistryEvidenceRepository:
        if self.model_registry_evidence is None:
            raise InvariantViolation("MODEL_RUNTIME_EVIDENCE_NOT_BOOTED")
        return self.model_registry_evidence

    def decision_adapter(self, *, kernel_version: str, descriptor: RuntimeAdapterDescriptor,
                         repository: PipelineEvidenceRepository, runtime_run_id: str,
                         pricing_applicability_evidence: PricingApplicabilityEvidence) -> DecisionRuntimeAdapter:
        """Expose the canonical pre-execution kernel from the composition root."""

        return DecisionRuntimeAdapter(
            DecisionKernel(kernel_version), descriptor, repository=repository,
            runtime_run_id=runtime_run_id,
            pricing_applicability_evidence=pricing_applicability_evidence,
        )
