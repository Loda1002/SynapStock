// 402 Guard — 데이터 수집 부서 (트랙 B: 개발/평가 지원 워크플로우)
//
// 시세 입력 CSV(data/market/*.csv)가 ReplayPriceFeed·백테스트·데모의 뿌리이므로,
// 이 데이터가 실제 소비 코드 경로로 로드되고 OHLC 정합·연속성·이상치·워밍업 충분성
// 면에서 건전한지 점검해 docs/reports/data_<TS>.md 리포트를 만든다.
//
// 읽기 전용 — CSV·앱 코드를 한 줄도 고치지 않고, 자동 수집(네트워크)도 하지 않는다.
// 모든 근거는 결정론적 품질 스냅샷(scripts/collect_dataquality.py)과 저장소 file:line(CSV
// 의 날짜·행번호)에 붙는다. 마커(갭·이상치)는 '점검 좌표'이지 결함 확정이 아니다 —
// 인스펙터가 행을 열어 공휴일 갭·실적 실변동과 진짜 결함을 가르고, 적대 검증관이 재확인한다.
//
// 파이프라인: 품질표면 수집 → 심볼별 점검(팬아웃) → 건별 적대 검증 → 종합 → 5축 자기평가·기록.
//
// 온디맨드 재실행(서브에이전트처럼):
//   Workflow({ scriptPath: '.claude/workflows/data-dept.js' })   // 항상 보장되는 경로
// 미래 세션에서 "데이터 부서 돌려줘" = 이 워크플로우 실행.

export const meta = {
  name: 'data-dept',
  description: '시세 CSV(data/market/*.csv)를 실제 소비경로 로드·OHLC 정합·연속성·이상치·충분성으로 점검 → 리포트(docs/reports). 읽기 전용 — 수집·수정 없음.',
  phases: [
    { title: 'Scan', detail: 'collect_dataquality.py 실행 + JSON 스냅샷' },
    { title: 'Inspect', detail: '심볼별 병렬 점검(품질 7차원 전부)' },
    { title: 'Verify', detail: '발견 건별 적대 검증(행 재확인·무해/결함 판별)' },
    { title: 'Report', detail: '종합 + 5축 자기평가 후 리포트 기록' },
  ],
}

// ─────────────────────────────────────────── 공통 규범

const REPO_NOTE = `현재 작업 디렉터리가 저장소 루트다. Python 은 .venv/Scripts/python.exe (3.10 고정).
Windows + 한국어 경로라 Python 실행 시 반드시 앞에 PYTHONIOENCODING=utf-8 를 붙인다(안 붙이면 한국어 출력이 깨진다).
이 부서는 네트워크가 필요 없다(정적 CSV 분석 + 인프로세스 load_bars 로드).`

const DATA_PRINCIPLE = `## 데이터 부서 원칙 (반드시 지킬 것)
- 읽기 전용이다. CSV·앱 코드를 한 줄도 고치지 않고, 자동 수집(Alpha Vantage 호출 등)도 하지 않는다. 관찰·권고만 낸다.
- 모든 주장은 실물 좌표에 붙인다: data/market/<SYM>_daily.csv 의 날짜·행번호(파일 줄), 또는 증거 JSON 의 키/값. 좌표 없는 주장은 넣지 마라.
- 마커(갭·이상치·주말봉)는 '여기를 봐라'는 좌표일 뿐 결함 확정이 아니다. 공휴일로 인한 갭, 실적발표발 큰 변동은 정상이다 — '무해'로 분류하라.
- 무엇이 '데이터 결함'인가: 소비경로(load_bars) 로드 실패, OHLC 정합 위반(high<low 등), 스키마/날짜형식 오류, 중복 날짜, 진짜 결측(공휴일로 설명 안 되는 긴 갭), 음수/0 가격. 이건 확인되면 실제 이슈다.
- 무엇이 '관찰'인가: 봉 수가 MA200 에 못 미침, 데이터가 오래됨(신선도) — 이미 알려진 설계 선택(무료 티어 100봉)은 '새 결함'이 아니라 '관찰'로. 재보고 금지.
- 근거를 못 대면 등급을 낮추고 confidence=추정 으로. 저장소에 없는 것을 지어내지 마라(창작 금지).`

