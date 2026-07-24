// 402 Guard — 버그 전담 부서 (트랙 B: 개발/평가 지원 워크플로우)
//
// 앱 전체 모듈을 4렌즈(정확성·엣지케이스·에러처리·커버리지 공백)로 스캔해
// docs/reports/bug_<TS>.md 리포트를 만든다. 재현 절차 + 수정안을 '제안'만 한다 —
// 코드를 고치지 않는다(읽기 전용). 자동 수정은 승인 후 별도 작업에서만.
//
// 모든 근거는 결정론적 버그표면 스냅샷(scripts/collect_bugscan.py)과 저장소 file:line
// 에 붙는다. 마커는 '점검 지점'이지 버그 확정이 아니다 — 헌터가 코드를 열어 판정하고,
// 적대 검증관이 재현으로 재확인한다(오탐 방지).
//
// 파이프라인: 버그표면 수집 → 모듈그룹 사냥(팬아웃 4) → 건별 적대 검증 → 종합 → 자기검토·기록.
//
// 온디맨드 재실행(서브에이전트처럼):
//   Workflow({ name: 'bug-dept' })                              // 이름 등록이 잡히면
//   Workflow({ scriptPath: '.claude/workflows/bug-dept.js' })   // 항상 보장되는 경로
// 미래 세션에서 "버그 부서 돌려줘" = 이 워크플로우 실행.

export const meta = {
  name: 'bug-dept',
  description: '402 Guard 앱 전체를 4렌즈(정확성·엣지케이스·에러처리·커버리지)로 스캔 → 재현 절차 + 수정안 리포트(docs/reports). 읽기 전용 — 자동 수정 없음.',
  phases: [
    { title: 'Scan', detail: 'collect_bugscan.py 실행 + JSON 스냅샷' },
    { title: 'Hunt', detail: '모듈그룹 4개 병렬 사냥(4렌즈 전부)' },
    { title: 'Verify', detail: '발견 건별 적대 검증(재현·실재 재확인)' },
    { title: 'Report', detail: '종합 + 자기검토 후 리포트 기록' },
  ],
}

// ─────────────────────────────────────────── 공통 규범

const REPO_NOTE = `현재 작업 디렉터리가 저장소 루트다. Python 은 .venv/Scripts/python.exe (3.10 고정).
Windows + 한국어 경로라 Python 실행 시 반드시 앞에 PYTHONIOENCODING=utf-8 를 붙인다(안 붙이면 한국어 출력이 깨진다).
localnet 검증기가 필요하면 WSL 에서 돈다 — 하지만 이 부서는 네트워크가 필요 없다(정적 분석 + 인프로세스 재현).`

const SCAN_PRINCIPLE = `## 버그 부서 원칙 (반드시 지킬 것)
- 읽기 전용이다. 저장소의 앱 코드·테스트를 한 줄도 고치지 않는다. 자동 수정 금지 — 재현 절차와 수정'안'만 제안한다.
- 재현하려고 임시 스크립트를 돌릴 때는 python -c "..." 한 줄을 쓰거나, 굳이 파일이 필요하면 OS 임시폴더에 쓰고 저장소에는 남기지 않는다.
- 모든 버그 주장은 실물에 붙인다: file:line 을 실제로 열어 확인하고, 재현은 구체적으로(명령/입력 → 기대 vs 실제).
- 수집기 마커(bare except·TODO·assert 등)는 '여기를 봐라'는 좌표일 뿐 버그 확정이 아니다. 열어보고 정상이면 버리라.
- 근거를 못 대면 등급을 낮추고 confidence=추정 으로. 저장소에 없는 것을 지어내지 마라(창작 금지).
- docs 의 '알려진 한계'(손절 없음·Gemini 보류만 등)는 이미 알려진 설계 선택이다 — 그걸 '새 버그'로 재보고하지 마라(중복). 진짜 결함·회귀·엣지케이스에 집중.`

