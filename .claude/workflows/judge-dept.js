// 402 Guard — 심사용 부서 (트랙 B: 개발/평가 지원 워크플로우)
//
// 저장소 실물(코드·artifacts·테스트·git)을 해커톤 심사 4축에 대조 평가해
// docs/reports/judging_<TS>.md 리포트를 만든다. 추정·창작 금지 — 모든 근거는
// 결정론적 증거 스냅샷(scripts/collect_evidence.py)과 저장소 file:line 에 붙는다.
//
// 파이프라인: 증거수집 → 축별 심사(팬아웃 4) → 적대 검증(축별) → 종합 → 자기검토·기록.
//
// 온디맨드 재실행(서브에이전트처럼):
//   Workflow({ name: 'judge-dept' })                              // 이름 등록이 잡히면
//   Workflow({ scriptPath: '.claude/workflows/judge-dept.js' })   // 항상 보장되는 경로
// 미래 세션에서 "심사 부서 돌려줘" = 이 워크플로우 실행.

export const meta = {
  name: 'judge-dept',
  description: '402 Guard 를 해커톤 심사 4축에 대조 자가평가 — 증거수집→축별 심사(팬아웃)→적대 검증→자기검토→리포트(docs/reports)',
  phases: [
    { title: 'Evidence', detail: 'collect_evidence.py 실행 + JSON 스냅샷' },
    { title: 'Review', detail: '심사 4축 병렬 평가(증거 기반)' },
    { title: 'Verify', detail: '축별 주장 적대 검증(인용 실재·수치 일치)' },
    { title: 'Synthesize', detail: '종합 + 자기검토 후 리포트 기록' },
  ],
}

// ─────────────────────────────────────────── 공통 규범

const REPO_NOTE = `현재 작업 디렉터리가 저장소 루트다. Python 은 .venv/Scripts/python.exe (3.10 고정).
Windows + 한국어 경로라 Python 실행 시 반드시 앞에 PYTHONIOENCODING=utf-8 를 붙인다(안 붙이면 한국어 출력이 깨진다).`

const SOURCE_OF_TRUTH = `## 심사 기준의 출처 (반드시 지킬 것)
- 1차 기준 = 킥오프 세션 '전사' 기반 문서: docs/hackathon_essentials_0721.md §1(심사 4축) + docs/FEATURES.md §5.
- 대회 공식 신청 페이지 원문은 아직 미확보다(docs/handoff.md §4 — 사이트 JS 렌더링으로 조사 실패).
  전사와 신청 페이지가 다를 수 있는 지점은 단정하지 말고 criteria_mismatch(기준 불일치·미검증)로 분리 표기한다.
- 모든 부합 근거는 실물에 붙인다: 증거 JSON 의 키/값, 저장소 file:line, artifacts 파일명.
  근거를 못 찾으면 등급을 낮추고 '미검증'으로 적는다. 추정·창작·기억 인용 금지.
- docs(FEATURES/differentiation/submission)의 주장은 '사실'이 아니라 '검증 대상'이다 — 코드/아티팩트로 뒷받침되는지 직접 확인한다.`

