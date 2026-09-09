# Canonical ALFRED macro state (AMA-164)

The retained economic hypothesis is the four-state yield-curve, inflation-trend,
unemployment-trend and policy-rate-trend model. It does not become an ETF
allocation rule. A common macro state is not ranked cross-sectionally over assets.

## Existing owners, new adapters

`asset_management.data.alfred.ingest_alfred` accepts a decoded ProviderBatch and
uses the existing ProviderDatasetAdapter and ImmutableDatasetStore for raw-first
bronze/silver storage. There is no HTTP client, new lake, credential store,
forecast engine or independent trading runtime. The input source/dataset,
endpoint, request units, output type and schema are explicit. Partial API pages
are rejected until the acquisition owner assembles the response.

`asset_management.features.macro_state.run_macro_state` reads pinned canonical
manifests, rechecks their raw parents and recomputes normalized rows before
selecting vintages. It returns a content-addressed MACRO_STATE receipt whose
scope is MECHANISM_ONLY. It never produces weights or orders and does not grant
FeatureStore publication, OOS acceptance or model authorization.

## Time and revision contract

FRED documents inclusive realtime_start/realtime_end boundaries:
https://fred.stlouisfed.org/docs/api/fred/realtime_period.html
The endpoint supports real-time-period observations and new/revised vintage
columns: https://fred.stlouisfed.org/docs/api/fred/series_observations.html

The normalizer accepts complete output-type 1 or 3 responses. Type 3 intervals
end one day before the next known revision. Type 1 overlapping intervals and
conflicting duplicate vintages fail closed. A provider '.' value remains a
versioned missing tombstone instead of falling back to an obsolete number.

Public vintage dates remain dates. This model uses an explicitly versioned
America/Chicago calendar-date lag (1 to 7 days, default 1), not an invented
intraday official release timestamp. Exchange-session effectiveness must be
supplied by the outer reference/calendar owner. This is a conservative research
convention, not proof of same-day tradability or a universal provider timezone.

Manifest collection availability and historical public availability are separate.
The existing data contract requires each manifest to have been available by the
AsOfContext cutoff. Downloading an archive today does not make it a historically
ingested dataset. A historical replay needs retained admissible artifacts or a
separately reviewed reconstruction contract. The adapter does not backdate
collection timestamps to make a test pass.

## State calculation and availability

The original four direction tests use Decimal arithmetic. CPI year-over-year
change requires contiguous monthly periods, not merely a row count. Unemployment
and policy-rate changes also require the stated monthly window. Rate observations
must have aligned dates. Stale, missing, expired-latest or conflicting evidence
cannot yield a COMPUTED state.

The receipt includes selected vintages, policy/spec identity, context, manifests
and an information-set hash. Repeating monthly information on another day does
not manufacture additional monthly observations. The period counts are inventory
counts, not an estimate of statistical independence. OOS regime/forecast evaluation
remains AMA-166.

## Evidence and migration boundaries

26 synthetic tests cover actual bronze/silver ingestion, parity with the retained
macro model, inclusive intervals, revision lag, timezone boundaries, missing
tombstones, partial pages, malformed/nonfinite values, monthly gaps, staleness,
expired-latest evidence, raw-to-silver mismatch and unavailable historical
collection artifacts. The comparison imports research_platform from AMA-156.
Required exact-head CI and review are recorded in the PR and Linear.

No runtime consumer is switched and no legacy backtester is removed here. Actual
acquisition completeness, real-data OOS acceptance and allocation/backtest
retirement require the subsequent integration and parity stages.
