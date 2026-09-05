"""WorldQuant BRAIN-inspired alpha research package.

`asset_management` owns account/data/time truth and execution policy.
`alpha_management` owns alpha expression, simulation, and evaluation.

The alpha package may read validated values through explicit adapters, but it
must never call a broker or create an order directly.
"""

from .datafields import AssetDataSource, RepositoryDataFields
from .expression import Alpha, AlphaPositions, AlphaSimulationSettings, simulate_cross_section
from .metrics import SimulationResult, evaluate, fitness
from . import operators

__all__ = [
    "operators",
    "Alpha",
    "AlphaPositions",
    "AlphaSimulationSettings",
    "simulate_cross_section",
    "AssetDataSource",
    "RepositoryDataFields",
    "SimulationResult",
    "evaluate",
    "fitness",
]
