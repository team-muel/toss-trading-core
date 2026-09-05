# Phase 7: reference information and historical universe

Canonical instrument IDs are opaque UUIDs. Tickers are attributes, never primary
keys. Instrument versions store ticker, Toss/vendor symbols, optional CIK, MIC,
asset class, currency and IANA timezone. Missing CIK is explicit, never fabricated.

All reference versions are immutable and source-attributed. Effective intervals
are half-open; available_at is the knowledge-time boundary. A correction appends
a complete replacement for the same entity key with a later availability time.
Queries select the latest known version before testing the effective interval.
Revisions never overwrite the history needed for an earlier AsOfContext.

Aliases are provider-qualified and dated independently. Missing or ambiguous
resolution fails closed. Universe membership includes its reason and source;
queries intersect historical memberships with listing intervals, excluding
prelisting and delisted instruments. Unknown universe history is an error.

Exchange sessions explicitly describe local date, timezone, open/closed status,
regular/premarket/afterhours timestamps and early closure. UTC timestamps must map
to the stated local date. Holidays and missing sessions are not guessed. Calendar
data must be supplied from an identified source; fixture dates are not live data.

Corporate action records support dividend, split, reverse split, merger, spinoff,
delisting and ticker change. Split reconciliation checks exact quantity ratios
and inverse reference-price ratios with an explicit price tolerance. This compares
adjustment reference prices, not actual market prices across a trading interval.
Comparisons retain their inputs, run context and MATCH/MISMATCH result in an
append-only journal; retrying an identical comparison does not duplicate it.
Complex actions need explicit terms and manual reconciliation; no automatic ledger
mutation or synthetic cash is performed. Delisting events also directly exclude
the instrument from Universe and post-delisting prices. A ticker change requires
dated alias records. canonicalize_order maps Toss symbols to instrumentId before
orders enter a canonical ledger; existing unverified ticker-keyed records are not
silently remapped.

PriceObservationStore checks canonical identity and listing eligibility before PIT
storage. Price fields distinguish raw, split_adjusted and total_return. Consumers
must call require_price_basis: account accounting accepts raw prices; total-return
prices cannot be combined with cash dividends. calculate_price_return enforces
these checks on stored observations and checks both availability times against
AsOfContext. Broader financial calculation modules must preserve this boundary.

Migration 13 stores append-only hashed reference versions. The tests in
test_phase7_reference.py cover identity continuity, knowledge-time revisions,
historical universe, listing boundaries, price-basis errors, corporate-action
reconciliation and calendar DST/early-close cases.
