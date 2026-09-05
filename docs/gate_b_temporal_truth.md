# AMA-22 — Gate B Temporal Truth Acceptance

Gate B는 시간 및 기준정보 계층의 여덟 필수 공격·복원 검사를 증거 artifact/run
ID와 함께 판정한다. future sentinel, cutoff 이후 availability, revision replay,
당일 종가, 공식 발표·수신 전 접근, DST/휴장/조기폐장, 역사적 Universe,
기업행동·가격 의미 중 하나라도 실패하면 `FAIL`이다. 누락된 check, `None` 상태,
비어 있거나 중복된 evidence ID는 판정 입력으로 인정하지 않는다.

결과는 PASS/FAIL, 실패 reason code, 정렬된 evidence IDs, UTC 평가시각,
code revision과 canonical SHA-256 hash를 보존한다. FAIL 결과는 다음 데이터 계층
승격의 근거로 사용할 수 없다. Gate는 데이터를 추정하거나 시간을 보정하지 않는다.

2026-09-06 공식 구현 판정은 `docs/evidence/gate_b_temporal_truth_2026-09-06.json`에
기록한다. 이 판정은 테스트 fixture와 코드 계약에 대한 PASS다. fixture calendar는
운영 calendar 승격이 아니며, 실제 공급자의 최신성·완전성은 이후 데이터 품질과
source health gate에서 별도로 판정한다. 실거래 설정은 계속 비활성이다.
