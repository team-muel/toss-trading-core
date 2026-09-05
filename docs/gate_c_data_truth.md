# AMA-30 — Gate C Data Truth Acceptance

Gate C는 데이터 및 품질 계층의 일곱 필수 검사를 증거 artifact/run ID와 함께
판정한다. Silver-to-raw lineage, 필수 시간·vintage·quality 필드, 잘못된 품질값의
비정상 정상화 방지, source health와 fallback/quarantine, 시장·FX·무위험금리·거시·
기업 데이터의 point-in-time 처리, 역사적 consensus의 UNKNOWN 유지, provider/license
metadata 중 하나라도 실패하면 `FAIL`이다. 누락된 check, `None` 상태, 비어 있거나
중복된 evidence ID는 판정 입력으로 인정하지 않는다.

결과는 PASS/FAIL, 실패 reason code, 정렬된 evidence IDs, UTC 평가시각,
code revision과 canonical SHA-256 hash를 보존한다. FAIL 결과는 M3 실행 구현
승격의 근거로 사용할 수 없다. Gate는 누락·충돌·stale 데이터를 추정하거나
정상값으로 보정하지 않는다.

2026-09-06 공식 구현 판정은 `docs/evidence/gate_c_data_truth_2026-09-06.json`에
기록한다. 이 판정은 테스트 fixture와 코드 계약에 대한 PASS다. 실제 공급자 데이터의
현재 가용성·최신성과 라이선스 유효성은 각 운영 실행에서 다시 판정해야 한다.
실거래 설정은 계속 비활성이다.
