# Gate D2 runtime evidence protocol

The three checks initially blocked at Gate D2 are derived from artifacts, not
caller-provided booleans:

- USD risk-free evidence requires one immutable FRED/ALFRED bronze
  `risk-free-curve` manifest for all 21, 63, 126, and 252 business-day
  horizons. The manifest and each point must be available at the requested
  information cutoff and use the approved currency,
  `BUS/252`, and `EFFECTIVE_ANNUAL` conventions.
- Factor/specific-risk calculations can be recorded as `MECHANISM_ONLY` v2
  artifacts with exact covariance, policy and parent references. They do **not**
  pass the runtime gate. Existing code cannot yet replay the estimator from a
  complete, instrument-bound raw-return input set. It therefore returns
  `FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED` for old v1 and new v2 records.
  Adding a Tiingo source name, arbitrary parent or stored copy of an assessment
  is not estimator provenance. Assessment and policy estimator versions must
  agree even for recording mechanism evidence.
- Model-lineage evidence requires an active registry authorization, a matching
  model-calculation binding, and a calculation graph whose raw manifests verify
  in the immutable store. It is published as a gold `model-lineage-evidence`
  artifact containing the exact registry, authorization, binding, and graph
  payloads, with all traced raw manifests retained as immutable parents.

`build_d2_gate_input` accepts the remaining static checks separately and the
actual typed runtime evidence sources. It recomputes checks at the evaluation
cutoff through `assemble_d2_runtime_evidence`; a mutable/caller-constructed
`D2RuntimeEvidenceResult` is not an authority argument. It
rejects a caller that attempts to supply a runtime check directly. Missing,
stale, malformed, or unverifiable inputs remain failed checks and preserve
`permits_m5_execution=false`.

No collector run, FRED credential use, model activation, paper order, or live
trading authority is performed by this protocol.

`materialize_usd_fred_risk_free_curve` is the pure input boundary for the
risk-free artifact. It opens one verified `fred-alfred` bronze
`risk-free-curve` manifest and requires exactly DGS1MO, DGS3MO, DGS6MO, and
DGS1 observations with common `as_of`, explicit availability, and decimal-safe
percent values. It does not interpolate tenors or backfill missing observations.

The risk-free runtime check rematerializes the four-tenor curve using
`materialize_usd_fred_risk_free_curve` and compares the supplied economic values
against that artifact-derived result. Empty bodies or caller-invented rates
cannot pass by supplying correct-looking manifest metadata.
