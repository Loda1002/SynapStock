# docs/reports — 에이전트 부서(트랙 B) 리포트 저장소

이 디렉터리는 **개발/평가 지원 에이전트 부서**(트랙 B)가 내는 리포트를 모은다.
최종 앱에는 들어가지 않는다 — 개발·자가평가를 돕는 조직이다. 상세 배경은
`CLAUDE.md` '에이전트 부서', `docs/differentiation.md`(402 Guard 재포지셔닝).

> **원칙**: 이 부서들은 앱 동작·제출물 자체를 바꾸지 않는다(품질만 높인다).
> 리포트는 저장소 실물(코드·artifacts·테스트·git)에 근거하며 **추정·창작을 섞지 않는다**.

---

## 부서 1 — 심사용 부서 (`judge-dept`) ✅ 가동

402 Guard 를 해커톤 **심사 4축**에 대조해 축별 부합도·근거·갭·개선 TODO 리포트를 낸다.

### 재실행 (온디맨드 — 서브에이전트처럼 이름/경로로 호출)

```
Workflow({ scriptPath: ".claude/workflows/judge-dept.js" })
```

미래 세션에서 사용자가 **"심사 부서 돌려줘"** 라고 하면 이 워크플로우를 실행하면 된다.
(참고: 이 하니스는 `.claude/workflows/*.js` 를 `Workflow({name})` 레지스트리에 자동 등록하지
않는다 — 내장 워크플로우만 name 으로 잡힌다. 그래서 **scriptPath 로 호출**한다. 항상 보장되는 경로다.)

### 파이프라인 (다중 에이전트 — 검증 허점을 남기지 않기 위해 워크플로우 형태)

1. **Evidence** — `scripts/collect_evidence.py`(결정론적)를 실행해 실물 사실을
   `docs/reports/_evidence_<TS>.json` 스냅샷으로 덤프.
2. **Review** (팬아웃 4) — 축①~④를 각 에이전트가 **증거 JSON + 저장소 file:line 만** 근거로 평가.
3. **Verify** (축별) — 적대적 검증관이 각 근거의 출처가 실재하는지, 수치가 증거와 일치하는지 재확인.
4. **Synthesize** — 종합 작성관이 리포트 초안 작성 → **최종 자기검토관**이 초안 전체를 증거와 재대조
   (인용 실재·수치 일치·창작 0·내부 정합)하고, 통과 후에만 파일로 기록.

### 산출물

| 파일 | 내용 | 커밋 |
|---|---|---|
| `judging_<TS>.md` | 실행 시각별 리포트(이력 추적) | O |
| `judging_latest.md` | 최신 리포트 포인터(내용 동일) | O |
| `_evidence_<TS>.json` | 결정론적 증거 스냅샷(재생성 가능) | X (.gitignore) |

### 심사 기준의 출처 (source of truth)

- **1차 기준 = 킥오프 세션 '전사' 기반**: `docs/hackathon_essentials_0721.md` §1 + `docs/FEATURES.md` §5.
- 대회 공식 신청 페이지 원문은 아직 미확보(`docs/handoff.md` §4). 전사와 신청 페이지가
  다를 수 있는 지점은 리포트의 **"기준 불일치·미검증"** 섹션으로 분리 표기한다.
- 공식 원문(영상 전사 전문·신청 페이지 원문)을 확보하면 그걸 정본으로 삼아 워크플로우의
  `AXES`/`SOURCE_OF_TRUTH`(`.claude/workflows/judge-dept.js`)를 갱신한다.

### 증거수집기가 모으는 것 (`scripts/collect_evidence.py`)

git 상태 · 테스트 6종 pass/fail·케이스 수 · `red_team.py --report` 시도·차단·유출·오탐 ·
`artifacts/tx` 인벤토리(localnet vs devnet·cross_check·explorer 링크) ·
`artifacts/backtests` 종목별 전략 vs 벤치마크 · 디렉터리별 코드 라인 ·
402 Guard 구현 마커(check_demand·check_delivery·exact `!=`·Memo AT1·`allowed_asset`·HTTP402 유무)를
git grep 으로 대조(추적 파일만 — `secrets/`·`.venv` 자동 제외).

수동 실행(디버그용):
```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m scripts.collect_evidence
```

---

## 부서 2 — 버그 전담 부서 (`bug-dept`) ✅ 가동

앱 **전체 모듈**을 **4렌즈**(정확성·엣지케이스·에러처리·회귀/커버리지 공백)로 스캔해
**재현 절차 + 수정안**을 리포트로 제안한다. **읽기 전용** — 코드를 고치지 않는다(자동 수정은
승인 후 별도 작업). 기존 `scripts/test_*` · `scripts/red_team.py` 와 연계한다.

### 재실행 (온디맨드 — 서브에이전트처럼 경로로 호출)

```
Workflow({ scriptPath: ".claude/workflows/bug-dept.js" })
```

미래 세션에서 사용자가 **"버그 부서 돌려줘"** 라고 하면 이 워크플로우를 실행한다.

### 파이프라인 (다중 에이전트 — 오탐을 남기지 않기 위해 적대 검증 결합)

1. **Scan** — `scripts/collect_bugscan.py`(결정론적)를 실행해 버그표면을
   `docs/reports/_bugscan_<TS>.json` 스냅샷으로 덤프.
2. **Hunt** (팬아웃 4) — 모듈그룹 4개(G1 결제·가드 / G2 에이전트 / G3 시세·지표·공유 /
   G4 웹·설정)를 각 헌터가 **4렌즈 전부 + 증거 JSON + file:line** 으로 사냥.
