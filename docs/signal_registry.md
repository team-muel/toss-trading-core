# AMA-102 — Signal Definition Registry and Transform Contract

A Feature is an observed or calculated point-in-time input. A Signal is a separately versioned,
economically motivated transformation of one or more Feature snapshots. A Signal never represents
a BUY, SELL, target weight, or forecast return.

Each `SignalDefinition` records its identifier and version, economic rationale, source Feature IDs,
transform/ranking/normalization/neutralization rules, directionality, cross-sectional or
time-series type, horizon-validity-decay contract, historical universe and currency, minimum
coverage/history, missing and outlier policy, expected turnover, cost sensitivity, formula version,
and parameter set. No rule may be omitted: a no-op is recorded explicitly as `NONE`.

`SignalRegistry` is independent of `FeatureRegistry`. A conflicting definition for the same
`signal_id@version` fails closed; a changed contract must use a new version. Registry snapshots are
content-addressed.

`SignalStore` accepts only a complete historical universe and Feature snapshots whose manifest,
instrument, Feature ID, input universe manifest, `as_of`, information cutoff, and horizon all
match the requested context. It verifies the historical Feature manifest IDs and every current
Feature manifest before applying the registered transform. A current universe cannot be substituted
for the historical universe, and a Feature snapshot available after the cutoff is rejected.

The transform receives only eligible point-in-time Feature values. Insufficient history or coverage,
unverified lineage, quality blockage, an unexpected transform, malformed output, expiry, or a
universe mismatch returns `ABSTAIN` and publishes no Signal snapshot. A successful snapshot stores
the separate semantic type `SIGNAL_VALUE`, Feature manifest lineage, formula/parameter versions,
coverage, and a canonical output hash.
