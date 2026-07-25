# 402 Guard 데이터 품질 리포트
> 생성: 2026-07-25 10:27 (KST)
> 근거 스냅샷: docs/reports/_dataquality_20260725_101908.json
> 점검 범위: 시세 입력 CSV(data/market/*.csv) · 품질 7차원(소비호환·스키마·OHLC정합·연속성·이상치·충분성·신선도)
> 원칙: 읽기 전용 — CSV·코드 수정 없음, 자동 수집 없음. 마커는 점검 좌표이지 결함 확정이 아니다.

## 종합 요약
- KPI: 점검 심볼 3 · 총 300봉 · 실제 이슈 0건 · 관찰 20건 · 반려 0건 · [심각 0 · 높음 0 · 중간 0 · 낮음 2 · 정보 18]
- 소비경로 로드: 전 심볼 load_bars 성공 · 합계 {"parse_error_total":0,"ohlc_violation_total":0,"gap_total":0,"weekend_bar_total":0,"anomaly_total":0,"duplicate_total":0,"zero_volume_total":0,"schema_issues":[],"consumer_load_failures":[],"not_enough_for_ma20":[]}
- 한 줄 결론: **데이터 결함 0건 — 3종 CSV 전부 소비경로(load_bars) 정상 로드, OHLC 정합·스키마·연속성·중복 위반 없음.** 관찰 2건(MA200 여력·신선도)만 낮음 등급이고 나머지는 전부 정보성 건전성 확인.

| 심볼 | 봉수 | 로드 | OHLC위반 | 갭 | 이상치 | MA20/200 | 이슈 |
|---|---|---|---|---|---|---|---|
| AAPL | 100 | 성공 | 0 | 0 | 0 | 충족 / 미달 | 0 |
| NVDA | 100 | 성공 | 0 | 0 | 0 | 충족 / 미달 | 0 |
| TSLA | 100 | 성공 | 0 | 0 | 0 | 충족 / 미달 | 0 |

(전 심볼 공통: 헤더 `date,open,high,low,close,volume` 정확·오름차순·중복 0·거래량 0봉 0 · 기간 2026-02-27~2026-07-22 · 스냅샷 기준일 2026-07-25)

## 실제 이슈
**실제 이슈 0건 — 데이터 건전.** 소비경로 로드 실패, OHLC 정합 위반(high<low 등), 스키마/날짜형식 오류, 중복 날짜, 공휴일로 설명 안 되는 결측, 음수/0 가격 — 6개 결함 유형이 3종 300봉 전체에서 하나도 발견되지 않았다(근거: `docs/reports/_dataquality_20260725_101908.json → summary.*_total = 0`, `any_load_failure=false`, 세 CSV 인프로세스 `load_bars` 재현 성공). 억지 이슈를 만들지 않고 이슈 0으로 확정한다.

## 관찰·건전성 (이슈 아님)
- **충분성(낮음)**: 전 심볼 100봉 → MA20 충족(워밍업 20 제외 후 80봉 유효), MA200 미달 — `_dataquality_...json → items[*].adequacy(enough_for_ma20=true, enough_for_ma200=false, bars_after_warmup=80)`. Alpha Vantage 무료 티어 compact(100봉)라는 **이미 알려진 설계 선택**이며 새 결함 아님. 기본 전략(MA5/MA20)·백테스트·워밍업엔 무영향. indicators.py의 MA200 등 200봉 지표를 세션에서 켤 때만 산출 불가.
- **신선도(낮음)**: 마지막 봉 2026-07-22, 스냅샷 기준일 2026-07-25 — 거래일 2일(07-23 목·07-24 금) 뒤처짐. `items[*].profile.date_last`, `sections.freshness.today`. ReplayPriceFeed가 과거 일봉을 순차 재생하므로 로드·백테스트는 무영향, 라이브 데모 인상에서만 '2일 오래됨'으로 비침.
- **소비호환(정보)**: 3종 전부 `consumer_load.loaded_ok=true·bar_count=100·matches_parsed=true`, 인프로세스 `load_bars` 재현 성공(예: TSLA first 2026-02-27 open 402.94 / last 2026-07-22 close 374.01) — `items[*].consumer_load`.
- **스키마(정보)**: 헤더 6열 정확·`bad_date_count=0`·`parse_error_count=0`·`ascending_in_file=true`·`duplicates=[]` — `items[*].schema/ordering/duplicates`, `data/market/*_daily.csv:1`(헤더).
- **OHLC정합(정보)**: `ohlc_violation_count=0`(high<low, high<open/close, low>open/close, 가격≤0, volume<0 전무), `zero_volume_count=0` — `items[*].ohlc_violations(=[])` · 각 CSV `:2~101` 전수 재검증.
- **연속성(정보/무해)**: `gap_count=0`·`weekend_bar_count=0`. NVDA·TSLA의 4일 캘린더 갭 4건은 전부 미국 증시 공휴일(04-03 성금요일·05-25 메모리얼데이·06-19 준틴스·07-03 독립기념일 관측)로 설명 — `NVDA/TSLA_daily.csv:26-27,61-62,79-80,88-89`. 임계(>4일) 미달이라 진짜 결측 아님.
- **이상치(정보/무해)**: `anomaly_count=0`(20% 임계 초과 없음). 최대 일간 변동은 AAPL -6.12%(06-24→06-25, `AAPL_daily.csv:82~83`) · NVDA +6.26%(06-01, `:66`) · TSLA +8.46%(06-26→06-29, `:84~85`) — 전부 실적/수급성 정상 변동, 분할·데이터 오류(수배 급변) 특징 없음.

## 반려된 후보 (오탐 방지 기록)
반려 0건. 20개 후보 전부 검증관 `factual=true`(evidence_source 좌표를 실파일·증거 JSON에서 재확인, 창작 없음). 다만 20건 모두 `is_issue=false` — 결함이 아니라 '위반 없음/건전성'을 확인한 관찰이라 위 관찰·건전성으로 분류했다. (참고: 후보 evidence_source가 `items[SYM]`로 축약 표기된 곳은 실제로는 `sections.csvs.items[symbol=SYM]`이며 인용 수치는 원본과 일치함을 검증관이 확인.)

## 점검 한계·미검증
- 정적 스냅샷 기준: 이 리포트는 `_dataquality_20260725_101908.json`(2026-07-25 10:19:08 수집)과 그 시점 CSV에 근거한다. 이후 재수집(fetch_market_data.py) 시 신선도·봉 구성이 달라질 수 있음.
- 외부 대조 없음: 이상치(최대 일변동 6~8%대)는 임계(20%) 미만이라 무해로 분류했으나, 개별 종가를 외부 시세 소스와 대조 검증하지는 않았다(네트워크 미사용 부서). 실적일·분할 이벤트와의 1:1 매칭은 요일·거래캘린더 추론에 의존.
- 범위 한정: 점검 대상은 `data/market/AAPL/NVDA/TSLA_daily.csv` 3종뿐. 다른 심볼·인트라데이·실시간 피드는 이번 스냅샷 범위 밖.
- 작업트리 상태(읽기 전용 확인): `git status --short` 결과 **CSV(`data/market/*.csv`)와 앱 코드(payments/·agents/·engine.py·web/ 등)는 무변경** — 이 점검은 데이터·앱을 건드리지 않았다. 작업트리에 존재하는 변경은 데이터 부서 구축 산출물뿐이다(신규 `scripts/collect_dataquality.py`·`scripts/test_dataquality.py`·`.claude/workflows/data-dept.js`, 수정 `docs/reports/README.md`·`.gitignore`). `_dataquality_*.json`은 `.gitignore`로 추적 제외되며, 본 점검이 새로 만드는 파일은 이 리포트 md 2개뿐이다.
- 권고는 관찰일 뿐 자동 적용 안 함: MA200 실증 필요 시 유료 티어 full 수집, 데모 직전 신선도 개선은 사용자/수집 부서 몫이며 이 부서(읽기 전용)는 수집·수정을 수행하지 않는다.

## 참고 통계(파이프라인 산출)
{
  "inspected": 20,
  "issues": 0,
  "observations": 20,
  "dropped": 0,
  "by_severity": {}
}
(주: `by_severity`는 결정론적 수집 파이프라인이 산출하지 않는 필드라 빈 객체다. 위 KPI의 `낮음 2·정보 18`은 자기검토 단계에서 20개 관찰을 사람이 등급화한 값이며, 이슈가 0건이므로 심각·높음·중간 등급 이슈는 존재하지 않는다.)

## 증거 스냅샷
docs/reports/_dataquality_20260725_101908.json — Bash 로 열어 대조: cat "docs/reports/_dataquality_20260725_101908.json"

## 자기평가
- 정확성 — 5: 표본 좌표를 실파일에서 전수 재확인해 전부 일치. TSLA first `2026-02-27 open 402.94`(:2)·last `2026-07-22 close 374.01`(:101), AAPL 최대 하락 `-6.12%`(:82 close 293.08 → :83 close 275.15), NVDA `+6.26%`(:65 211.14 → :66 224.36), TSLA `+8.46%`(:84 379.71 → :85 411.84), 공휴일 갭 라인 26-27·61-62·79-80·88-89(NVDA·TSLA 공통), 헤더 6열 — 모두 증거 JSON·CSV와 오차 없음. 요약표 300봉·위반0·갭0·이상치0이 `summary.*_total`과 정합. 환각·오인용 없음.
- 완전성 — 4: 7차원 × 3심볼을 빠짐없이 다뤘고 읽기 전용·작업트리 상태까지 명시했다. 다만 이상치 6~8%대를 **외부 시세 소스와 1:1 대조하지 않아** '데이터 오류가 아님'을 100% 단정하지 못하고 임계 미만·분할특징 부재로 무해 '추정'에 머문다(네트워크 미사용 부서의 구조적 한계). 이 한 축이 미검증으로 남아 5가 아니다.
- 명료성 — 4: 요약표+KPI+차원별 섹션 구조가 심사·데모용으로 명확. 빈틈: KPI의 심각도 분포(`낮음 2·정보 18`)와 파이프라인 `by_severity {}`가 표기상 달라 순간 혼동 소지 → 참고통계에 주석 한 줄로 출처(사람 등급화 vs 파이프라인 미산출)를 명시해 완화했다.
- 실행가능성 — 4: 권고가 구체적(MA200=유료 full 수집, 신선도=fetch_market_data.py 재수집, 담당=수집 부서/사용자). 빈틈: 신선도 개선 권고가 스크립트 지목까지이고 **정확한 실행 인자·절차(어느 심볼을 어느 구간으로 재수집)까지는 제시하지 않아** 곧바로 복붙 실행되진 않는다.
- 간결성 — 4: 대체로 조밀하나 '이슈 0건'이 종합요약·한 줄 결론·실제 이슈 3곳에서 반복된다. 검증 강조 목적의 의도적 반복이지만 한 곳으로 줄일 여지가 있다.

전체 평균: **4.2**

개선 1~3 (영향순):
1. (실행가능성) 신선도 개선 권고에 재수집 명령·인자 예시를 붙여 '지목'을 '실행 절차'로 승격.
2. (명료성) KPI 심각도와 파이프라인 `by_severity` 불일치를 리포트 상단에서 한 번 더 정렬하거나, 파이프라인이 등급 필드를 산출하도록 수집기 확장.
3. (간결성) '이슈 0건' 반복을 종합요약 1곳으로 통합.

사용자가 이 평가에 동의할까? — 데이터가 실제로 건전하고 인용 좌표가 전부 재현되므로 4.2는 방어 가능하며, 4점 세 축은 '외부 대조 미실시·재수집 절차 미제시·경미한 반복'이라는 구체 근거가 있어 과대·과소 평가가 아니라고 본다.