// 4렌즈 — 모든 헌터가 담당 파일에 이 넷을 전부 적용한다.
const LENSES = `## 스캔 4렌즈 (담당 파일에 전부 적용)
1. 정확성·로직 — 금액/수량 계산, 부호(+/-), 경계 비교(< vs <=), base units 정수 변환, 수수료 산식, 반올림/절사, 순서 의존.
2. 엣지케이스·경계값 — None·0·음수·빈 리스트/딕트·Decimal 정밀도·타임존/naive datetime·워밍업 봉 부족·피드 소진·큰 수 오버플로.
3. 에러처리 — 예외 삼킴(except 뒤 pass/무시)·bare except·실패를 성공으로 오인·Gemini 폴백 오염·부분 실패 방치·조용한 미완성·재시도 누락.
4. 회귀·커버리지 공백 — 전용 test 가 없는 모듈(coverage_map.uncovered 우선)·미검증 분기·상태 전이(세션 경계·긴급정지·예약회계 원복) — 어떤 입력이 그 미검증 경로를 깨뜨릴 수 있는지 구체적으로.`

// 팬아웃 4그룹 — 응집도 있는 모듈 묶음(관련 파일을 같은 헌터가 함께 읽어 상호작용을 본다).
const GROUPS = [
  {
    key: 'payments', title: 'G1 결제·가드 본체 (402 Guard 심장)',
    files: 'payments/guard.py, payments/ap2_mandate.py, payments/x402_solana.py',
    focus: '금액 base units 오차·수취인/자산/주문번호 대사·exact 비교 부호(!= 여야)·Memo 바인딩(AT1:)·서명 dedup·한도 예약/원복·check_delivery 온체인 재조회. scripts/red_team.py 가 이 경로를 태우니 red_team 이 초록인데도 남는 빈틈을 노려라.',
  },
  {
    key: 'agents', title: 'G2 에이전트 (구매·판매·판단)',
    files: 'agents/trading_agent.py, agents/broker_agent.py, agents/gemini_decider.py',
    focus: '구매자의 청구서 검증 경로(build_payment→guard), 브로커 정산/수수료/재고, Gemini 판단→규칙 폴백 전이(JSON 파싱 실패·429·타임아웃)와 폴백 오염, 수량/가격 Decimal 취급.',
  },
  {
    key: 'market_shared', title: 'G3 시세·지표·공유 모델',
    files: 'market/indicators.py, market/price_feed.py, shared/a2a_messages.py, shared/models.py',
    focus: '지표 경계(MA 창 부족·0 분모·빈 시리즈), 리플레이 피드 소진/워밍업/전일종가, a2a 메시지·Quote/모델의 필드 검증·직렬화·Decimal 왕복.',
  },
  {
    key: 'web_config', title: 'G4 웹 엔진·서버·저장·설정',
    files: 'web/engine.py, web/server.py, web/store.py, web/events.py, web/briefing.py, config.py',
    focus: '전역 싱글턴 엔진의 상태·세션 경계·긴급정지·틱 루프, FastAPI 입력 검증(CONTROL_TOKEN 게이트·pydantic extra), 저장/복원 왕복(FakeStore vs Firestore), 브리핑 수치, config 인코딩/env/base units. 미커버 우선: web/server.py·web/briefing.py.',
  },
]

// ─────────────────────────────────────────── 스키마

const SCAN_EVIDENCE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    evidence_path: { type: 'string', description: '저장소 루트 기준 상대경로 docs/reports/_bugscan_<ts>.json' },
    tests_all_pass: { type: 'boolean' },
    tests_count: { type: 'integer' },
    red_team_ok: { type: 'boolean' },
    uncovered: { type: 'array', items: { type: 'string' }, description: 'coverage_map.uncovered(전용 test 없는 모듈)' },
    risk_marker_totals: { type: 'object', additionalProperties: true },
    collector_error: { type: 'string', description: '수집기 실행 실패 시 그 사유(없으면 생략)' },
  },
  required: ['evidence_path'],
}

