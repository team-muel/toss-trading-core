# 자율 전략 연구의 검증·승격 운영 규약

## 목표와 현재 경계

AI가 가설을 제안하고 시스템이 같은 기준으로 평가하는 연구 루프를 운영한다.
그러나 AI는 코드 실행, 임의 전략 생성, 주문 제출, 승격 승인 권한을 갖지 않는다.
현재 자동화의 최종 권한은 **전향 관찰 후보 등록**까지다. 실제 paper 체결
인프라와 shadow 체결 증거가 없으므로 live 승격은 구조적으로 차단된다.

## 권장 실행 순서

1. Vertex AI는 가격이나 백테스트 성과를 보지 않고 정책·종목 목록·이미 등록된
   설정만 받아 최대 3개 가설을 제안한다.
   같은 ISO 주에 수동으로 다시 실행해도 원장의 `registered_at`을 기준으로
   주당 3개 한도를 다시 사용할 수 없다.
2. 로컬 검증기가 `config/autonomous_research_policy.json`에 열거된 dual-momentum
   DSL과 정확히 일치하는지 검사한다. 임의 코드, 새 필드, 허용 밖 종목은 즉시
   거부한다. 비용은 후보가 제안하지 못하며 별도 signed-off calibration만 사용한다.
3. 설정 내용으로 UUID를 계산하고 후보를 append-only 가설 원장에 사전등록한다.
   설명이 달라도 설정이 같으면 같은 후보다.
4. 모든 등록 후보를 total-return point-in-time 자료로 실행한다. SPY 대비 paired
   excess return에 21거래일 block bootstrap을 적용하고, 누적 등록 후보 수를
   Bonferroni 보정의 family size로 사용한다.
5. 최소 4개 순차 walk-forward 창, SPY 초과수익 창 비율 50% 이상, family-wise 5%
   통계 관문, 수수료·slippage 2배 스트레스를 모두 통과해야 한다.
6. 통과 시 마지막 역사 데이터 날짜를 후보별 prospective protocol에 영구
   기록한다. 이후 들어온 데이터만 전향 평가에 사용한다.
7. 252거래일과 12회 리밸런싱을 모두 채우기 전에는 전향 성과를 공개하지 않는다.
8. 전향 관문을 통과해도 상태는 `prospective_complete_awaiting_paper_infrastructure`다.
   paper 주문·체결·수수료·부분체결·대사 증거, 그 다음 shadow 증거가 별도로
   확보되기 전에는 promotion과 execution 권한이 항상 `false`다.

## 불변 증거

영구 런타임 원장은 다음 파일을 수정하지 않고 추가만 한다.

```text
hypothesis-ledger/
  hypotheses/<hypothesis-id>.json
  prospective_protocols/<hypothesis-id>.json
  evaluations/<hypothesis-id>/<run-id>.json
```

각 평가는 code revision과 실제 사용한 silver manifest ID를 포함한다. 같은
`hypothesis-id/run-id`에 다른 내용이 들어오면 실행이 실패한다. 실행 산출물에도
가설·protocol·평가 사본을 넣어 GCS의 고유 run과 함께 보존한다.

## 상태 해석

| 상태 | 의미 | 성과 공개 | 승격 |
|---|---|---:|---:|
| `historical_not_qualified` | 과거자료 관문 미통과 | 진단값만 | 금지 |
| `awaiting_prospective_observation` | 과거자료 관문 통과, 미래 관찰 등록 | 과거 진단값만 | 금지 |
| `prospective_collecting` | 252일/12회 중 하나 이상 부족 | 숨김 | 금지 |
| `prospective_not_qualified` | 고정 전향 표본 관문 미통과 | 공개 가능 | 금지 |
| `prospective_complete_awaiting_paper_infrastructure` | 전향 관문 통과, 실제 paper 증거 없음 | 공개 가능 | 금지 |

`historically_qualified`는 “검증된 수익 전략”이 아니라 “새 데이터로 관찰할
자격을 얻은 후보”라는 뜻이다.

## 현재 남은 필수 작업

- Toss 또는 별도 브로커의 안전한 paper 주문·체결 환경 결정
- 주문 의도와 실제 fill, 수수료, 부분체결, 취소, 결제 상태의 대사 증거 스키마
- paper 최소 기간·거래 횟수와 shadow 최소 기간의 별도 사전등록
- corporate action·ticker 변경·상장폐지 시점 데이터의 독립 검증
- 데이터 공급자 장애와 수정 vintage가 전향 표본에 미치는 영향 기록

이 항목이 완료되기 전에는 AI가 좋은 결과를 설명하더라도 live 연결을 만들지
않는다.

## 구현 위치

- 정책: `config/autonomous_research_policy.json`
- AI 제안·불변 원장: `src/toss_trading/research/hypotheses.py`
- 역사·전향 평가: `src/toss_trading/research/candidate_evaluation.py`
- CLI: `src/toss_trading/cli/research_plan_hypotheses.py`,
  `src/toss_trading/cli/research_evaluate_hypotheses.py`
- GCP 실행: `scripts/run_research_automation_gcp.sh`

## 2026-08-11 다계열 확장

이 문서의 초기 `dual_momentum` 단일 DSL 설명은 기존 원장 후보와의 호환성을 위한
기록이다. 신규 후보는 `docs/30_multi_direction_quant_research.md`의 6개 계열, 계열 순환,
구조적 참신성 검사를 적용한다. 승격 및 실행 차단 규칙은 그대로 유지된다.