// 품질 7차원 — 모든 인스펙터가 담당 CSV 에 이 일곱을 전부 적용한다.
const RUBRIC = `## 품질 점검 7차원 (담당 CSV 에 전부 적용)
1. 소비 호환(최우선) — consumer_load 가 loaded_ok=true 인가, bar_count 가 파싱 행수와 일치(matches_parsed)하는가. 실패면 데모·백테스트가 이 CSV 로 깨진다 → 심각.
2. 스키마·파싱 — 헤더가 date,open,high,low,close,volume 인가, 날짜 YYYY-MM-DD 인가, 숫자 변환 실패(parse_errors) 좌표.
3. OHLC 정합 — ohlc_violations(high<low·high<open/close·low>open/close·가격<=0·volume<0). 객관적 결함.
4. 연속성 — gaps(공휴일 vs 진짜 결측 구분), weekend_bars(있으면 이상), 중복 날짜(duplicates), 파일 정렬(ordering.ascending_in_file).
5. 이상치 — anomalies(전일 대비 |변동|>20%: 분할/오류 vs 실적 실변동 구분), zero_volume(거래량0: 휴장/결측 의심).
6. 충분성 — adequacy(enough_for_ma20/ma200·bars_after_warmup). 전략이 쓰는 지표(MA5/MA20 기본, indicators.py 는 MA200 지원)에 봉이 충분한가.
7. 신선도 — profile.date_last 와 freshness.today 로 데이터가 얼마나 오래됐나(재생 데이터라 치명적이진 않으나 데모 인상에 영향).`

// ─────────────────────────────────────────── 스키마

const SCAN_EVIDENCE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    evidence_path: { type: 'string', description: '저장소 루트 기준 상대경로 docs/reports/_dataquality_<ts>.json' },
    symbols: { type: 'array', items: { type: 'string' }, description: 'summary.symbols(점검 대상 심볼)' },
    csv_count: { type: 'integer' },
    total_bars: { type: 'integer' },
    any_load_failure: { type: 'boolean', description: 'summary.any_load_failure — 하나라도 load_bars 실패면 true' },
    totals: { type: 'object', additionalProperties: true, description: 'summary 의 위반/갭/이상치 합계 등' },
    collector_error: { type: 'string', description: '수집기 실행 실패 시 그 사유(없으면 생략)' },
  },
  required: ['evidence_path', 'symbols'],
}

const CATEGORY_ENUM = ['소비호환', '스키마', 'OHLC정합', '연속성', '이상치', '충분성', '신선도']
const SEV_ENUM = ['심각', '높음', '중간', '낮음', '정보']
const CLASS_ENUM = ['데이터결함', '조치필요', '무해', '관찰']

const FINDING_PROPS = {
  symbol: { type: 'string' },
  category: { type: 'string', enum: CATEGORY_ENUM },
  severity: { type: 'string', enum: SEV_ENUM, description: '심각=소비경로 로드실패/정합붕괴, 높음=명백한 데이터결함, 중간=진짜 결측/의심 이상치, 낮음/정보=관찰·경미' },
  classification: { type: 'string', enum: CLASS_ENUM, description: '데이터결함=고쳐야 함, 조치필요=수집/갱신 권고, 무해=공휴일갭·실적변동 등 정상, 관찰=설계선택·정보' },
  title: { type: 'string', description: '한 줄 제목' },
  observation: { type: 'string', description: '무엇이 관찰됐고 왜 문제/정상인가' },
  evidence_source: { type: 'string', description: 'data/market/<SYM>_daily.csv:<행번호>(실제 연 것) 또는 증거 JSON 키' },
  consumer_impact: { type: 'string', description: 'load_bars·ReplayPriceFeed·MA 워밍업·백테스트에 실제로 미치는 영향(없으면 "없음")' },
  recommendation: { type: 'string', description: '권고(관찰·제안만 — 자동 적용 안 함)' },
  confidence: { type: 'string', enum: ['확실', '추정'] },
}

