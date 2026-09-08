"""Expected-return aggregation with distinct asset-class paths."""
from datetime import datetime
from decimal import Decimal
from typing import Mapping
from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus
from .bond import BOND_ETF_COMPONENTS
from .cash import CASH_COMPONENTS
from .equity import EQUITY_COMPONENTS, AGGREGATE_EQUITY_COMPONENTS, EquityGrowthBasis
from .etf import COMMODITY_ETF_COMPONENTS, EQUITY_ETF_COMPONENTS
from .models import AssetClass, ExpectedReturnComponent, ExpectedReturnEstimate

COMPONENTS = {AssetClass.EQUITY: EQUITY_COMPONENTS, AssetClass.EQUITY_ETF: EQUITY_ETF_COMPONENTS,
              AssetClass.BOND_ETF: BOND_ETF_COMPONENTS, AssetClass.CASH: CASH_COMPONENTS,
              AssetClass.COMMODITY_ETF: COMMODITY_ETF_COMPONENTS}

def expected_return(*, instrument_id: str, asset_class: AssetClass,
                    components: Mapping[str, ExpectedReturnComponent], horizon: int, as_of: datetime,
                    transaction_cost: Decimal = Decimal(0), tax_drag: Decimal = Decimal(0),
                    fx_cost: Decimal = Decimal(0), uncertainty_z: Decimal = Decimal("1.96"),
                    growth_basis: EquityGrowthBasis | None = None) -> ExpectedReturnEstimate:
    if not isinstance(asset_class, AssetClass):
        raise DataQualityError("ASSET_CLASS_INVALID")
    names = COMPONENTS[asset_class]
    if asset_class is AssetClass.EQUITY:
        growth_basis = EquityGrowthBasis.PER_SHARE if growth_basis is None else growth_basis
        if not isinstance(growth_basis, EquityGrowthBasis):
            raise DataQualityError("EQUITY_GROWTH_BASIS_INVALID")
        if growth_basis is EquityGrowthBasis.AGGREGATE:
            names = AGGREGATE_EQUITY_COMPONENTS
    elif growth_basis is not None:
        raise DataQualityError("EQUITY_GROWTH_BASIS_UNEXPECTED")
    if set(components) != set(names): raise DataQualityError("ASSET_CLASS_COMPONENT_MISMATCH")
    costs = (transaction_cost, tax_drag, fx_cost)
    if any(not x.is_finite() or x < 0 for x in costs) or uncertainty_z < 0:
        raise DataQualityError("EXPECTED_RETURN_COST_INVALID")
    ordered = tuple(components[name] for name in names)
    if any(item.component_name != name for name,item in zip(names,ordered)):
        raise DataQualityError("EXPECTED_RETURN_COMPONENT_NAME_CONFLICT")
    gross = sum(x.point_estimate for x in ordered); net = gross-sum(costs)
    # Correlations are not supplied, so use the conservative triangle bound.
    uncertainty = sum(x.uncertainty for x in ordered)
    confidence = min(x.confidence for x in ordered)
    quality = QualityStatus.VALID if all(x.confidence > 0 and x.uncertainty.is_finite() for x in ordered) else QualityStatus.MISSING
    validity = ordered[0].validity
    return ExpectedReturnEstimate(instrument_id, asset_class, horizon, ordered, gross,
                                  transaction_cost, tax_drag, fx_cost, net,
                                  net-uncertainty_z*uncertainty, net+uncertainty_z*uncertainty,
                                  confidence, quality, as_of, validity, growth_basis)
