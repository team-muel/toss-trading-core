"""Compatibility and integration surface for alpha research.

The canonical expression, operator, simulation, and evaluation implementation
lives in :mod:`alpha_management`. Existing callers may keep this import path;
the only runtime-specific behavior retained here is position-to-Signal adaptation
and provider-specific legacy datafield compatibility.

This surface never enables live trading. ``RiskHub`` and the normal execution
gates remain authoritative.
"""

from . import metrics, operators
from .expression import (
    Alpha,
    AlphaPositions,
    SimulationSettings,
    simulate_cross_section,
    to_signals,
)
from .metrics import SimulationResult, evaluate, fitness

__all__ = [
    "operators",
    "metrics",
    "Alpha",
    "AlphaPositions",
    "SimulationSettings",
    "simulate_cross_section",
    "to_signals",
    "SimulationResult",
    "evaluate",
    "fitness",
]