// 심사 4축 — 킥오프 전사(hackathon_essentials_0721.md §1) 기준. look 은 힌트일 뿐, 반드시 실제로 열어 확인.
const AXES = [
  {
    key: 'innovation', axis: '① 혁신성·UX·상업성',
    criteria: '직관적/새로운 UX 로 기존 문제를 푸는가. 당위성(왜 카드망이 아니라 블록체인·왜 사람 없는 자율결제인가)이 분명한가. 상업성.',
    look: '재포지셔닝(docs/differentiation.md §1·§7), 첫 화면 KPI 가 수익률이 아니라 시도·차단·유출·오탐인지(scripts/red_team.py, web/static), 공격 콘솔(--attacker 로 심사위원 입력). 당위성 블록(docs/submission.md §2-3).',
  },
  {
    key: 'ai', axis: '② AI 활용도 (Gemini)',
    criteria: 'Gemini 등 AI 를 실제로 활용하는가. 호출 수준이면 충분(ADK/Vertex 구조화는 가점 아님). AI 의 기여가 무엇인지 정량으로.',
    look: 'agents/gemini_decider.py(판단), 규칙 폴백, TA 보강(market/indicators.py). 증거 JSON backtests 에서 brain=gemini 런의 수익률/MDD/gemini_fallbacks. 알려진 한계(직접 확인): AI 가 규칙 신호 위에서 보류만 가능 → 수익 정량기여가 약함.',
  },
  {
    key: 'infra', axis: '③ 기술·인프라 연동',
    criteria: 'USDC on Solana / Solana Pay / pay.sh, 또는 AP2·A2A·x402 등(병렬 예시). 결제 레이어로 솔라나를 실제로 쓰는가.',
    look: 'payments/x402_solana.py, payments/ap2_mandate.py, shared/a2a_messages.py, payments/guard.py. 증거 JSON guard_markers 로 구현 여부 대조(check_demand·check_delivery·exact !=·Memo AT1·allowed_asset). HTTP 402 서비스는 http_402_code_hits 로 확인(0 이면 미구현=갭). pay.sh 미연동.',
  },
  {
    key: 'live', axis: '④ 실제 구동',
    criteria: '목업이 아니라 진짜 트랜잭션. 실행 로그·tx 이력. 로컬넷/데브넷 라이브.',
    look: '증거 JSON tx_artifacts(by_network, cross_check usdc_ok/stock_ok, sample_explorer), red_team KPI(시도·차단·유출·오탐), 테스트 6종 통과 여부. devnet 증빙 개수(현재 explorer cluster=devnet 건수).',
  },
]

// ─────────────────────────────────────────── 스키마

const EVIDENCE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    evidence_path: { type: 'string', description: '저장소 루트 기준 상대경로 docs/reports/_evidence_<ts>.json' },
    tests_all_pass: { type: 'boolean' },
    tests_count: { type: 'integer' },
    red_team_kpi: { type: 'object', additionalProperties: true },
    devnet_evidence_present: { type: 'boolean' },
    http_402_code_hits: { type: 'integer' },
    collector_error: { type: 'string', description: '수집기 실행 실패 시 그 사유(없으면 생략)' },
  },
  required: ['evidence_path'],
}

const AXIS_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    axis: { type: 'string' },
    grade: { type: 'string', enum: ['강', '중', '약', '불충분'] },
    evidence: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          claim: { type: 'string' },
          source: { type: 'string', description: 'file:line 또는 증거 JSON 키 또는 artifacts 파일명' },
          verified: { type: 'boolean', description: '실제로 열어 확인했는가' },
        },
        required: ['claim', 'source'],
      },
    },
    gaps: { type: 'array', items: { type: 'string' } },
    todos: { type: 'array', items: { type: 'string' } },
    criteria_mismatch: { type: 'array', items: { type: 'string' } },
  },
  required: ['axis', 'grade', 'evidence', 'gaps', 'todos'],
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    axis: { type: 'string' },
    checked: { type: 'integer' },
    unsupported: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: { claim: { type: 'string' }, reason: { type: 'string' } },
        required: ['claim', 'reason'],
      },
    },
    fabrication_found: { type: 'boolean' },
    notes: { type: 'string' },
  },
  required: ['axis', 'unsupported', 'fabrication_found'],
}

const WRITE_RESULT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    report_path: { type: 'string' },
    latest_path: { type: 'string' },
    overall_summary: { type: 'string' },
    corrections_made: { type: 'array', items: { type: 'string' } },
  },
  required: ['report_path', 'overall_summary'],
}

// ─────────────────────────────────────────── 프롬프트

const EVIDENCE_PROMPT = `${REPO_NOTE}

'심사용 부서'의 증거 수집 단계다. 결정론적 증거 스냅샷을 만들고 위치를 보고한다.

1. Bash 실행: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m scripts.collect_evidence
   (실패해도 중단하지 말 것 — stderr 를 collector_error 로 담아 가능한 최선을 반환)
2. docs/reports/_evidence_*.json 중 파일명 타임스탬프가 가장 최근인 파일을 찾는다.
3. 그 파일을 읽어 summary 섹션 값을 확인한다.
4. 반환: evidence_path(상대경로), summary 의 tests_all_pass·tests_count·red_team_kpi·devnet_evidence_present·http_402_code_hits.`

