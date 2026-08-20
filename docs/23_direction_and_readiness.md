# 방향성 및 실행 준비 기준

## 결론

전체 방향은 맞습니다. Toss를 계좌·주문·체결의 유일한 실행 진실로 두고,
외부 데이터와 전략을 그 위의 의사결정 계층으로 분리한 구조는 유지합니다.
다만 연구 기능의 폭보다 주문 안전성과 회계 완결성을 먼저 완성해야 합니다.

현재 저장소는 **read-only Foundation 및 research/paper 단계**입니다.
`live_trading_enabled`와 모든 전략별 `live_orders_enabled`는 계속 `false`이며,
아래 증거가 모두 쌓여도 운영자의 별도 검토와 명시적 승인 없이 live로
전환하지 않습니다.

## 고정 범위

- 전용 계좌
- 미국 상장, USD 표시, long-only ETF
- 수량 지정 주문 우선
- 현금 또는 현금성 ETF와의 월간 리밸런싱
- 옵션, 공매도, 마진, 조건부 주문, 국내 종목, FX 다중통화 전략은 제외

Toss API가 더 넓은 상품을 기술적으로 지원하는 것과 이 시스템이 안전하게
운영할 범위는 구분합니다.

## 권장 실행 순서

| 순서 | 게이트 | 완료 증거 | 현재 상태 |
| --- | --- | --- | --- |
| 1 | 공식 계약 고정 | OpenAPI 1.2.14 version/hash/operation 검사, CI 일일 drift 검사 | 구현 |
| 2 | 주문 연속성 | OPEN + 최근 7일 CLOSED 중첩 수집, 정확한 주문 상세 | 구현 |
| 3 | 승인-주문 결속 | 계좌·스냅샷·정책·수량·가격의 동일 intent hash, 30초 만료 | 구현 |
| 4 | 현금·대사 완결성 | 통화별 cash genesis, execution delta, 미해결 BLOCK 0 | `v2-live-readiness` 증거 필요 |
| 5 | 연구 시점 정확성 | event/retrieval/availability 분리, total-return lineage, prospective collection ledger | 구현, 126거래일 수집 중 |
| 6 | 비용후 검증 | 양쪽 체결비용, 실제 벤치마크, OOS/walk-forward | 다중 fold 구현, 승인된 실데이터 검증 대기 |
| 7 | 지속 paper | 60일, 3회 리밸런싱, 30개 주문 lifecycle 시나리오 | 미실행 |
| 8 | shadow | 주문 제출 없이 실시간 의사결정·대사 비교 | 미실행 |
| 9 | 최소 live | 별도 운영 승인 후 초소형 1주 주문 | 차단 |

## `v2-live-readiness`의 의미

이 감사 프로필은 live 허가가 아닙니다. 다음 사항을 기계적으로 확인하는
증거 묶음입니다.

- Foundation v1의 실제 체결·수수료·결제·매도가능수량 증거
- 최근 complete run의 CLOSED 연속성
- 모든 buying-power 통화에 대해 운영자가 승인한 cash-ledger genesis
- 정책 hash와 불변 code revision
- cash event gap, 예약금 blocker, reconciliation BLOCK, 미지 enum이 없음

## 연구 결과 해석

- 과거 데이터의 원본 manifest는 실제 `retrieved_at`을 보존합니다.
- 정규화된 일봉의 `available_at`은 거래소 현지 20:00의 보수적 추정치이며,
  `source_revision`에 retrieval snapshot을 남깁니다.
- baseline은 다음 거래일 종가 체결 가정입니다. 신호일 종가부터 다음 종가까지의
  수익을 새 포지션에 귀속하지 않습니다.
- Foundation이 생성한 현재 Toss 미국 수수료와 protocol의 주문금액별 slippage
  tier를 매도·매수 양쪽에 적용합니다. calibration이 없거나 만료되면 새
  experiment를 만들지 않습니다.
- SPY buy-and-hold, 후보 동일가중, 60/40, 현금 benchmark를 실제로 계산합니다.
- 단일 IS/OS tail split과 별도로, 사전 등록된 전략을 504 거래일 train-context와
  126 거래일 비중첩 OOS 창으로 전진 평가합니다. 승인된 실데이터에서 여러 fold가
  생성되고 통과하기 전에는 전략 준비 완료로 표시하지 않습니다.

## 데이터·권한 원칙

- FRED/ALFRED는 series별 재배포·보관·표시 의무를 검토하기 전까지 비활성화합니다.
- Tiingo는 내부 사용 약관 승인 후 활성화됐으며 원본·파생 데이터를 외부에
  재배포하지 않습니다.
- 현재 공유 VM 연구 런타임은 Toss 교차검증 때문에 Foundation client secret을
  사용합니다. 목표 구조에서는 별도 research client secret만 허용합니다.
- GCS 런타임은 `objectCreator`만 가지며 run-id 경로에 새 객체만 씁니다.
  최신 상태는 덮어쓰는 GCS 객체가 아니라 BigQuery view와 로컬 symlink로 봅니다.
- 현재 Foundation과 research가 같은 VM service account를 공유하므로 완전한
  secret 격리는 아닙니다. `docs/27_p0_identity_and_holdout_remediation.md`의
  별도 VM/service account 전환과 7 daily + 1 weekly 관찰을 완료하기 전에는
  이 게이트가 통과되지 않습니다.
- GCS retention lock은 되돌릴 수 있으므로 자동 적용하지 않습니다. 보존기간과
  규제 요구를 운영자가 확정한 뒤 별도 change로 적용합니다.

## 즉시 No-Go

다음 중 하나라도 있으면 신규 주문은 만들지 않습니다.

- OpenAPI hash drift
- CLOSED 연속성 실패
- risk intent hash 또는 만료 검증 실패
- 통화별 cash genesis 누락
- 미해결 reconciliation BLOCK, cash event gap, 알 수 없는 주문 상태
- total-return 데이터 또는 point-in-time lineage 부재
- 비용후 OOS/walk-forward 및 지속 paper 증거 부재
- 전용 계좌 밖의 수동 보유·주문이 섞임