const INSPECT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    symbol: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: FINDING_PROPS,
        required: ['symbol', 'category', 'severity', 'classification', 'title', 'observation', 'evidence_source', 'consumer_impact', 'confidence'],
      },
    },
    inspected_note: { type: 'string', description: '무엇을 읽었고 무엇을 못 봤는지(한계) 한 줄' },
  },
  required: ['symbol', 'findings'],
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    factual: { type: 'boolean', description: '인용한 좌표·수치가 실제 파일/증거와 일치하는가(불일치면 false)' },
    is_issue: { type: 'boolean', description: '진짜 데이터 품질 문제인가. 공휴일갭·실적변동·알려진 설계선택이면 false' },
    corrected_severity: { type: 'string', enum: SEV_ENUM },
    corrected_classification: { type: 'string', enum: CLASS_ENUM },
    reason: { type: 'string', description: '판정 근거(실제로 연 행/JSON 확인 결과)' },
  },
  required: ['factual', 'is_issue', 'reason'],
}

const WRITE_RESULT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    report_path: { type: 'string' },
    latest_path: { type: 'string' },
    overall_summary: { type: 'string' },
    self_eval_overall: { type: 'number', description: '5축 자기평가 평균(1~5)' },
    counts: { type: 'object', additionalProperties: true, description: '{inspected, issues, observations, dropped, by_severity:{}}' },
    corrections_made: { type: 'array', items: { type: 'string' } },
  },
  required: ['report_path', 'overall_summary'],
}

// ─────────────────────────────────────────── 프롬프트

const SCAN_PROMPT = `${REPO_NOTE}

'데이터 부서'의 품질표면 수집 단계다. 결정론적 스냅샷을 만들고 위치를 보고한다.

1. Bash 실행: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m scripts.collect_dataquality
   (실패해도 중단하지 말 것 — stderr 를 collector_error 로 담아 가능한 최선을 반환)
2. docs/reports/_dataquality_*.json 중 파일명 타임스탬프가 가장 최근인 파일을 찾는다.
3. 그 파일을 읽어 summary 섹션 값을 확인한다.
4. 반환: evidence_path(상대경로), summary 의 symbols·csv_count·total_bars·any_load_failure, 그리고 위반/갭/이상치 합계를 totals 로.`

function inspectPrompt(sym, ev) {
  return `${REPO_NOTE}

너는 402 Guard '데이터 부서'의 인스펙터다. 아래 한 심볼의 시세 CSV 만 담당해 품질을 점검한다.

${DATA_PRINCIPLE}

${RUBRIC}

## 담당 심볼
${sym}  (파일: data/market/${sym}_daily.csv)

## 근거
결정론적 품질 스냅샷: ${ev.evidence_path}
먼저 Bash 로 열어 네 심볼 항목을 찾아라: cat "${ev.evidence_path}"
- sections.csvs.items 에서 symbol=="${sym}" 인 항목의 판정(ohlc_violations·gaps·anomalies·adequacy·consumer_load 등)을 본다.
- 마커에 좌표(날짜·행번호)가 있으면 실제 CSV 를 Read 로 열어 그 행을 확인한다(마커=좌표일 뿐, 열어서 정상이면 무해로).
- 갭/이상치는 공휴일·실적발표로 설명되는지 판단한다(설명되면 '무해'). 소비호환·OHLC정합·스키마는 객관적 결함이니 확인되면 그대로.

## 재현·확인(선택이지만 강력)
필요하면 python -c "..." 한 줄로 확인하라(예: 특정 구간 load_bars 로드, 갭 날짜의 요일). 저장소에 파일을 남기지 마라.
  예: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "from market.price_feed import load_bars; b=load_bars('data/market/${sym}_daily.csv'); print(len(b), b[0].date, b[-1].date)"

## 출력 규칙
- 각 finding 은 symbol·category·severity·classification·title·observation·evidence_source·consumer_impact·confidence 를 채운다.
- 데이터가 깨끗하면 억지 결함을 만들지 마라 — 대신 충분성/신선도 '관찰'(classification=관찰)과 확인된 건전성을 정보(severity=정보)로 남겨 심사·데모에 쓸 사실을 준다.
- evidence_source 는 반드시 실제 좌표: data/market/${sym}_daily.csv:<행> 또는 증거 JSON 키. 확인 못 한 것은 confidence=추정 으로.
- 이미 알려진 설계 한계(무료 티어 100봉 → MA200 불가 등)는 '관찰'로만, '결함'으로 올리지 마라.`
}