function reviewPrompt(ax, ev) {
  return `${REPO_NOTE}

너는 402 Guard 프로젝트 '심사용 부서'의 평가관이다. 아래 한 축만 평가한다.

${SOURCE_OF_TRUTH}

## 평가 대상 축
${ax.axis}
기준: ${ax.criteria}
살펴볼 곳(힌트 — 반드시 실제로 열어 확인): ${ax.look}

## 증거
결정론적 증거 스냅샷: ${ev.evidence_path}
먼저 Bash 로 열어라: cat "${ev.evidence_path}"
그리고 힌트가 가리키는 저장소 코드/문서를 직접 열어 file:line 을 확인하라(Read/Grep).
요약 사실: 테스트 ${ev.tests_all_pass ? '전부 통과' : '일부 실패/미확인'}(${ev.tests_count}종), red_team ${JSON.stringify(ev.red_team_kpi || {})}, devnet 증빙 ${ev.devnet_evidence_present ? '있음' : '없음'}, HTTP402 코드매치 ${ev.http_402_code_hits}건.

## 출력 규칙
- 등급(강/중/약/불충분)은 근거가 실물로 뒷받침되는 정도에 비례.
- evidence 각 항목은 반드시 실제로 확인한 것만. source 에 file:line / 증거 JSON 키 / artifacts 파일명을 적고 verified 를 정직하게.
- 확인 못 한 주장은 넣지 말고 gaps 로. 개선안은 todos 로(실행 가능한 구체 행동).
- 신청 페이지와 다를 수 있는 기준 해석은 criteria_mismatch 로.`
}

function verifyPrompt(review, ax, ev) {
  return `${REPO_NOTE}

너는 적대적 검증관이다. 아래 '${ax.axis}' 축 평가 결과의 근거가 실물에 실제로 존재하는지 회의적으로 재검증한다.

## 평가 결과(JSON)
${JSON.stringify(review, null, 2)}

## 증거 스냅샷
${ev.evidence_path} — Bash 로 열어 대조: cat "${ev.evidence_path}"

## 검증 규칙
- 각 evidence 의 source(file:line / JSON키 / 파일명)를 실제로 열어 그 주장이 맞는지 확인한다.
- 숫자가 증거 JSON 과 불일치하면 unsupported 에 담는다.
- file:line 이 실재하지 않거나 그 라인 내용이 주장과 다르면 unsupported.
- 저장소에 없는 것을 있다고 한 창작이 하나라도 있으면 fabrication_found=true.
- 통과한 근거는 나열하지 말고, 문제 있는 것만 unsupported 로. checked 에 확인한 근거 수를 적는다.`
}

function synthPrompt(clean, ev) {
  return `${REPO_NOTE}

너는 '심사용 부서'의 종합 작성관이다. 4개 축의 평가와 각 축 적대 검증 결과를 받아 심사 자가평가 리포트(마크다운) 초안을 쓴다.

${SOURCE_OF_TRUTH}

## 입력 (축별 평가 review + 검증 verdict)
${JSON.stringify(clean, null, 2)}

증거 스냅샷 경로: ${ev.evidence_path}

## 작성 규칙
- verdict.unsupported 로 지목된 근거는 제거하거나 '미검증'으로 강등한다. fabrication_found=true 축은 그 사실을 '적대 검증 메모'에 명시.
- 등급은 실물 근거 강도에 비례. 요약표 등급과 각 축 등급이 일치해야 한다.
- 아래 정확한 구조의 마크다운만 출력(코드펜스로 감싸지 말 것):

# 402 Guard 심사 자가평가 리포트
> 생성: <PLACEHOLDER — 다음 단계에서 타임스탬프 기입>
> 근거 스냅샷: ${ev.evidence_path}
> 심사 기준 출처: 킥오프 전사(docs/hackathon_essentials_0721.md §1) + docs/FEATURES.md §5. 신청 페이지 원문 미확보(docs/handoff.md §4).

## 종합 요약
| 축 | 등급 | 한 줄 근거 |
|---|---|---|
(4행 — ①②③④)

## 축① 혁신성·UX·상업성
**등급**: …
**부합 근거**
- 주장 — \`출처(file:line/JSON키/파일명)\`
**갭**
- …
**개선 TODO**
- …

## 축② AI 활용도 (Gemini)
(동일 구조)

## 축③ 기술·인프라 연동
(동일 구조)

## 축④ 실제 구동
(동일 구조)

## 기준 불일치·미검증
- (criteria_mismatch 모음 + 신청 페이지 원문 미확보 사실)

## 적대 검증 메모
- (verdict 에서 반려된 주장·창작 여부. 없으면 '반려 0건 — 모든 근거가 실물로 확인됨')`
}