const LENS_ENUM = ['정확성', '엣지케이스', '에러처리', '커버리지']
const SEV_ENUM = ['심각', '높음', '중간', '낮음', '정보']

const FINDING_PROPS = {
  file: { type: 'string', description: '저장소 상대경로 예: payments/guard.py' },
  line: { type: 'integer', description: '1-indexed 대표 라인' },
  lens: { type: 'string', enum: LENS_ENUM },
  severity: { type: 'string', enum: SEV_ENUM, description: '심각=자금유출/오정산/크래시, 높음=명백한 로직오류, 중간=엣지케이스/에러삼킴, 낮음/정보=경미·개선' },
  title: { type: 'string', description: '한 줄 제목' },
  symptom: { type: 'string', description: '무엇이 왜 잘못되는가' },
  reproduction: { type: 'string', description: '구체적 재현: 명령/입력 → 기대 vs 실제. 커버리지 건은 어떤 입력이 미검증 경로를 깨뜨리는지.' },
  evidence_source: { type: 'string', description: 'file:line(실제로 연 것) 또는 증거 JSON 키' },
  fix_proposal: { type: 'string', description: '수정안(제안만 — 적용 안 함)' },
  confidence: { type: 'string', enum: ['확실', '추정'] },
}

const HUNT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    group: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: FINDING_PROPS,
        required: ['file', 'line', 'lens', 'severity', 'title', 'symptom', 'reproduction', 'evidence_source', 'fix_proposal', 'confidence'],
      },
    },
    scanned_note: { type: 'string', description: '무엇을 읽었고 무엇을 못 봤는지(한계) 한 줄' },
  },
  required: ['group', 'findings'],
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    real: { type: 'boolean', description: '실제 결함인가(회의적으로 — 못 확인하면 false)' },
    reproduced: { type: 'boolean', description: '코드를 열어/돌려 재현했는가' },
    corrected_severity: { type: 'string', enum: SEV_ENUM, description: '검증 후 조정한 심각도' },
    reason: { type: 'string', description: 'real 판정 근거(file:line 확인 결과)' },
    notes: { type: 'string' },
  },
  required: ['real', 'reproduced', 'reason'],
}

const WRITE_RESULT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    report_path: { type: 'string' },
    latest_path: { type: 'string' },
    overall_summary: { type: 'string' },
    counts: { type: 'object', additionalProperties: true, description: '{checked, confirmed, dropped, by_severity:{}}' },
    corrections_made: { type: 'array', items: { type: 'string' } },
  },
  required: ['report_path', 'overall_summary'],
}

// ─────────────────────────────────────────── 프롬프트

const SCAN_PROMPT = `${REPO_NOTE}

'버그 부서'의 버그표면 수집 단계다. 결정론적 스냅샷을 만들고 위치를 보고한다.

1. Bash 실행: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m scripts.collect_bugscan
   (실패해도 중단하지 말 것 — stderr 를 collector_error 로 담아 가능한 최선을 반환)
2. docs/reports/_bugscan_*.json 중 파일명 타임스탬프가 가장 최근인 파일을 찾는다.
3. 그 파일을 읽어 summary 섹션 값을 확인한다.
4. 반환: evidence_path(상대경로), summary 의 tests_all_pass·tests_count·red_team_ok·uncovered·risk_marker_totals.`

