# AMA-15 — Gate A Account Truth Acceptance

Gate A는 M0 Account Truth 구성요소가 다음 단계의 입력으로 사용 가능한지 공식
판정한다. 일곱 필수 check는 각각 비어 있지 않은 artifact/run ID를 가져야 한다.

- cash replay 결정론
- position/tax-lot replay 결정론
- order/execution delta 중복 안전성
- unknown order state의 신규 거래 차단
- opening balance와 settlement evidence 검증
- Portfolio Accounting 대사
- clean checkout CI/build/secret scan 통과

미해결 reconciliation blocker는 승인 목록에 명시된 항목만 허용한다. 승인된
blocker도 결과에 보존한다. 필수 check 실패, 미승인 blocker, 실거래 활성화는
각각 안정 reason code를 가진 `FAIL`이며 다음 단계 승격 근거로 사용할 수 없다.
`None` 같은 미확인 boolean, 누락된 check/evidence, 중복·빈 ID는 판정 자체를
거부한다. 동일 입력은 동일 SHA-256 `content_hash`를 만든다.

현재 알려진 `TOSS_CASH_SOURCE_UNVERIFIABLE`은 직접 대사 가능한 broker 현금
source가 없을 때 신규 거래를 차단하는 명시적 운영 blocker다. Gate에서 허용해도
거래 gate가 이를 정상 cash로 바꾸지는 않는다. 실계좌 blocker가 추가되면 별도
증거 없이 자동 허용되지 않는다. `live_trading_enabled`는 계속 `false`다.

2026-09-06 구현 기준의 공식 결과는
`docs/evidence/gate_a_account_truth_2026-09-06.json`에 기록했다. 결과는 `PASS`이며
PR #27의 Python 3.11 CI run과 개별 회계·대사·replay 테스트 node ID를 증거로
묶었다. 위 현금 source blocker는 명시적으로 보존되므로 실제 현금 진실이 없는
계좌의 신규 거래를 허용하는 근거로 사용할 수 없다.
