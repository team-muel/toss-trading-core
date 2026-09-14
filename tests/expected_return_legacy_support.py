"""Historical unit-test helper; deliberately absent from the installable package."""
from datetime import datetime
from decimal import Decimal
from typing import Mapping

from asset_management.domain.errors import DataQualityError
from asset_management.expectations.engine import COMPONENTS
from asset_management.expectations.equity import AGGREGATE_EQUITY_COMPONENTS, EquityGrowthBasis
from asset_management.expectations.models import AssetClass, ExpectedReturnComponent, ExpectedReturnEstimate
from asset_management.quality.models import QualityStatus


def aggregate_expected_return(*, instrument_id: str, asset_class: AssetClass,
                              components: Mapping[str, ExpectedReturnComponent], horizon: int,
                              as_of: datetime, transaction_cost: Decimal = Decimal(0),
                              tax_drag: Decimal = Decimal(0), fx_cost: Decimal = Decimal(0),
                              uncertainty_z: Decimal = Decimal("1.96"),
                              growth_basis: EquityGrowthBasis | None = None) -> ExpectedReturnEstimate:
    names = COMPONENTS[asset_class]
    if asset_class is AssetClass.EQUITY:
        growth_basis = EquityGrowthBasis.PER_SHARE if growth_basis is None else growth_basis
        names = AGGREGATE_EQUITY_COMPONENTS if growth_basis is EquityGrowthBasis.AGGREGATE else names
    elif growth_basis is not None:
        raise DataQualityError("EQUITY_GROWTH_BASIS_UNEXPECTED")
    if set(components) != set(names):
        raise DataQualityError("ASSET_CLASS_COMPONENT_MISMATCH")
    costs = (transaction_cost, tax_drag, fx_cost)
    if any(not item.is_finite() or item < 0 for item in costs) or uncertainty_z < 0:
        raise DataQualityError("EXPECTED_RETURN_COST_INVALID")
    ordered = tuple(components[name] for name in names)
    gross = sum(item.point_estimate for item in ordered); net = gross - sum(costs)
    uncertainty = sum(item.uncertainty for item in ordered)
    return ExpectedReturnEstimate(
        instrument_id, asset_class, horizon, ordered, gross, transaction_cost, tax_drag, fx_cost,
        net, net-uncertainty_z*uncertainty, net+uncertainty_z*uncertainty,
        min(item.confidence for item in ordered), QualityStatus.VALID, as_of,
        ordered[0].validity, growth_basis,
    )
