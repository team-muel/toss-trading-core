# Calibration Policy

## Principle

보고서에 적힌 숫자는 시장 법칙이 아니라 초기 운용 가드레일입니다. 자동매매 코드에 그대로 박아 넣지 않습니다.

숫자는 세 단계로 관리합니다.

| Stage | Meaning | Use In Live Trading |
| --- | --- | --- |
| Report Example | 설계 보고서의 설명용 수치 | No |
| Starter Guardrail | 보고서보다 더 보수적인 paper/micro-live 안전장치 | Limited |
| Calibrated Policy | 실제 체결, 슬리피지, 손실분포, 주문거부, 세금/수수료 자료로 승인된 값 | Yes |

## Default Rule

- `config/default_policy.yaml`의 `portfolio_limits`와 전략 임계값은 기본적으로 `null`입니다.
- `null` 값은 “아직 자동 운용에 쓰지 말라”는 뜻입니다.
- live 주문은 `starter_guardrails` 또는 별도 승인된 calibrated policy를 통과해야 합니다.
- 보고서 숫자를 그대로 복사한 PR이나 설정 변경은 reject합니다.

## What Must Be Observed First

실제 값을 완화하거나 확정하기 전에 최소한 다음 분포를 수집합니다.

- 주문 제출 후 상태 확정까지 latency
- OPEN/CLOSED 주문 조회 누락률
- 주문 거부율과 거부 코드
- 부분체결 빈도
- 모델 대비 실체결 슬리피지
- 종목별 호가 스프레드와 주문 크기별 체결 품질
- `cashBuyingPower` broker constraint와 내부 available cash 차이
- 수수료/세금/결제예정일 반영 오차
- 일별 손익과 drawdown 분포

## Adjustment Process

1. Paper mode에서 관측치를 수집합니다.
2. Micro-live에서는 주문 크기와 open order 수를 강하게 제한합니다.
3. 주간 리포트에서 가드레일 변경 근거를 기록합니다.
4. 변경 전후 정책 파일을 보존합니다.
5. 손실분포가 나빠지거나 reconciliation 오류가 생기면 즉시 이전 보수 정책으로 되돌립니다.

## Hard Blocks

다음 상황에서는 수치 보정과 무관하게 신규 주문을 막습니다.

- reconciliation 차이 발생
- 주문 상태 불명
- `clientOrderId` 없는 live 주문 생성 시도
- `cashBuyingPower` broker constraint 조회 실패
- CLOSED 주문 페이징 누락
- rate limit 대응 실패
- unknown error code 반복 발생
