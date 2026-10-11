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

## 모델 출력의 시간 계약

`bind_authorized_model_calculation`은 기존 runtime registry authorization을 다시
검증한 뒤 final output의 `as_of`와 완전한 `validity`를 요구한다. `validity`는
forecast/holding horizon, expiry, decay profile, half-life를 모두 명시하며 공통
`SignalValidity.from_payload`로 읽는다. 누락된 값이나 숫자 형식을 추론하지 않는다.
선택적인 output `horizon`도 forecast horizon과 일치해야 한다.

생성 시각은 timezone-aware여야 하고 binding 시각보다 미래일 수 없다. 생성 시각
이전에 만료된 출력과 binding 시각에 이미 만료된 출력은 거부한다. 생성 시각과
validity는 immutable final node와 binding hash에 함께 포함된다. binding은 decay를
출력 값에 다시 적용하지 않으며 소비자는 기존 effective-weight 계약을 사용한다.

시간 계약이 없는 과거 graph는 일반 lineage로 보존할 수 있지만 모델 authorization
binding으로 승격할 수 없다. 이 API 보강은 실제 producer 연결, matured OOS 증거,
paper 또는 live acceptance를 증명하지 않는다.

실제 persisted economic input을 읽는 `ComponentForecastAssembler`도 생성 경계에서
exact integer horizon과 typed validity를 요구한다. information cutoff에 이미 만료된
validity는 계산·저장 전에 거부하며 DB 상태를 변경하지 않는다. 이 producer는 자체
observation/manifest lineage와 runtime model evidence를 소비한다. 별도의 일반 graph
binding을 호출하지 않는다는 이유만으로 해당 authority를 대체하거나 중복하지 않는다.
