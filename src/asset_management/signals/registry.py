"""Registry for versioned signal contracts, separate from FeatureRegistry."""
from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation

from .models import SignalDefinition


class SignalRegistry:
    def __init__(self, definitions: Iterable[SignalDefinition] = ()) -> None:
        self._definitions: dict[str, SignalDefinition] = {}
        for definition in definitions:
            self.register(definition)

    @property
    def definitions(self) -> Mapping[str, SignalDefinition]:
        return MappingProxyType(self._definitions)

    def register(self, definition: SignalDefinition) -> None:
        if not isinstance(definition, SignalDefinition):
            raise InvariantViolation("SIGNAL_DEFINITION_INVALID")
        previous = self._definitions.get(definition.key)
        if previous is not None and previous != definition:
            raise InvariantViolation("SIGNAL_DEFINITION_CONFLICT")
        self._definitions[definition.key] = definition

    def get(self, signal_id: str, version: str) -> SignalDefinition:
        try:
            return self._definitions[f"{signal_id}@{version}"]
        except KeyError:
            raise InvariantViolation("SIGNAL_DEFINITION_MISSING") from None

    def payload(self) -> dict[str, object]:
        body = {"definitions": [self._definitions[key].payload() for key in sorted(self._definitions)]}
        return {**body, "registry_hash": digest(canonical(body))}

    def publish(self, store: ImmutableDatasetStore) -> str:
        return store.catalog("signal-registry", self.payload())