function selfReviewPrompt(draft, ev) {
  return `${REPO_NOTE}

너는 '심사용 부서'의 최종 자기검토·기록관이다. 아래 리포트 초안을 **제출 전 마지막으로 스스로 검토**해 오류를 바로잡고 파일로 기록한다.

## 리포트 초안
${draft}

## 증거 스냅샷
${ev.evidence_path} — Bash 로 열어 대조: cat "${ev.evidence_path}"

## 자기검토 체크리스트 (하나씩 실제로 확인하고, 어긋나면 초안을 고친다)
1. 리포트의 모든 수치가 증거 JSON 과 일치하는가?(테스트/red_team KPI/tx 개수/백테스트 수익률 등) 불일치 시 수정.
2. 모든 부합 근거에 출처(file:line/JSON키/파일명)가 있는가? 출처 없는 주장은 '미검증'으로 바꾸거나 삭제.
3. 저장소에 없는 것을 있다고 한 창작이 없는가? 있으면 삭제.
4. 등급이 근거 강도와 모순되지 않고, 요약표 등급 == 각 축 등급인가?
5. '기준 불일치·미검증' 섹션이 신청 페이지 원문 미확보 사실을 담고 있는가?

## 기록 (자기검토 통과 후에만)
1. Bash 로 타임스탬프: date +%Y%m%d_%H%M%S  → 이 값을 TS 라 한다. 그리고 date +"%Y-%m-%d %H:%M" 로 표시용 시각도 얻는다.
2. 초안 상단 '> 생성: <PLACEHOLDER>' 를 표시용 시각(Asia/Seoul)으로 바꾼다.
3. 최종본(내용 동일)을 두 파일에 기록한다:
   - docs/reports/judging_<TS>.md
   - docs/reports/judging_latest.md
   Write 도구가 절대경로를 요구하면 저장소 루트 절대경로를 앞에 붙인다.
4. 두 파일이 실제로 생성됐는지 ls 로 확인한다.

## 출력
report_path(docs/reports/judging_<TS>.md), latest_path(docs/reports/judging_latest.md),
overall_summary(3~4문장 한국어 총평: 4축 등급 요지 + 가장 큰 갭), corrections_made(자기검토에서 고친 것 목록. 없으면 빈 배열).`
}

// ─────────────────────────────────────────── 파이프라인

phase('Evidence')
const ev = await agent(EVIDENCE_PROMPT, { label: 'collect-evidence', phase: 'Evidence', schema: EVIDENCE_SCHEMA })
if (!ev || !ev.evidence_path) {
  throw new Error('증거 수집 실패 — evidence_path 를 얻지 못했다. collect_evidence.py 실행을 확인하라.')
}
log(`증거 스냅샷: ${ev.evidence_path} · 테스트 ${ev.tests_count}종 통과=${ev.tests_all_pass} · devnet=${ev.devnet_evidence_present} · HTTP402코드=${ev.http_402_code_hits}`)

phase('Review')
// 축별로 심사→적대검증을 파이프라인으로(배리어 없음 — 한 축이 검증되는 동안 다른 축은 아직 심사).
const perAxis = await pipeline(
  AXES,
  (ax) => agent(reviewPrompt(ax, ev), { label: `review:${ax.key}`, phase: 'Review', schema: AXIS_SCHEMA }),
  (review, ax) => {
    if (!review) return null
    return agent(verifyPrompt(review, ax, ev), { label: `verify:${ax.key}`, phase: 'Verify', schema: VERDICT_SCHEMA })
      .then((verdict) => ({ key: ax.key, axis: ax.axis, review, verdict }))
  },
)
const clean = perAxis.filter(Boolean)
log(`축별 심사·검증 완료: ${clean.length}/${AXES.length}`)
if (!clean.length) throw new Error('축별 심사가 모두 실패했다.')

phase('Synthesize')
const draft = await agent(synthPrompt(clean, ev), { label: 'synthesize', phase: 'Synthesize' })
const result = await agent(selfReviewPrompt(draft, ev), { label: 'self-review+write', phase: 'Synthesize', schema: WRITE_RESULT_SCHEMA })

log(`리포트 기록: ${result && result.report_path ? result.report_path : '(경로 미반환)'}`)
return result