function huntPrompt(g, ev) {
  return `${REPO_NOTE}

너는 402 Guard '버그 부서'의 헌터다. 아래 한 모듈그룹만 담당해 진짜 버그를 찾는다.

${SCAN_PRINCIPLE}

${LENSES}

## 담당 그룹
${g.title}
파일: ${g.files}
집중: ${g.focus}

## 근거
결정론적 버그표면 스냅샷: ${ev.evidence_path}
먼저 Bash 로 열어라: cat "${ev.evidence_path}"
- risk_markers 의 file:line 매치가 네 담당 파일에 있으면 그 지점을 우선 열어본다(단 마커=좌표일 뿐, 열어서 정상이면 버린다).
- coverage_map.uncovered 에 네 담당 모듈이 있으면 미검증 분기를 특히 판다.
- module_index 로 표면이 큰 파일을 가늠한다.
그리고 담당 파일들을 Read 로 실제로 열어 4렌즈를 전부 적용하라.

## 재현 확인(선택이지만 강력)
가능하면 python -c "..." 한 줄로 실제 재현해보라(예: 경계값 입력 → 예외/오답). 저장소에 파일을 남기지 마라.
  예: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "from payments... import ...; print(...)"

## 출력 규칙
- 각 finding 은 file·line·lens·severity·title·symptom·reproduction·evidence_source·fix_proposal·confidence 를 채운다.
- reproduction 은 반드시 구체적으로: 어떤 호출/입력에서 기대 vs 실제가 어떻게 갈리는지. 추상적 우려는 넣지 마라.
- 확인 못 한 것은 confidence=추정 으로 낮추고, 아예 근거가 없으면 넣지 마라.
- 이미 알려진 설계 한계(손절 없음 등)나 스타일 취향은 버그가 아니다 — 넣지 마라.
- 없으면 findings 를 빈 배열로. 억지로 만들지 마라(오탐이 부서 신뢰를 깎는다).`
}

function verifyPrompt(f, ev) {
  return `${REPO_NOTE}

너는 적대적 검증관이다. 아래 버그 후보가 '진짜 결함'인지 회의적으로 재검증한다. 기본값은 real=false 다 — 확실히 확인될 때만 true.

## 후보(JSON)
${JSON.stringify(f, null, 2)}

## 검증 절차
1. evidence_source 의 file:line 을 Read 로 실제로 열어 그 라인이 주장과 맞는지 확인한다(다르면 real=false).
2. 가능하면 재현한다: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "..." 로 기대 vs 실제를 직접 확인(저장소에 파일 남기지 말 것). 재현되면 reproduced=true.
3. 정상 동작인데 오해한 것(가드가 이미 막음·상위에서 검증·의도된 설계·이미 알려진 한계)이면 real=false 로 반려하고 reason 에 왜인지 적는다.
4. 심각도가 과장/과소면 corrected_severity 로 조정한다(자금유출/오정산/크래시만 심각).

## 규칙
- 통과 못 시킨 근거는 real=false. 저장소에 없는 것을 있다고 한 창작이면 real=false + reason 에 '창작'.
- reason 에는 네가 연 file:line 과 확인 결과를 구체적으로. 짐작 금지.`
}

