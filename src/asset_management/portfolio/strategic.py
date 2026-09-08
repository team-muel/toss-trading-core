"""Long-horizon strategic allocation."""
from decimal import Decimal
from asset_management.domain.errors import DataQualityError
from .models import PortfolioTarget

STRATEGIC_ASSET_CLASSES=("EQUITY","BOND","CASH","GOLD_COMMODITY")
def strategic_allocation(weights):
    if set(weights)!=set(STRATEGIC_ASSET_CLASSES) or any(not x.is_finite() or x<0 for x in weights.values()) or sum(weights.values())!=1:
        raise DataQualityError("STRATEGIC_ALLOCATION_INVALID")
    return PortfolioTarget(STRATEGIC_ASSET_CLASSES,tuple(weights[name] for name in STRATEGIC_ASSET_CLASSES),"STRATEGIC")