function verifyPrompt(f, ev) {
  return `${REPO_NOTE}

너는 적대적 검증관이다. 아래 데이터 품질 발견이 (1)사실인지 (2)진짜 이슈인지 회의적으로 재검증한다.

## 후보(JSON)
${JSON.stringify(f, null, 2)}

## 검증 절차
1. evidence_source 의 좌표(CSV 파일:행 또는 증거 JSON 키)를 실제로 열어 인용한 수치가 맞는지 확인한다. 다르면 factual=false.
   - CSV 행은 Bash 로: sed -n '<행>p' "data/market/${f && f.symbol ? f.symbol : 'SYM'}_daily.csv"  (또는 Read).
   - 증거 JSON 은: cat "${ev.evidence_path}" 에서 해당 키 확인.
2. 사실이면(factual=true), 이게 '진짜 데이터 품질 문제'인지 판단한다:
   - 객관적 결함(소비경로 로드실패·OHLC 위반·스키마오류·중복날짜·진짜 결측)이면 is_issue=true.
   - 공휴일로 설명되는 갭, 실적발표발 큰 변동, 무료 티어라 100봉(알려진 설계) 같은 건 is_issue=false + classification=무해/관찰.
3. 심각도·분류가 과장/과소면 corrected_severity·corrected_classification 로 조정한다(소비경로 로드실패/정합붕괴만 심각).

## 규칙
- 좌표·수치가 파일과 다르면 factual=false. 저장소에 없는 것을 지어낸 창작이면 factual=false + reason 에 '창작'.
- reason 에는 네가 연 좌표와 확인 결과를 구체적으로. 짐작 금지.`
}

function synthPrompt(data, ev, stats) {
  return `${REPO_NOTE}

너는 '데이터 부서'의 종합 작성관이다. 검증된 발견(사실 확인된 것)을 받아 데이터 품질 리포트(마크다운) 초안을 쓴다.

${DATA_PRINCIPLE}

## 입력 (심볼별 발견 + 각 건 검증 verdict)
${JSON.stringify(data, null, 2)}

## 통계(파이프라인 산출)
${JSON.stringify(stats, null, 2)}

증거 스냅샷 경로: ${ev.evidence_path}
현재 요약: 심볼 ${JSON.stringify(ev.symbols || [])} · 총 ${ev.total_bars}봉 · 로드실패 ${ev.any_load_failure ? '있음' : '없음'} · 합계 ${JSON.stringify(ev.totals || {})}.

## 작성 규칙
- factual=false 로 반려된 건은 확정 목록에서 제외하고 '반려된 후보'에 사유와 함께.
- is_issue=true 는 '실제 이슈', is_issue=false(무해·관찰)는 '관찰·건전성'으로 분리. 섞지 마라.
- 심각도는 verdict.corrected_severity 가 있으면 그걸 우선, 심각도순 정렬.
- 데이터가 깨끗하면 그걸 정직하게 '이슈 0건 — 데이터 건전'이라 쓰고, 관찰·권고(신선도·MA200 여력 등)를 준다. 억지 이슈 금지.
- 아래 정확한 구조의 마크다운만 출력(코드펜스로 전체를 감싸지 말 것):

# 402 Guard 데이터 품질 리포트
> 생성: <PLACEHOLDER — 다음 단계에서 타임스탬프 기입>
> 근거 스냅샷: ${ev.evidence_path}
> 점검 범위: 시세 입력 CSV(data/market/*.csv) · 품질 7차원(소비호환·스키마·OHLC정합·연속성·이상치·충분성·신선도)
> 원칙: 읽기 전용 — CSV·코드 수정 없음, 자동 수집 없음. 마커는 점검 좌표이지 결함 확정이 아니다.

## 종합 요약
- KPI: 점검 심볼 <N> · 총 <B>봉 · 실제 이슈 <M>건 · 관찰 <O>건 · 반려 <K>건 · [심각 <a> · 높음 <b> · 중간 <c> · 낮음 <d> · 정보 <e>]
- 소비경로 로드: ${ev.any_load_failure ? '실패 있음(⚠)' : '전 심볼 load_bars 성공'} · 합계 ${JSON.stringify(ev.totals || {})}

| 심볼 | 봉수 | 로드 | OHLC위반 | 갭 | 이상치 | MA20/200 | 이슈 |
|---|---|---|---|---|---|---|---|
(심볼별 1행 — 증거 JSON 값으로 채움)

## 실제 이슈
### [DQ-01] <제목> — \`data/market/<SYM>_daily.csv:<행>\`
- **심각도 / 분류 / 차원**: <심각도> / <분류> / <차원>
- **관찰**: …
- **근거**: \`좌표\` (열어 확인)
- **소비 영향**: load_bars/ReplayPriceFeed/백테스트에 …
- **권고**: … (관찰 — 자동 적용 안 함)
- **적대 검증**: factual=<t/f> · is_issue=<t/f> · <검증관 메모>
(이슈 수만큼 DQ-02 … 반복. 없으면 '실제 이슈 0건 — 데이터 건전'.)

## 관찰·건전성 (이슈 아님)
- <차원>: <관찰> — \`좌표\` (예: 충분성 — 100봉이라 MA20 은 되나 MA200 미달, indicators.py MA200 사용 시 워밍업 부족 / 신선도 — 마지막 봉 <date>)

## 반려된 후보 (오탐 방지 기록)
- <제목> — \`좌표\` — 반려 사유: … (factual=false 또는 무해 오분류)
(없으면 '반려 0건')

## 점검 한계·미검증
- (수집기 실패 섹션·시간/토큰 제약으로 못 연 곳·외부 대조 없이 판단 못 한 이상치 등.)`
}

