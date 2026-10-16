# AMA-180 — Regime foundation retirement and acceptance

AMA-180 closes the pre-heuristic State/regime foundation. It does not select a regime
classifier and does not grant new Forecast, Risk, Portfolio, Execution, or broker authority.

## Final disposition

| Surface | Disposition | Result |
| --- | --- | --- |
| Generic `StateEngine._derive_regime` / `derive_regime` | DELETE | Removed before AMA-180; regression tests assert the generic State engine has no regime derivation method. |
| `StateSnapshot.regime_label` | DELETE | Removed from the dataclass, payload, writer, and JSON schema. Canonical snapshots are `state-snapshot-v3`. Historical v2 payloads are historical artifacts, not a writable compatibility contract. |
| Typed `RegimeModelSpec` / `RegimeSnapshot` | RETAIN | Separate probability/uncertainty/lineage representation. It carries no direct decision authority. |
| Canonical ALFRED/PIT macro-state evidence | RETAIN | Raw-first vintage lineage and canonical macro-state mechanism remain available as evidence. |
| `MacroRegimeConfig` + `run_macro_regime_backtest` | READ-ONLY HISTORICAL | Kept only in `toss_trading.research.backtest` to reproduce prior research. Removed from active policy, new hypothesis registration, canonical candidate evaluation, and public `toss_trading.research` exports. |
| Autonomous `macro_regime` research family | DELETE FROM ACTIVE POLICY | New proposals and attempts to reactivate it fail closed. Existing historical files remain readable. |
| State `operational_state` / `risk_multiplier` | RETAIN, NON-AUTHORITY | Retained as state-local data-quality/operational restriction output. Static acceptance requires no production consumer outside `asset_management.states`. |
| AMA-179 regime uncertainty adapter | RETAIN | The only reviewed regime-to-risk boundary. It feeds traceable evidence into the existing RiskGovernor; it does not issue approvals itself. |

## Authority boundary

The canonical path after closure is:

`PIT data -> FeatureSnapshot -> MarketState -> optional RegimeSnapshot -> typed uncertainty evidence -> existing RiskGovernor`

The following paths are forbidden:

- State or Regime -> portfolio target
- State or Regime -> BUY/SELL / OrderIntent
- State or Regime -> broker write
- retrospective/smoothed Regime output -> decision-time risk evidence
- historical `MacroRegimeConfig` -> canonical candidate evaluation or autonomous proposal
- State-local `risk_multiplier` -> external portfolio/risk authority

The numerical State `risk_multiplier` is intentionally not renamed in this retirement
slice because it is part of the State v3 operational-quality representation and repository
inspection found no external source consumer. The acceptance test makes that isolation
fail closed if a future production module starts consuming it.

## PIT and provenance acceptance

The foundation preserves:

- explicit `as_of` and `information_cutoff`
- immutable source Feature publishing manifests
- exact FeatureDefinition catalog identity in MarketState component evidence
- raw/silver manifest lineage
- code revision and parameter/formula identity
- quality, freshness, confidence, and unavailable-state semantics
- filtered/causal versus retrospective regime semantics

AMA-179 additionally requires adapter-issued, content-addressed uncertainty evidence before
`RiskInputs.regime_uncertain` can enter the RiskGovernor.

## Post-closure sequencing

AMA-180 is the gate for the separate descriptive heuristic layer. NBER chronology,
real-time Sahm Rule, and market drawdown/MDD observations must be implemented after this
closure and must not reuse the retired direct allocation path. Data-driven regime inference
remains sequenced after the heuristic reference surface.
