"""Named portfolio exposures."""
from decimal import Decimal
from asset_management.domain.errors import DataQualityError

EXPOSURES=("market_beta","sector","industry","value","momentum","quality","growth","duration","credit","currency","liquidity")
def aggregate_exposure(weights, instrument_exposures):
    weights=tuple(weights); rows=tuple(instrument_exposures)
    if (len(weights)!=len(rows) or any(not x.is_finite() for x in weights) or
            any(set(row)!=set(EXPOSURES) or any(not x.is_finite() for x in row.values()) for row in rows)):
        raise DataQualityError("EXPOSURE_INPUT_INVALID")
    return {name:sum(weights[i]*rows[i][name] for i in range(len(weights))) for name in EXPOSURES}