function selfReviewPrompt(draft, ev, stats) {
  return `${REPO_NOTE}

너는 '데이터 부서'의 최종 자기검토·기록관이다. 아래 리포트 초안을 **제출 전 마지막으로 스스로 검토**해 오류를 바로잡고, 5축 자기평가를 붙여 파일로 기록한다.

## 리포트 초안
${draft}

## 참고 통계(파이프라인 산출)
${JSON.stringify(stats, null, 2)}

## 증거 스냅샷
${ev.evidence_path} — Bash 로 열어 대조: cat "${ev.evidence_path}"

## 1단계: 사실 자기검토 (하나씩 실제로 확인하고, 어긋나면 초안을 고친다)
1. 실제 이슈의 좌표(CSV 파일:행 / JSON 키)가 실재하고 인용 수치가 맞는가? 표본 몇 개를 Bash 로 확인(예: sed -n '<행>p' data/market/<SYM>_daily.csv). 틀리면 강등/삭제.
2. 요약표의 심볼별 봉수·위반·갭·이상치 수가 증거 JSON 과 일치하는가? 불일치 시 수정.
3. 무해·관찰이 '실제 이슈'에 섞이지 않았는가? (공휴일 갭·실적 변동·MA200 미달을 결함으로 올리지 않았는가)
4. 저장소/데이터에 없는 것을 지어낸 창작이 없는가? 있으면 삭제.
5. 읽기 전용을 지켰는가? Bash 로 git status --short 확인 — 이 점검이 CSV/앱 코드를 바꾸지 않았는지 본다(리포트 md 와 무시되는 _dataquality JSON 외 변경이 있으면 '점검 한계'에 명시).

## 2단계: 5축 자기평가 (agent-self-evaluation — 초안 하단에 '## 자기평가' 섹션으로 추가)
각 축을 1~5로 채점하고 5 미만은 반드시 구체 증거를 든다("빈틈을 명명만 말고 보여줘라"). 머릿속 평균을 먼저 내지 말고 축별로 신선하게.
- 정확성: 좌표·수치가 증거와 일치하는가(환각/오인용 없는가)
- 완전성: 7차원·전 심볼을 빠짐없이 다뤘는가
- 명료성: 심사·데모에 쓸 사람이 이해할 구조인가
- 실행가능성: 권고가 바로 행동으로 옮겨지는가(모호한 "개선하라" 금지)
- 간결성: 군더더기·반복 없는가
그리고 '전체 평균(소수1자리)'과 '개선 1~3개(영향순)', 마지막에 "사용자가 이 평가에 동의할까?" 한 줄 자문을 적는다.

## 기록 (자기검토 통과 후에만)
1. Bash 로 타임스탬프: date +%Y%m%d_%H%M%S → 이 값을 TS 라 한다. 그리고 date +"%Y-%m-%d %H:%M" 로 표시용 시각도 얻는다.
2. 초안 상단 '> 생성: <PLACEHOLDER>' 를 표시용 시각(Asia/Seoul)으로 바꾼다.
3. 최종본(내용 동일 + 자기평가 섹션 포함)을 두 파일에 기록한다(Write, 절대경로 필요하면 저장소 루트를 앞에 붙임):
   - docs/reports/data_<TS>.md
   - docs/reports/data_latest.md
4. 두 파일이 실제로 생성됐는지 Bash 로 ls 확인한다.

## 출력
report_path(docs/reports/data_<TS>.md), latest_path(docs/reports/data_latest.md),
overall_summary(3~4문장 한국어 총평: 이슈 건수·건전성 + 가장 큰 관찰/권고 + 소비경로 상태),
self_eval_overall(5축 평균 숫자), counts({inspected, issues, observations, dropped, by_severity}),
corrections_made(자기검토에서 고친 것. 없으면 빈 배열).`
}

