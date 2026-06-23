# 구현 로드맵

## Phase 0 - Broker and Data Discovery

산출물:

- Toss 공식 API 확인 체크리스트 작성
- `client_id/client_secret`, 토큰 발급, accountSeq 검증
- 데이터 공급자 후보 목록
- symbol master 초안
- 데이터 결측률 리포트
- 수수료, 세금, borrow, margin 가정표

완료 기준:

- accounts, holdings, orders, buying power 호출 확인
- holdings와 주문 스냅샷을 내부 장부로 반복 재현
- ETF NAV/분배 데이터는 외부 소스로 point-in-time 저장 가능성 확인

## Phase 1 - Research Backtest

산출물:

- Toss live 후보 엔진별 백테스트
- 비용/세금/슬리피지 민감도
- 2008/2020/2022/2024 스트레스 블록
- look-ahead, survivorship 방지 검증

완료 기준:

- modeled cost 반영 후 현금 대기 대비 초과수익 가능성
- drawdown과 엔진별 손실 기여가 starter guardrail을 넘지 않을 것
- 모든 수치형 합격선은 관측 데이터로 calibration 전까지 provisional로 표시

## Phase 2 - Simulation

산출물:

- 주문큐
- 부분체결 모의
- 계좌 상태 replay
- cash ledger replay
- broker reconciliation simulator
- 스프레드 확대 모의
- broker reject 모의
- rate limit과 주문거부 모의

완료 기준:

- 주문 거부, 체결 지연, 슬리피지 확대 상황에서 kill switch 작동
- Toss holdings/orders 대사 실패 시 신규 주문 중단
- 동일 입력 이벤트 재처리 시 동일한 계좌 상태 재현

## Phase 3 - Paper Trading

산출물:

- 라이브 신호 로그
- 페이퍼 주문/체결 로그
- 리스크 스냅샷
- 세무 보조장부
- 주간 운영 리포트

완료 기준:

- 신호-실행 괴리 측정 가능
- 데이터 결측과 stale 원인 파악
- 실거래 전 운영 절차가 반복 가능

## Phase 4 - Micro Live

전제 조건:

- Toss Open API 실계좌 호출 검증 완료
- 실주문 어댑터 별도 코드리뷰 완료
- 포지션 한도 초소형으로 제한

목표:

- 수익이 아니라 운영 정상성 검증
- 주문 거부, 부분체결, 수수료/세금, 결제예정일, 장부 대사 확인

## Phase 5 - Scale Decision

결정 기준:

- 성과보다 재현성과 안정성
- 세후/비용후 수익성
- 엔진 간 공통 꼬리위험 관리 가능성
- 수동 개입 없이도 감사 가능한 로그 체계
