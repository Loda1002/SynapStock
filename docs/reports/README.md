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

## 부서 2·3 (예정) — 같은 패턴으로 추가

다른 부서도 **워크플로우 형태 + 결정론적 수집기 + 자기검토 단계**의 동일 패턴을 따른다.
`judge-dept.js` 가 템플릿이다.

- **버그 전담 부서** (`bug-dept`, 예정) — 회귀·엣지케이스·재현 절차 스캔 → 재현 절차 + 수정안
  제안(자동 수정은 승인 후). 기존 `scripts/test_*` · `scripts/red_team.py` 와 연계.
- **데이터 수집 부서** (`data-dept`, 예정) — 시세/데이터 수집·품질 점검(`scripts/fetch_market_data.py`
  확장 · CSV 검증 등).

새 부서 추가 절차: 필요한 결정론적 수집기(`scripts/collect_*.py`)를 만들고 → 워크플로우
(`.claude/workflows/<dept>.js`)를 `judge-dept.js` 구조(Evidence→Review/Scan→Verify→Synthesize
+자기검토)로 얹고 → 이 README 에 한 줄 추가한다.
