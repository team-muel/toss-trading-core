"""Provider adapters return raw observations and never portfolio decisions."""

from asset_management.data.immutable import IngestionResult, ProviderDatasetAdapter
from asset_management.data.adapters.toss import TossDatasetAdapter

__all__ = ["IngestionResult", "ProviderDatasetAdapter", "TossDatasetAdapter"]
