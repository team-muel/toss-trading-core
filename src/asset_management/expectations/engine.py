"""Expected-return aggregation with distinct asset-class paths."""
from asset_management.domain.errors import DataQualityError
from .bond import BOND_ETF_COMPONENTS
from .cash import CASH_COMPONENTS
from .equity import EQUITY_COMPONENTS
from .etf import COMMODITY_ETF_COMPONENTS, EQUITY_ETF_COMPONENTS
from .models import AssetClass

COMPONENTS = {AssetClass.EQUITY: EQUITY_COMPONENTS, AssetClass.EQUITY_ETF: EQUITY_ETF_COMPONENTS,
              AssetClass.BOND_ETF: BOND_ETF_COMPONENTS, AssetClass.CASH: CASH_COMPONENTS,
              AssetClass.COMMODITY_ETF: COMMODITY_ETF_COMPONENTS}

def expected_return(**_: object) -> None:
    """Retired caller-assembled route; production must consume persisted evidence."""
    raise DataQualityError("DIRECT_EXPECTED_RETURN_ASSEMBLY_RETIRED")