function synthPrompt(data, ev) {
  return `${REPO_NOTE}

너는 '버그 부서'의 종합 작성관이다. 확인된 버그와 반려된 후보를 받아 버그 스캔 리포트(마크다운) 초안을 쓴다.

${SCAN_PRINCIPLE}

## 입력
확인된 버그(real=true) + 반려 후보(real=false):
${JSON.stringify(data, null, 2)}

증거 스냅샷 경로: ${ev.evidence_path}
현재 기준선: 테스트 ${ev.tests_all_pass ? '전부 통과' : '일부 실패/미확인'}(${ev.tests_count}종), red_team ${ev.red_team_ok ? 'OK' : '경고'}, 미커버 ${JSON.stringify(ev.uncovered || [])}.

## 작성 규칙
- 확정 목록에는 real=true 만. 심각도는 verdict.corrected_severity 가 있으면 그걸 우선. 심각도순(심각→정보) 정렬.
- 반려 후보는 '반려된 후보' 섹션에 사유와 함께(오탐 방지 기록). 확정 목록에 절대 섞지 마라.
- 요약표의 등급/개수와 본문이 정확히 일치해야 한다.
- 아래 정확한 구조의 마크다운만 출력(코드펜스로 전체를 감싸지 말 것):

# 402 Guard 버그 스캔 리포트
> 생성: <PLACEHOLDER — 다음 단계에서 타임스탬프 기입>
> 근거 스냅샷: ${ev.evidence_path}
> 스캔 범위: 앱 전체 모듈(agents·payments·market·web·shared·config) · 렌즈 4종(정확성·엣지케이스·에러처리·커버리지) · 심각도 전부
> 원칙: 읽기 전용 — 코드 자동 수정 없음. 재현 절차 + 수정안 '제안'만. 마커는 점검 지점이지 버그 확정이 아니다.

## 종합 요약
- KPI: 검토 후보 <N>건 · 확인 <M>건 · 반려 <K>건 · [심각 <a> · 높음 <b> · 중간 <c> · 낮음 <d> · 정보 <e>]
- 현재 기준선: 테스트 ${ev.tests_count}종 ${ev.tests_all_pass ? '통과' : '일부 실패'} · red_team ${ev.red_team_ok ? '유출0·오탐0' : '경고'} · 미커버 ${JSON.stringify(ev.uncovered || [])}

| # | 심각도 | 렌즈 | 모듈 | 제목 |
|---|---|---|---|---|
(확인된 버그 각 1행 — 심각도순)

## 확정 버그
### [BUG-01] <제목> — \`file.py:line\`
- **심각도 / 렌즈**: <심각도> / <렌즈>
- **증상**: …
- **재현**: <명령/입력> → 기대: … / 실제: …
- **근거**: \`file.py:line\` (열어 확인)
- **수정안**: … (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=<t/f> · <검증관 메모>
(확정 건 수만큼 BUG-02, 03 … 반복. 없으면 '확정 버그 0건' 이라고만.)

## 반려된 후보 (오탐 방지 기록)
- <제목> — \`file:line\` — 반려 사유: … (real=false)
(없으면 '반려 0건')

## 스캔 한계·미검증
- (수집기 실패 섹션·시간/토큰 제약으로 못 연 곳·커버리지 공백으로 남는 위험. uncovered 모듈을 여기 명시.)`
}

function selfReviewPrompt(draft, ev, stats) {
  return `${REPO_NOTE}

너는 '버그 부서'의 최종 자기검토·기록관이다. 아래 리포트 초안을 **제출 전 마지막으로 스스로 검토**해 오류를 바로잡고 파일로 기록한다.

## 리포트 초안
${draft}

## 참고 통계(파이프라인 산출)
${JSON.stringify(stats, null, 2)}

## 증거 스냅샷
${ev.evidence_path}

## 자기검토 체크리스트 (하나씩 실제로 확인하고, 어긋나면 초안을 고친다)
1. 확정 버그의 file:line 이 실재하고 그 라인이 주장과 맞는가? 표본 몇 개를 Bash 로 확인하라(예: sed -n '180p' payments/x402_solana.py). 틀리면 강등/삭제.
2. 각 재현이 구체적인가(명령/입력/기대/실제)? 추상적이면 강등하거나 '미검증'으로.
3. 저장소에 없는 것을 지어낸 창작이 없는가? 있으면 삭제.
4. 심각도가 근거와 모순되지 않고, 요약표 등급·개수 == 본문 목록인가?
5. 반려된 후보가 확정 목록에 섞이지 않았는가?
6. 읽기 전용을 지켰는가? Bash 로 git status --short 를 확인해, 이 스캔이 앱 코드/테스트를 바꾸지 않았는지 본다(리포트 md 와 무시되는 _bugscan JSON 외의 변경이 있으면 안 된다 — 있으면 '스캔 한계'에 명시).

## 기록 (자기검토 통과 후에만)
1. Bash 로 타임스탬프: date +%Y%m%d_%H%M%S → 이 값을 TS 라 한다. 그리고 date +"%Y-%m-%d %H:%M" 로 표시용 시각도 얻는다.
2. 초안 상단 '> 생성: <PLACEHOLDER>' 를 표시용 시각(Asia/Seoul)으로 바꾼다.
3. 최종본(내용 동일)을 두 파일에 기록한다(Write, 절대경로 필요하면 저장소 루트를 앞에 붙임):
   - docs/reports/bug_<TS>.md
   - docs/reports/bug_latest.md
4. 두 파일이 실제로 생성됐는지 Bash 로 ls 확인한다.

## 출력
report_path(docs/reports/bug_<TS>.md), latest_path(docs/reports/bug_latest.md),
overall_summary(3~4문장 한국어 총평: 확인 건수·심각도 분포 + 가장 큰 위험 + 커버리지 공백),
counts({checked, confirmed, dropped, by_severity}), corrections_made(자기검토에서 고친 것. 없으면 빈 배열).`
}

