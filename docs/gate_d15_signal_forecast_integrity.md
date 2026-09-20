# Gate D1.5 — Signal & Forecast Integrity

Gate D1.5는 M3.5 Signal & Forecast Engineering에서 M4 실행 구현으로 넘어가기 전의
acceptance boundary다. Feature와 Signal의 semantic separation, versioned Signal의 economic
rationale·horizon·validity·lineage, cross-sectional diagnostics와 ETF time-series OOS
calibration/forecast-error/utility evidence를 모두 확인한다.

또한 PIT cross-section에서만 수행되는 normalization/neutralization, factor exposure와
incremental predictive power, forecast mapping의 OOS evidence와 uncertainty, correlation/cost/
stability를 반영한 forecast combination을 검증한다. Strategy Registry는 signal, forecast,
risk, portfolio, execution, benchmark, capital/risk budget 버전을 한 strategy version에
고정해야 한다. weak/unstable signal은 shrink 또는 ABSTAIN할 수 있어야 하며, look-ahead,
survivorship, leakage는 모두 차단돼야 한다.

입력은 정확한 check set과 immutable evidence artifact ID를 요구한다. `pytest:` selector,
short SHA, 또는 source revision과 불일치하는 evidence revision은 진단용 historical metadata일
뿐 acceptance evidence가 아니다. PASS는 `git:<40-hex>` exact source revision과 그와 동일한
evidence source revision, 그리고 각 check의 `sha256:<64-hex>` immutable artifact ID를 모두
요구한다. 하나라도 unknown, missing, failed, unverifiable, 또는 source-mismatched이면 결과는
`FAIL`이고 `permits_m4_execution`은 false다. PASS 결과는 canonical content hash로 재현한다.
Artifact ID는 `signal-forecast-gate-evidence@1` catalog object를 가리켜야 하며, gate는
ImmutableDatasetStore에서 exact bytes, content address, check name, evidence source revision을
다시 대조한다. 또한 read-only Git source verifier가 trusted checkout의 current `HEAD`가 exact
commit과 그 tree object를 실제로 resolve해야 하며, catalog record의 tree identity도 일치해야 한다.
과거 commit은 동일 repository에 남아 있어도 current acceptance authority가 아니다. 단순히 SHA
모양의 문자열을 제공하거나 store에 self-attested marker를 쓰는 것으로는 PASS가 될 수 없다.
`docs/evidence/gate_d15_signal_forecast_integrity_2026-09-06.json`은 이 stronger contract보다
앞선 historical record이며, 현재 M4 promotion authority가 아니다. 이 gate는 주문을 생성하거나
`live_trading_enabled=false` 설정을 변경하지 않는다.
