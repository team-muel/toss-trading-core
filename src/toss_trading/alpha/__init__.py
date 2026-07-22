"""Research-only alpha authoring layer.

A small, dependency-free vocabulary for writing and scoring *fast-expression*
alphas (in the spirit of WorldQuant BRAIN) on top of this repository's Toss
foundation.  See ``docs/18_alpha_expression_language.md`` for the concept map
between the terms used here and the existing foundation terms.

This layer only produces decision inputs and ``Signal`` proposals.  It never
enables live trading; the existing ``RiskHub`` and live gates remain the sole
authority over execution.
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
