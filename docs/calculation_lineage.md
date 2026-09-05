# AMA-37 — Calculation Lineage Graph

`CalculationLineageGraph`은 최종 추정치를 `RAW_DATA → FEATURE →
INTERMEDIATE_CALCULATION → FINAL_ESTIMATE` 순서로 역추적한다. 각 node는 공식 버전,
parameter set, 정렬된 input node ID, 중간값, 실제 output과 그 SHA-256 hash를 보존한다.
node ID와 graph hash도 canonical JSON에서 결정적으로 계산된다.

raw root는 `ImmutableDatasetStore`의 검증된 Bronze manifest에서만 생성할 수 있다.
그래프 검증과 publication은 manifest와 content hash를 다시 읽어 확인한다. 부모가
없거나, 계층을 건너뛰거나, output/node hash가 충돌하거나, 네 단계 중 하나가 빠지면
실패로 닫힌다. 동일 계산 node를 여러 후속 계산이 공유할 수 있지만 최종 추정치에서
도달할 수 없는 node는 추적 결과에 포함되지 않는다.

그래프 publication은 content-addressed catalog에 저장하며 주문을 생성하거나 실거래
권한을 변경하지 않는다. `live_trading_enabled` 기본값은 계속 `false`다.
