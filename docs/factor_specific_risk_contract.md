# AMA-46 Factor and Specific Risk Contract

`assess_factor_specific_risk` builds `Σ = B F B' + D` from point-in-time factor
exposures. Each exposure must be available at the information cutoff and have
the same assessment `as_of`; sparse residual history fails closed.

Specific variance is winsorized, shrunk to the approved floor, and clamped to
that positive floor. The assessment retains systematic, specific, and total
variance per instrument, a factor covariance matrix, currency basis, estimation
version, normal full-covariance disagreement, and residual regime sensitivity.
It never treats missing, stale, or zero specific risk as a valid result.
