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

입력은 정확한 check set과 immutable evidence artifact ID를 요구한다. 하나라도 unknown,
missing, failed이면 결과는 `FAIL`이고 `permits_m4_execution`은 false다. PASS 결과는 canonical
content hash로 재현한다. 이 gate는 주문을 생성하거나 `live_trading_enabled=false` 설정을
변경하지 않는다.
