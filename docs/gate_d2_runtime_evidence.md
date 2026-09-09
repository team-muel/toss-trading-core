# Gate D2 runtime evidence protocol

The three checks initially blocked at Gate D2 are derived from artifacts, not
caller-provided booleans:

- USD risk-free evidence requires one immutable FRED/ALFRED bronze
  `risk-free-curve` manifest for all 21, 63, 126, and 252 business-day
  horizons. The manifest and each point must be available at the requested
  information cutoff and use the approved currency,
  `BUS/252`, and `EFFECTIVE_ANNUAL` conventions.
- Factor/specific-risk evidence requires a USD-base assessment with a complete
  immutable Tiingo EOD source-manifest set, a known availability time, PSD covariance, and
  the validated specific-risk floor/decomposition already enforced by the
  assessment contract.
- Model-lineage evidence requires an active registry authorization, a matching
  model-calculation binding, and a calculation graph whose raw manifests verify
  in the immutable store.

`build_d2_gate_input` accepts the remaining static checks separately and
derives these three runtime checks from `assemble_d2_runtime_evidence`. It
rejects a caller that attempts to supply a runtime check directly. Missing,
stale, malformed, or unverifiable inputs remain failed checks and preserve
`permits_m5_execution=false`.

No collector run, FRED credential use, model activation, paper order, or live
trading authority is performed by this protocol.