3. **Verify** (건별) — 적대적 검증관이 각 후보를 회의적으로 재확인(file:line 실재·python -c 재현).
   기본값 real=false — 확인될 때만 확정. 반려 건은 오탐 방지 기록으로 남긴다.
4. **Report** — 종합 작성관이 심각도순 리포트 초안 작성 → **최종 자기검토관**이 6항목 체크
   (file:line 실재·재현 구체성·창작 0·심각도 정합·반려 누락·**읽기 전용 준수**) 후에만 기록.

### 산출물

| 파일 | 내용 | 커밋 |
|---|---|---|
| `bug_<TS>.md` | 실행 시각별 버그 리포트(이력 추적) | O |
| `bug_latest.md` | 최신 리포트 포인터(내용 동일) | O |
| `_bugscan_<TS>.json` | 결정론적 버그표면 스냅샷(재생성 가능) | X (.gitignore) |

### 버그표면 수집기가 모으는 것 (`scripts/collect_bugscan.py`)

git churn(최근 20커밋 파일별 변경 빈도) · 테스트 6종 pass/fail·OK수 · `red_team.py --report` KPI ·
**coverage_map**(모듈별 test 참조 + 미커버 목록) · **risk_markers**(bare/broad except·TODO·noqa·
runtime assert·payments 내 float, 각 file:line) · **module_index**(파일별 비공백·함수·클래스 수)를
git grep 으로 대조(추적 파일만 — `secrets/`·`.venv` 자동 제외). **마커는 '점검 지점'이지 버그 확정이
아니다** — 헌터가 열어 판정한다.

수동 실행(디버그용):
```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m scripts.collect_bugscan
```

---

## 부서 3 — 데이터 수집 부서 (`data-dept`) ✅ 가동

시세 입력 CSV(`data/market/*.csv`)가 **ReplayPriceFeed·백테스트·데모의 뿌리**이므로, 이
데이터가 **실제 소비 코드 경로(`market.price_feed.load_bars`)로 로드**되고 OHLC 정합·연속성·
이상치·워밍업 충분성 면에서 건전한지 **읽기 전용**으로 점검해 심사 재현성을 지킨다. 수집·수정
없음 — 관찰·권고만 낸다(자동 수집은 `--fetch` 미래 여지로만 남김).

### 재실행 (온디맨드 — 서브에이전트처럼 경로로 호출)

```
Workflow({ scriptPath: ".claude/workflows/data-dept.js" })
```

미래 세션에서 사용자가 **"데이터 부서 돌려줘"** 라고 하면 이 워크플로우를 실행한다.

### 파이프라인 (다중 에이전트 — 무해/결함 오판을 남기지 않기 위해 적대 검증 결합)

1. **Scan** — `scripts/collect_dataquality.py`(결정론적)를 실행해 품질표면을
   `docs/reports/_dataquality_<TS>.json` 스냅샷으로 덤프.
2. **Inspect** (팬아웃, 심볼별) — 각 인스펙터가 담당 CSV 를 **품질 7차원 전부 + 증거 JSON +
   실제 행(파일:행번호)** 으로 점검하고, 갭/이상치를 공휴일·실적변동(무해)과 진짜 결함으로 가른다.
3. **Verify** (건별) — 적대적 검증관이 각 후보를 회의적으로 재확인(좌표·수치 실재·무해/결함 판별).
   객관적 결함(로드실패·OHLC 위반·스키마·중복)만 이슈로 확정, 공휴일갭·실적변동은 무해로 반려.
4. **Report** — 종합 작성관이 리포트 초안 작성 → **최종 자기검토관**이 사실 자기검토 5항목 +
   **5축 자기평가(agent-self-evaluation)** 를 붙여 기록한다.

### 산출물

| 파일 | 내용 | 커밋 |
|---|---|---|
| `data_<TS>.md` | 실행 시각별 데이터 품질 리포트(이력 추적) | O |
| `data_latest.md` | 최신 리포트 포인터(내용 동일) | O |
| `_dataquality_<TS>.json` | 결정론적 품질표면 스냅샷(재생성 가능) | X (.gitignore) |

### 품질표면 수집기가 모으는 것 (`scripts/collect_dataquality.py`)

CSV별로 **소비 호환**(실제 `load_bars()` 로드 성공·bar수 일치) · **스키마·파싱**(헤더·날짜형식·
숫자변환) · **OHLC 정합**(high≥low 등·중복 날짜·정렬) · **연속성**(달력 갭>4일·주말 봉) ·
**이상치**(전일 대비 |변동|>20%·거래량0) · **충분성**(MA20/MA200 워밍업) · **신선도**(마지막 봉)를
모은다. 판정 로직은 **순수 함수**로 분리해 `scripts/test_dataquality.py`(28건)가 직접 검증한다.
**마커는 '점검 좌표'이지 결함 확정이 아니다** — 공휴일 갭·실적 실변동은 인스펙터가 무해로 가른다.

수동 실행(디버그용):
```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m scripts.collect_dataquality
```

---

## 새 부서 추가 절차 (템플릿)

필요한 결정론적 수집기(`scripts/collect_*.py`)를 만들고 → 워크플로우
(`.claude/workflows/<dept>.js`)를 `judge-dept.js`·`bug-dept.js`·`data-dept.js` 구조
(Evidence/Scan→Hunt/Review/Inspect→Verify→Synthesize+자기검토)로 얹고 → 이 README 에 한 절 추가한다.
