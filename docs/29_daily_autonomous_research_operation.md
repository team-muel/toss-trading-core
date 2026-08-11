# 일일 자율 연구 운영 규칙

## 왜 같은 메일이 반복됐는가

2026-08-11 운영 점검에서 daily 타이머와 데이터 수집은 정상임을 확인했다. 그러나
기존 runner는 AI 가설 생성, 후보 평가, 고정 기준전략 backtest를 `weekly` 모드에서만
실행했다. daily 보고서의 `autonomous_research.state`는 실제로
`not_scheduled`였고 전략 산출물도 없었기 때문에, Vertex AI는 매일 거의 같은 사실을
다시 설명할 수밖에 없었다.

또한 daily Tiingo 스냅샷은 최근 45일 약 900개 정규화 행만 포함했다. 이 스냅샷은
신규 봉과 정정분 확인에는 충분하지만 504일 학습 구간과 walk-forward 검증에는
부족했다. 반면 2026-08-09 weekly 스냅샷에는 15개 ETF, 150,338개 정규화 행,
2020-05-28~2026-08-07의 공통 1,556거래일이 있었다. 데이터가 전혀 없는 것이
아니라, 장기 데이터와 일일 연구 실행이 연결되지 않은 것이 핵심 결함이었다.

## 수정된 일일 루프

daily 실행은 다음 순서를 따른다.

1. Toss 최근 구간, FRED revision 구간과 Tiingo 2004년 이후 장기 이력을 수집한다.
2. Tiingo의 `complete_through_date`를 직전 성공 daily와 비교한다.
3. 새 시장 거래일이 확인된 경우에만 AI가 최대 1개의 정책 제한 가설을 등록한다.
4. 새 가설과 prospective 단계 후보를 평가한다. 이미 과거검증에서 탈락한 후보는
   매일 다시 검정하지 않고 직전 결론을 이월한다.
5. 고정 기준전략을 다시 계산하여 prospective 관찰일을 갱신한다.
6. 검증된 데이터 범위, 총수익률 행 수, 신규 가설 수, 실제 평가 수를 보고서와
   Gmail 제목·본문에 기록한다.
7. 새 시장 데이터가 없고 강제 보고도 요청되지 않았다면 Gmail을 보내지 않는다.
   GCP 실행·GCS·BigQuery heartbeat는 계속 남는다.

weekly 실행은 2004년 이후 전체 데이터와 등록된 전체 후보를 재대사하는 감사
역할을 유지한다. weekly 실행은 한 번에 새 후보를 대량 생성하지 않는다.

전향 성과 계산에는 현재 실행에서 막 수집한 마지막 날짜를 즉시 사용하지 않는다.
직전 성공 실행이 완료 증거를 남긴 날짜까지만 사용한다. 따라서 데이터 수집 완료일과
prospective 평가 종료일은 정상적으로 한 성공 실행만큼 차이날 수 있다. 이는 현재
실행의 QA·백업·보고 성공을 전제로 현재 실행을 검증하는 순환 논리를 막기 위한
의도적인 지연이다.

## 가설 예산과 과적합 방지

- 새 가설: 시장 데이터가 실제로 전진한 daily 실행당 최대 1개
- 주간 상한: 최대 5개
- 전체 불변 원장 상한: 50개
- 기존 탈락 후보: daily 재검정 금지, weekly 감사에서만 재평가
- 후보 승격: 과거자료 평가만으로는 불가
- 실제 주문: 계속 비활성

이는 “매일 새 숫자를 만들기”가 아니라 “새 독립 관측치가 생길 때 연구 상태를 한
단계 전진시키기” 위한 규칙이다. 주말에 같은 데이터를 다시 돌려 다른 결론을 만드는
것은 연구 진전이 아니라 반복검정이므로 차단한다.

## 메일에서 확인할 항목

제목 예시:

```text
[Toss Research][DAILY][검증대기 6/126][AI해석포함][데이터 2026-08-10][신규 1·검증 1]
```

본문의 검증 사실에는 다음 값이 있어야 한다.

- 데이터 요청 시작일과 공급자 공통 완료일
- 이번 불변 실행에 포함된 총수익률 행 수와 종목 수
- 신규 등록, 실제 평가, 과거검증 통과, prospective 상태
- 고정 기준전략의 관찰일/필요일
- 이전 동일 모드 실행과 달라진 점

동일한 `complete_through_date`로 daily 메일이 반복되면 정상 동작이 아니다.
`RESEARCH_EMAIL_FORCE=1`을 명시한 수동 진단 실행만 예외다.

## 데이터 양 해석

총 행 수를 늘리는 것만으로 통계적 증거가 같은 비율로 늘지는 않는다. 동일 ETF의
raw와 total-return 행, 서로 높은 상관을 가진 S&P 500 ETF 복제본은 독립 표본이
아니다. 현재 병목은 저장 바이트가 아니라 다음 세 가지다.

1. 하루에 추가되는 독립 시장 관측치는 종목당 일봉 1개뿐이다.
2. 현 전략 DSL은 `dual_momentum` 한 계열로 제한되어 있다.
3. SGOV 포함 공통 구간 때문에 기준전략의 동일 조건 비교는 2020년 이후다.

따라서 다음 확장은 종목을 무작정 추가하는 대신 point-in-time 종목 이력, 사전 등록된
새 전략 계열, FRED의 as-of 거시 국면, 서로 다른 benchmark를 순서대로 추가해야 한다.

## 운영 확인 명령

```bash
systemctl list-timers --all | grep toss-research
jq '.data_progress, .autonomous_research, .strategy' \
  /home/seoje/toss-trading/research-runtime/latest-daily/reports/reporting-summary.json
jq . \
  /home/seoje/toss-trading/research-runtime/latest-daily/reports/tiingo-collection.json
tail -n 50 \
  /home/seoje/toss-trading/research-runtime/research_automation.jsonl
```