// ─────────────────────────────────────────── 파이프라인

phase('Scan')
const ev = await agent(SCAN_PROMPT, { label: 'collect-bugscan', phase: 'Scan', schema: SCAN_EVIDENCE_SCHEMA })
if (!ev || !ev.evidence_path) {
  throw new Error('버그표면 수집 실패 — evidence_path 를 얻지 못했다. collect_bugscan.py 실행을 확인하라.')
}
log(`버그표면 스냅샷: ${ev.evidence_path} · 테스트 ${ev.tests_count}종 통과=${ev.tests_all_pass} · red_team=${ev.red_team_ok} · 미커버=${JSON.stringify(ev.uncovered || [])}`)

phase('Hunt')
// 그룹별로 사냥→적대검증을 파이프라인으로(배리어 없음 — 한 그룹이 검증되는 동안 다른 그룹은 아직 사냥).
const perGroup = await pipeline(
  GROUPS,
  (g) => agent(huntPrompt(g, ev), { label: `hunt:${g.key}`, phase: 'Hunt', schema: HUNT_SCHEMA }),
  (hunt, g) => {
    if (!hunt || !hunt.findings || !hunt.findings.length) {
      return { group: g.key, findings: [], scanned_note: hunt ? hunt.scanned_note : '사냥 실패' }
    }
    return parallel(hunt.findings.map((f, i) => () =>
      agent(verifyPrompt(f, ev), { label: `verify:${g.key}#${i + 1}`, phase: 'Verify', schema: VERDICT_SCHEMA })
        .then((verdict) => ({ ...f, group: g.key, verdict }))
    )).then((verified) => ({ group: g.key, findings: verified.filter(Boolean), scanned_note: hunt.scanned_note }))
  },
)

const groups = perGroup.filter(Boolean)
const all = groups.flatMap((x) => x.findings || [])
const confirmed = all.filter((f) => f.verdict && f.verdict.real)
const dropped = all.filter((f) => !(f.verdict && f.verdict.real))
log(`사냥·검증 완료: 후보 ${all.length} · 확인 ${confirmed.length} · 반려 ${dropped.length}`)

const bySeverity = {}
for (const f of confirmed) {
  const s = (f.verdict && f.verdict.corrected_severity) || f.severity || '정보'
  bySeverity[s] = (bySeverity[s] || 0) + 1
}
const stats = { checked: all.length, confirmed: confirmed.length, dropped: dropped.length, by_severity: bySeverity }

phase('Report')
const draft = await agent(synthPrompt({ confirmed, dropped }, ev), { label: 'synthesize', phase: 'Report' })
const result = await agent(selfReviewPrompt(draft, ev, stats), { label: 'self-review+write', phase: 'Report', schema: WRITE_RESULT_SCHEMA })

log(`리포트 기록: ${result && result.report_path ? result.report_path : '(경로 미반환)'}`)
return result
