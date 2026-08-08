# P1 연구 데이터·검증 완료 기록

기준일은 2026-08-08이다. 이 문서는 P1의 원래 8개 작업을 코드, 운영 증거,
안전 경계로 나누어 기록한다. P1은 연구 데이터와 검증 인프라 범위이며 실주문을
허용하지 않는다.

| 번호 | 작업 | 구현·검증 결과 |
| --- | --- | --- |
| 1 | Tiingo 약관·토큰 승인 | `config/data_sources.yaml`에 Internal Use Only 범위와 사용자 승인 소유자를 기록했다. 운영 weekly 수집도 라이선스 gate와 Secret Manager 토큰이 모두 있을 때만 실행한다. |
| 2 | ETF 15종 raw/total-return 수집 | 운영 weekly 실행에서 15종, 75,094행, 2026-07-31까지 수집한 증거를 확인했다. 공식 유니버스의 `SPYM`은 Tiingo의 공급자 별칭 `SPLG`로 요청하고 silver에는 `SPYM`으로 정규화한다. |
| 3 | Toss raw 교차검증 | Tiingo가 수집된 실행은 Toss/Tiingo raw의 종목·거래일·주기 겹침과 종가 오차를 정책상 최근 45 calendar day에서 강제 검사한다. 공급자별 과거 split 소급 방식이 달라 장기 raw는 비교를 위해 변형하지 않는다. read-only 재검증에서 15종목 495행이 겹쳤고 최대 종가 오차는 0.0 bps였다. 종가 허용 오차 초과는 실행 실패, 거래량 차이는 경고다. |
| 4 | `SPLG` 매핑 해소 | 공식 현재 티커를 `SPYM`으로 변경했다. 2025-10-31 이전 exchange 티커 `SPLG`, 현재 Toss `SPYM`, Tiingo 별칭 `SPLG`를 별도 유효기간으로 보존한다. Toss `SPYM` 일봉 read-only 호출은 HTTP 200으로 확인했다. |
| 5 | point-in-time instrument master | `listed_from`, `delisted_on`, identity 출처·검토일, 공급자별 alias 이력, corporate action registry를 추가했다. 공급자가 동일 ticker에 전신 상품 이력을 연결한 경우도 원본은 보존하되 상장 전·폐지 후 관측치는 백테스트와 가설평가 입력에서 제외하고 제외 건수를 기록한다. SMH의 2011-12-20 이전 HOLDRS 이력이 이 규칙의 운영 검증 사례다. |
| 6 | 첫 immutable gold experiment | 운영 gold artifact `6a517d92-5f41-5f7c-a483-9cc2303d7fcc.json`과 SHA-256 `491f21123f28d0a6fdd8ba391a874c6290b852c3f8b206e19cd6cf69357df15a`를 확인했다. 309개 manifest ID와 코드 revision이 고정돼 있고 prospective holdout 수집 중이라 headline metrics는 봉인돼 있다. |
| 7 | benchmark-relative walk-forward | 각 fold를 단순 양수 수익이 아니라 동일 날짜 SPY buy-and-hold 대비 초과수익으로 판정한다. SPY가 없을 때만 equal-weight candidates를 대체 benchmark로 사용한다. |
| 8 | 실제 계좌·거래규모 비용 모델 | Foundation DB의 현재 미국 수수료 schedule을 읽어 percent 단위를 bps로 정규화한다. 최근 체결 표본은 개인 식별자 없이 집계하고, 주문 notional 구간별 보수적 slippage와 매수·매도 각 leg 비용을 적용한다. 만료된 schedule은 사용하지 않는다. |

## 데이터 신원 규칙

- canonical instrument: `US:SPYM`
- 공식 현재 티커 및 Toss symbol: `SPYM`
- 공식 과거 exchange ticker: `SPLG` (2005-11-08~2025-10-30)
- Tiingo request alias: `SPLG`
- 정규화된 bronze/silver/gold symbol: `SPYM`

`python -m toss_trading.cli.research_validate_instruments`는 현재 15개 종목의
universe/master/history/action 1:1 정합성과 Toss/Tiingo 별칭의 시점별 유일성을
검사한다.

## 비용 모델 경계

Foundation 서비스만 계좌 ledger를 읽어 sanitized calibration JSON을 원자적으로
생성한다. 연구 서비스는 그 파일을 read-only로 받으며 계좌 번호, 주문 ID, 원본
snapshot을 받지 않는다. 실제 체결에서 직접 측정하지 못한 slippage는 측정치라고
표현하지 않고 보수적 정책값으로 명시한다.

## 운영 상태와 배포 경계

P1 이전 운영 증거는 immutable release `36a60066a485`에서 확인했다. P1 v3가
활성화됐다는 판단은 `readlink -f /home/seoje/toss-trading/current`의 새 commit과
그 revision이 기록된 weekly gold가 함께 있을 때만 내린다. 미커밋 코드를 기존
revision 이름으로 배포하지 않는다.

새 구현은 strategy implementation v3으로 올렸다. holdout 성과가 아직 봉인된 상태에서
시작일을 바꾸지 않고 point-in-time identity, benchmark-relative fold, sanitized 실제
수수료와 notional-tier slippage를 적용한다는 amendment를 protocol에 명시했다. 기존
v2 gold artifact는 수정하지 않는다.

실주문, 주문 취소, 조건 주문은 계속 비활성이다. SEC 연락처 승인과 SEC 자동 수집은
원래 P1 8개 항목 밖이며 기존 승인 gate를 유지한다.