// ─────────────────────────────────────────── 파이프라인

phase('Scan')
const ev = await agent(SCAN_PROMPT, { label: 'collect-dataquality', phase: 'Scan', schema: SCAN_EVIDENCE_SCHEMA })
if (!ev || !ev.evidence_path) {
  throw new Error('품질표면 수집 실패 — evidence_path 를 얻지 못했다. collect_dataquality.py 실행을 확인하라.')
}
const symbols = (ev.symbols && ev.symbols.length) ? ev.symbols : []
if (!symbols.length) {
  throw new Error('점검할 심볼이 없다 — data/market/*.csv 존재를 확인하라.')
}
log(`품질 스냅샷: ${ev.evidence_path} · 심볼 ${JSON.stringify(symbols)} · 총 ${ev.total_bars}봉 · 로드실패=${ev.any_load_failure} · 합계=${JSON.stringify(ev.totals || {})}`)

phase('Inspect')
// 심볼별로 점검→적대검증을 파이프라인으로(배리어 없음 — 한 심볼이 검증되는 동안 다른 심볼은 아직 점검).
const perSymbol = await pipeline(
  symbols,
  (sym) => agent(inspectPrompt(sym, ev), { label: `inspect:${sym}`, phase: 'Inspect', schema: INSPECT_SCHEMA }),
  (ins, sym) => {
    if (!ins || !ins.findings || !ins.findings.length) {
      return { symbol: sym, findings: [], inspected_note: ins ? ins.inspected_note : '점검 실패' }
    }
    return parallel(ins.findings.map((f, i) => () =>
      agent(verifyPrompt(f, ev), { label: `verify:${sym}#${i + 1}`, phase: 'Verify', schema: VERDICT_SCHEMA })
        .then((verdict) => ({ ...f, verdict }))
    )).then((verified) => ({ symbol: sym, findings: verified.filter(Boolean), inspected_note: ins.inspected_note }))
  },
)

const groups = perSymbol.filter(Boolean)
const all = groups.flatMap((x) => x.findings || [])
const factual = all.filter((f) => f.verdict && f.verdict.factual)
const issues = factual.filter((f) => f.verdict.is_issue)
const observations = factual.filter((f) => !f.verdict.is_issue)
const dropped = all.filter((f) => !(f.verdict && f.verdict.factual))
log(`점검·검증 완료: 후보 ${all.length} · 실제이슈 ${issues.length} · 관찰 ${observations.length} · 반려 ${dropped.length}`)

const bySeverity = {}
for (const f of issues) {
  const s = (f.verdict && f.verdict.corrected_severity) || f.severity || '정보'
  bySeverity[s] = (bySeverity[s] || 0) + 1
}
const stats = {
  inspected: all.length, issues: issues.length, observations: observations.length,
  dropped: dropped.length, by_severity: bySeverity,
}

phase('Report')
const draft = await agent(synthPrompt({ issues, observations, dropped }, ev, stats), { label: 'synthesize', phase: 'Report' })
const result = await agent(selfReviewPrompt(draft, ev, stats), { label: 'self-review+write', phase: 'Report', schema: WRITE_RESULT_SCHEMA })

log(`리포트 기록: ${result && result.report_path ? result.report_path : '(경로 미반환)'}`)
return result
