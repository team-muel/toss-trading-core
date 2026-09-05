# 구현 단계 17 — Risk Governor

Risk Governor는 target portfolio 뒤, order intent 앞에 있는 최종 위험 승인 계층이다. 입력은 이미 계산된 target과 계좌·시간·데이터·위험모형의 증거뿐이며 기대수익이나 Alpha를 새로 만들지 않는다. 결정은 `runtime_run_id`, target ID와 hash, 정책 version, `as_of_utc`, 입력 증거 ID에 묶인다.

## 결정 상태

| 상태 | 의미 | 주문 승인 |
|---|---|---|
| `ALLOW` | 모든 필수 조건 충족 | 가능 |
| `REDUCE` | 정책 multiplier까지 노출 축소 | 가능 |
| `BLOCK` | hard block 발생 | 불가 |
| `ABSTAIN` | 증거·데이터·현금·경제성 근거 부족 | 불가 |
| `DEFER` | 판단 근거는 있으나 현재 실행 보류 | 불가 |

`ALLOW`와 `REDUCE`만 불변 `ApprovedRiskDecision`을 만든다. 주문 의도는 runtime, policy version, target ID, target hash가 승인과 모두 일치해야 생성된다. 기존 DB pipeline도 같은 runtime의 `ALLOW/REDUCE` 결정과 risk-control evidence가 없으면 order intent 삽입을 거부한다.

## Hard block

계좌 대사 실패, 주문상태 미확인, 초기현금 미확인, 같은 run 계좌 snapshot 누락, clock risk, stale execution price, data conflict, risk model 실패, optimizer infeasible, policy mismatch, 중복 order intent, kill switch, 미승인 runtime mode를 독립된 안정 reason code로 기록한다. 하나라도 참이면 다른 soft 조건보다 먼저 `BLOCK`, multiplier `0`을 반환한다.

## Soft reduction

변동성 상승, 낮은 confidence, event risk, sector·factor 집중, 높은 spread·turnover, regime·risk estimate 불확실성은 versioned policy의 multiplier를 적용한다. 여러 조건이 동시에 생기면 multiplier를 곱해 암묵적으로 과도 축소하지 않고 가장 보수적인 cap인 최솟값을 사용한다. 활성 조건은 모두 reason code로 남는다. 정책은 아홉 조건의 multiplier를 빠짐없이 명시해야 하며 각 값은 `0 < multiplier < 1`이어야 한다.

## 결정론과 보존

입력과 결과를 canonical JSON으로 직렬화하고 SHA-256으로 `content_hash`와 `risk_decision_id`를 만든다. 증거 ID 순서는 정렬하므로 동일한 의미의 입력은 동일 결정을 낸다. JSONL journal은 append-only로 추가하며 동일 ID의 정확한 replay만 멱등 처리한다. ID가 같고 내용이 다르면 overwrite로 거부한다.

## 완료조건 점검

- 모든 거부·감축에 stable reason code 존재: 충족
- hard block을 soft reduction으로 우회 불가: hard-first 평가로 충족
- Risk Governor가 신규 Alpha를 생성하지 않음: 입력·출력 contract에 Alpha/expected return 없음
- 승인된 RiskDecision 없이 주문 생성 불가: 승인 토큰과 exact target binding, DB pipeline gate로 충족
- 동일 입력은 동일 Decision: canonical payload와 SHA-256 결정 ID로 충족
- `ALLOW`, `REDUCE`, `BLOCK`, `ABSTAIN`, `DEFER` 의미 분리: 충족
- 정상·실패 경로: 13개 hard block, soft cap, 다섯 상태, 정책 mismatch, journal, 주문 gate 테스트로 검증

실거래 기본값은 계속 `false`이며 이 단계는 주문 제출이나 Alpha 생성 기능을 포함하지 않는다.
