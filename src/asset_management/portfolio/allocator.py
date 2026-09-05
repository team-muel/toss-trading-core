"""Tactical overlays and security selection."""
from decimal import Decimal
from asset_management.domain.errors import DataQualityError
from .models import PortfolioTarget

def tactical_overlay(strategic: PortfolioTarget, deltas, bounds):
    if set(deltas)!=set(strategic.instruments) or set(bounds)!=set(strategic.instruments) or sum(deltas.values())!=0:
        raise DataQualityError("TACTICAL_OVERLAY_INVALID")
    weights=[]
    for name,base in zip(strategic.instruments,strategic.weights):
        low,high=bounds[name]; value=base+deltas[name]
        if not low<=value<=high: raise DataQualityError("TACTICAL_BOUND_EXCEEDED")
        weights.append(value)
    return PortfolioTarget(strategic.instruments,tuple(weights),"TACTICAL")

def select_securities(asset_weight: Decimal, scores):
    if asset_weight<0 or not scores or any(not x.is_finite() or x<0 for x in scores.values()) or sum(scores.values())<=0:
        raise DataQualityError("SECURITY_SELECTION_INVALID")
    total=sum(scores.values()); names=tuple(sorted(scores))
    return PortfolioTarget(names,tuple(asset_weight*scores[name]/total for name in names),"SECURITY_SELECTION")
