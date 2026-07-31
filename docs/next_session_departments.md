# 다음 대화 브리프 — 버그 부서 · 심사 부서 실행

> **이 문서 하나만 읽고 시작하면 된다.** 배경이 더 필요하면 `CLAUDE.md` 최상단 블록.
> 작성 2026-07-31. 기준 커밋은 아래 "직전 라운드가 바꾼 것" 표를 볼 것.

## 0. 착수 조건 (먼저 확인)

부서를 돌리기 전에 **아래 3개가 전부 ✅ 다 — 착수 조건이 충족됐다.** 사용자 지시가
*"버그부서·심사부서 돌리기 전에 완벽하게 검토해야 된다"* 였다.

| # | 항목 | 상태 |
|---|---|---|
| 1 | devnet USDC 재실증 (Circle 공식 민트) | ✅ `artifacts/tx/20260731_1508_solana-devnet_live_buy.json` |
| 2 | A-lite(온체인 예산 레일) 재검토 | ✅ 58/58 · `artifacts/tx/20260731_1438_solana-localnet_delegation.json` |
| 3 | **배포본 라이브(온체인) 세션 1건** | ✅ `20260731_160230_live` · 검증본 `artifacts/tx/20260731_1603_..._onchain_verify.json` |

```powershell
$h = Invoke-RestMethod "https://synapstock-766888967498.asia-northeast3.run.app/api/history/sessions"
$h.sessions | Group-Object mode | ForEach-Object { $_.Name + " : " + $_.Count + "건" }
```

### (참고) 3번을 다시 해야 할 때만 읽는다 — 이미 ✅ 다

절차·함정은 `CLAUDE.md` 의 "▶▶ 다음 대화" 3번에 전부 적혀 있다. 요약:
`/app?lab=1#token=<토큰>` 으로 **실제 브라우저(Chrome)** 에서 열고, 실행 모드만
`라이브` 로 바꿔 시작한다. **Claude 내부 브라우저는 `alert()` 을 자동 무시**해서
401 사유가 안 보이므로 쓰지 않는다.

### ✅ 임시 env 원복 완료 — 리비전 `synapstock-00027-jgk` (다시 열었을 때만 필요)

**이미 되돌렸다** — 현행 `synapstock-00027-jgk` 는 `ALLOW_LIVE_FROM_WEB=0` · 예산 100 · 건별 50 이고
라이브 시작이 401 로 잠긴 것을 실측했다. 아래는 **다시 열었을 때 되돌리는** 명령이다(빌드 없이 1분):

```powershell
gcloud run services update synapstock --region asia-northeast3 --project synapstock --update-env-vars "ALLOW_LIVE_FROM_WEB=0,BUDGET_USDC=100,PER_TRADE_MAX_USDC=50"
```

되돌린 뒤 확인: `/api/state` 의 `budget.total_usdc` 가 `100`, `rules.spend_per_trade` 가 `30.00`.
⚠ 세션이 도는 중에는 `services update` 를 하지 않는다(런북 §6). `engine.status` 가 `idle` 인지 먼저 본다.

## 1. 부서 실행

두 부서 모두 **읽기 전용**이다(코드 자동 수정 없음). 각각 새 대화에서 돌리는 것을 권한다 —
토큰을 많이 쓴다.

```
Workflow({scriptPath: ".claude/workflows/bug-dept.js"})
```
```
Workflow({scriptPath: ".claude/workflows/judge-dept.js"})
```

리포트는 `docs/reports/bug_latest.md` · `docs/reports/judging_latest.md` 로 덮어쓰인다.
부서 설명은 `docs/reports/README.md`.

## 2. 부서에 꼭 알려야 할 최근 변경 (이걸 모르면 오탐이 난다)

직전 라운드(2026-07-31)가 바꾼 것. **전부 커밋·푸시됨.**

| 커밋 | 무엇 |
|---|---|
| `5a33026` | 프런트 전달본 8묶음 이식(맨 위로·토스트·플로팅 버튼·종·브리핑·시세 배지) |
| `70b1749` | `run_demo --spend` 옵션 신설 (기본 30 불변) |
| `df7dd20` | A-lite 재검토 증빙 (코드 무변경) |
| `d41746d` | `.env.*` gitignore (백업에 API 키가 든 채 저장소에 남던 것) |
| `bb07954` | **1회 매수를 절대 상수 30 → 예산 대비 비율 `SPEND_PCT = 30`** |
| `fb359c1` | devnet Circle 공식 USDC 재실증 증빙 |

**특히 `bb07954` 를 부서가 오해하기 쉽다:**
- `DEFAULT_RULES` 에서 `spend_per_trade` 를 **일부러 뺐다**. 화면으로 나가는 값은
  `_rules_snapshot()` 이 **계산해서** 넣는다(고정 문자열을 내보내면 화면과 엔진이 갈라진다).
- 기본 예산 100 에서 30% = 30 이라 **동작은 종전과 동일**하다. 성능 변경이 아니다.
- 근거는 `docs/reports/strategy_validation.md` 부록(백테스트 144회). 그 문서는
  **"30% 가 최적이라고 주장하지 않는다"** 고 명시한다 — 부서가 그 이상으로 인용하면 과장이다.

## 3. 부서가 건드리면 안 되는 것 (의도된 동작)

- **라이브 모드에서 종목 체크박스가 전부 잠기고 해제되는 것** — `app.js:1855-1861` 의도.
  서버가 라이브에서 종목을 `CFG.stock_symbol` 로 강제한다. 결함 아님.
- **`[data-slim-pause]` 가 `display:none` 인데 DOM 에 남아 있는 것** — 지우면 `app.js` 가
  null 참조로 죽는다. 시안 지시로 화면에서만 뺐다.
- **`[data-btn-briefing]`(오늘 요약)이 `display:none`** — 같은 이유.
- **`[data-fab-menu]` 가 아무 동작도 없는 것** — 누를 곳 미정이라 배선하지 않았다(전달서 지시).
- **A-lite(`payments/delegation.py`)가 제품에 배선돼 있지 않은 것** — 의도.
  `wired_into_product: false` 가 그 경계다. C4(엔진 배선)는 8/3 전 금지.

## 4. 아직 열려 있는 것 (부서가 재확인하면 좋은 것)

- **`.claude/settings.json` 에 정체불명 변경**이 떠 있다 — `ANTHROPIC_BASE_URL` 을
  `http://localhost:20128` 로 돌리고 모델을 `auto/best-free` 로 바꾸는 env 블록이 붙었고,
  **같은 객체가 파일 끝에 한 번 더 붙어 JSON 문법이 깨져 있다.** 커밋하지 않았다.
  추적 파일이라 그대로 커밋하면 공개 저장소에 올라간다. **사용자 판단 대기.**
- 잔여 버그: 낮음 14 · 정보 1 (`docs/reports/bug_latest.md`). 중간 이상은 전부 닫혔다.
- 제출물 3종(소개서 PDF · 3분 영상 · 구글폼)은 **디자인 최종본 수령 후** 착수.
  ⚠ 마감 **2026-08-03 23:59 KST** 는 그대로다.
