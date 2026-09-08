"""Convert weights to target quantities without creating orders."""
from decimal import Decimal,ROUND_DOWN
from asset_management.domain.errors import DataQualityError

def target_quantities(weights,prices,nav,lot_sizes):
    if (not len(weights)==len(prices)==len(lot_sizes) or nav<=0 or
            any(weight<0 for weight in weights) or any(price<=0 for price in prices) or any(lot<=0 for lot in lot_sizes)):
        raise DataQualityError("TARGET_QUANTITY_INPUT_INVALID")
    return tuple(((nav*weights[i]/prices[i]/lot_sizes[i]).to_integral_value(rounding=ROUND_DOWN))*lot_sizes[i]
                 for i in range(len(weights)))
