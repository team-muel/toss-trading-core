"""Provider adapters return raw observations and never portfolio decisions."""

from asset_management.data.immutable import IngestionResult, ProviderDatasetAdapter

__all__ = ["IngestionResult", "ProviderDatasetAdapter"]
