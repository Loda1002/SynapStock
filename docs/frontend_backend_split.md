# 프론트/백엔드 분리 개발 & 병합 가이드

> 작성 2026-07-25. **백엔드 = 저장소 소유자(사용자), 프론트 = 다른 작업자(데스크탑 Claude Code)**.
> 두 사람이 각자 작업한 뒤 충돌 없이 병합하기 위한 **파일 경계·통합 계약·병합 절차·페이지 설계**를 담는다.
> 이 문서 하나가 두 작업자의 "계약서"다 — 여기 적힌 경계와 스키마를 지키면 병합 충돌이 사실상 0이 된다.

---

## 0. 한눈에 (핵심 3줄)

1. **프론트는 `web/static/**` 만 건드린다. 백엔드는 그 밖의 모든 것.** 파일 집합이 겹치지 않아 git 3-way 병합이 자동으로 되고 충돌이 없다.
2. **서버 라우팅·상태 스키마·API 는 백엔드 단독 소유다.** 프론트가 새 라우트가 필요하면 코드로 고치지 말고 백엔드에 요청한다(§2.1).
3. **백엔드가 프론트에 보장하는 계약은 `GET /api/state` 의 JSON 모양(§2.2)·API 목록(§2.3)·SSE(§2.4)** 뿐이다. 백엔드는 이 필드 이름을 말없이 바꾸지 않는다.

---

## 1. 파일 소유 경계

| 영역 | 소유자 | 경로 | 비고 |
|---|---|---|---|
| **프론트엔드** | 프론트 작업자 | `web/static/**` (`index.html`·`login.html`·`css/theme.css`·`css/skeleton.css`·`js/app.js` + 새로 만들 `landing.html`) | HTML·CSS·JS 전부. **이 시점부터 백엔드는 여기를 편집하지 않는다.** |
| **백엔드** | 사용자 | `web/*.py`(`server.py`·`engine.py`·`events.py`·`store.py`·`briefing.py`)·`agents/`·`payments/`·`market/`·`shared/`·`config.py`·`scripts/`·`docs/`·테스트 | 엔진·결제·가드·시세·라우팅·스키마. |
| **공유(백엔드 단독 편집)** | 사용자 | `web/server.py` | 라우트가 프론트 페이지를 가리키지만, **편집은 백엔드만** 한다. 프론트는 라우트를 §2.1 표로 받고 파일만 채운다. |

> **⚠ 전환 주의**: 지금까지는 백엔드(사용자)가 `index.html`·`app.js` 도 직접 고쳐 왔다(멀티종목 UI 등). **이 문서 시점부터 그 습관을 멈춘다.** 프론트에 넘긴 뒤 백엔드가 정적 파일을 고치면 병합 충돌의 유일한 원인이 된다.

---

## 2. 통합 계약 (백엔드 → 프론트 보장) — **이걸 바꾸면 프론트가 깨진다**

### 2.1 라우트 표 (URL → 서빙 파일)

| URL | 서빙 파일 | 현재 | 목표(페이지 분리 후) |
|---|---|---|---|
| `GET /` | `static/index.html` | 대시보드 | **`static/landing.html`(랜딩)** 으로 변경 예정 |
| `GET /app` | — | 없음 | **`static/index.html`(대시보드)** 신설 예정 |
| `GET /login` | `static/login.html` | 로그인 자리표시 | 로그인+**지갑 등록**으로 확장 |
| `GET /static/*` | `static/**` 전부 | 정적 서빙 | 그대로 |

- 라우트 변경(§5)은 **백엔드가 `server.py` 에서** 적용한다. 프론트는 그 전에도 파일을 **`/static/landing.html` 로 직접 열어** 개발할 수 있다(StaticFiles 가 모든 정적 파일을 서빙하므로).
- 프론트가 새 페이지/라우트가 필요하면 이 표에 한 줄 추가를 백엔드에 요청한다.

### 2.2 상태 스키마 — `GET /api/state` (= SSE `state` 이벤트 payload)

프론트의 모든 렌더는 이 JSON을 읽는다. **필드 이름은 계약이다.** (출처: `web/engine.py` `state_snapshot()` L1500-1581)

```jsonc
{
  "engine":   { "status": "idle|running|stopped", "mode": "dry|live", "network": "localnet|devnet",
                "tick": 0, "tick_interval_sec": 0.3, "started_at": "...", "brain": "rule|gemini",
                "session_id": "...", "symbols": ["AAPL", ...] },
  "persistence": { "enabled": false, "backend": "...", "detail": "...", "last_error": null },
  "trading_enabled": true,
  "pause_info": { ... },                    // 일시정지 주체·사유
  "symbol":  "AAPL",                         // 포커스(대표) 종목
  "symbols": ["AAPL", "TSLA"],               // 세션 종목 목록(멀티=N개)
  "replay_available": true,
  "price":   { /* 포커스 종목 시세 블록 */, "feed": { /* 피드 정보 */ } },
  "per_symbol": {                            // 멀티 종목: 종목별 시세·포지션·평가·피드
    "AAPL": { "price": {...}, "position": {"quantity":"0","avg_price_usdc":"0"},
              "valuation": {...}, "feed": {...} }, ...
  },
  "position": { "quantity": "0", "avg_price_usdc": "0" },   // 포커스 종목 포지션
  "budget":  { "total_usdc": "100", "spent_usdc": "0", "remaining_usdc": "100",
               "per_trade_max_usdc": "10", "all_in": false },   // all_in=true 는 추세추종(올인)
  "pnl":     { "realized_usdc": "0", "return_pct": "0",
               "cum_buy_usdc": "0", "basis": "cum-buy|initial-capital" },
  "valuation": { /* 미실현 평가손익·총자산 (전 종목 합산) */ },
  "fees":    { "fee_bps": 30, "cum_fee_usdc": "0" },
  "rules":   { /* 기본 매매 규칙 */ },
  "strategy":{ "type": "condition|dca|trend", ... },
  "last_briefing": { ... },
  "counts":  { "trades": 0, "decisions": 0 },

  // ★ 첫 화면 KPI — 402 Guard 서사의 핵심. 이미 방출 중, 프론트는 렌더만 하면 된다.
  "guard":   { "attempts": 0,          // 지출 시도 횟수(매수 + 가드 차단)
               "blocked": 0,           // 402 Guard 가 막은 건수
               "ap2_rejected": 0,      // AP2 한도로 거부된 건수
               "leak_usdc": "0.00" },  // 엉뚱한 데로 샌 금액 = 항상 0.00 이 목표

  "wallets": { "user": "...", "trading": "...", "broker": "..." },   // pubkey 문자열
  "balances": { /* 최근 온체인/장부 잔액 스냅샷 */ }
}
```

> 금액·수량은 **문자열**(Decimal 정밀 보존)로 온다. 프론트에서 표시할 때만 숫자로 파싱한다.

### 2.3 조회·컨트롤 API

| 메서드·경로 | 용도 | 인증 |
|---|---|---|
| `GET /api/state` | 현재 상태 스냅샷(§2.2) | 열림 |
| `GET /api/trades` | 거래 내역 `{trades:[...]}` | 열림 |
| `GET /api/decisions` | 판단 타임라인 `{decisions:[...]}` | 열림 |
| `GET /api/history/sessions?limit=` | 지난 세션 목록(Firestore) | 열림 |
| `GET /api/history/sessions/{id}` | 세션 상세 | 열림 |
| `GET /api/history/trades?limit=` | 세션 경계 넘는 체결 이력 | 열림 |
| `GET /api/history/briefings?limit=` | 브리핑 이력 | 열림 |
| `POST /api/engine/start` | 세션 시작(body: mode·strategy·feed·tick_interval_sec) | **조작** |
| `POST /api/engine/stop` | 세션 정지 | **조작** |
| `POST /api/trading/pause` · `/resume` | 매매 일시정지·재개(body: actor) | **조작** |
| `POST /api/mandate` | 한도 변경(body: budget_total_usdc·per_trade_max_usdc·actor) | **조작** |
| `POST /api/briefing` | 수동 '오늘 요약' | **조작** |

- **조작 API 인증**: `CONTROL_TOKEN` 환경변수가 설정돼 있으면 `X-Control-Token` 헤더를 요구한다(배포 시). **로컬 개발은 미설정 = 무인증**이라 프론트가 헤더 없이 그대로 호출하면 된다.
- 요청 본문은 **선언 안 된 필드를 422로 거부**한다(`StrictBody`, `extra=forbid`). 오타·제거된 필드를 조용히 삼키지 않으니, 프론트는 `server.py` 의 `*Body` 클래스 필드명을 정확히 따른다.

### 2.4 SSE — `GET /api/events`

- 실시간 이벤트 스트림(`text/event-stream`). 상태 변화·거래·판단·알림·에러가 흘러온다.
- `Last-Event-ID` 헤더(또는 `?since=`)로 새로고침 후 히스토리 복원. 15초 keepalive 하트비트.
- 프론트는 `EventSource("/api/events")` 로 구독하고, 각 이벤트 타입별로 화면을 갱신한다(현 `app.js` 참고).

---

## 3. 프론트 개발자 셋업 (백엔드 없이도 UI 개발 가능)

프론트는 **백엔드를 직접 실행해 dry(모의) 세션**을 돌리면, 실제 `state_snapshot` 을 눈으로 보며 개발할 수 있다.

```bash
git clone <repo>            # GitHub 에서 받기
cd solana-agent
python -m venv .venv        # Python 3.10~3.11
.venv\Scripts\pip install -r requirements.txt   # (Windows PowerShell)
.venv\Scripts\python -m web.server              # http://127.0.0.1:8000
```

- **dry 모드는 지갑 시크릿(`.env`)이 필요 없다** — 엔진이 모의 지갑을 만든다. `GEMINI_API_KEY` 가 없으면 규칙 폴백으로 돈다. 즉 **프론트 작업자는 비밀키 없이** UI 를 개발할 수 있다.
- 브라우저에서 대시보드를 열고 "세션 시작(dry)" 하면 시세가 흐르고 `guard`·`per_symbol` 등 실제 상태가 채워진다.
- **랜딩 페이지**는 서버 없이 `web/static/landing.html` 파일을 브라우저로 직접 열거나 `/static/landing.html` 로 열어 개발한다.
- **포트 충돌**(`Port 8040 is in use…`): 데스크탑 Claude Code 의 `.claude/launch.json` 에서 `autoPort: true` 로 두면 빈 포트를 자동 할당한다. 특정 포트가 꼭 필요한 게 아니면 `--port` 하드코딩을 빼고 `PORT` 환경변수/자동할당을 쓴다.

---

## 4. 병합 워크플로우

### 브랜치 전략 (권장: 단순)

- 프론트 작업자는 `frontend` 브랜치를 `main` 에서 따서 작업하고, 끝나면 push → 백엔드(사용자)가 `main` 으로 병합.
- 백엔드(사용자)는 `main`(또는 `backend` 브랜치)에서 작업.
- **파일 집합이 겹치지 않으므로**(프론트=`web/static/**`, 백엔드=나머지) 3-way 병합이 자동으로 되고 **충돌이 없다.** 유일한 잠재 충돌 파일 `web/server.py` 는 백엔드 단독 편집이라 안전.

### gitignore (이미 반영됨)

- `node_modules/`·`package.json`·`package-lock.json` 는 **stray 파일**이라 커밋 대상이 아니다(이 프로젝트는 순수 Python). `.gitignore` 에 추가해 두었으니, 프론트가 clone 후 실수로 2,700여 개 파일을 커밋하는 사고를 막는다.
- `.env`·`secrets/`·`.venv/`·`.claude/settings.local.json` 은 원래 gitignore 됨 — 프론트 작업자의 로컬 설정·비밀키는 커밋되지 않는다.

### 병합 순서 (권장)

1. 병합 전 각자 `main` 최신을 rebase/merge 로 반영.
2. 프론트 PR → 백엔드가 `web/static/**` 변경만 있는지 확인 후 병합.
3. 병합 직후 dry 세션 1회로 스모크(§3) — 콘솔 오류 0 확인.

---

## 5. 페이지 분리 설계 (사용자 요청 7~9) — 프론트가 만들 것

검색 → 링크 클릭 → **랜딩** → 로그인·지갑 등록 → **대시보드(현 기능)** 의 3단 흐름.

> **시안 도착(2026-07-25)**: 대시보드 디자인 시안을 `docs/design/dashboard_mockup.png` 에 둔다.
> **컴포넌트 단위 상세 스펙·시안↔기존 카드 매핑·작업 순서는 [docs/frontend_kickoff.md](frontend_kickoff.md)** 로 분리했다(프론트 개발자가 읽는 문서). 아래는 아키텍처 개요만.
>
> **⚠ 메시징 재조정 결정(제품 포지셔닝 vs 시안)**: 시안은 옛 "Auto Trader — 돈을 넣어두면 AI가 알아서 사고 팝니다" 카피다. 이는 심사 리포트가 탈락 위험으로 지목한 트레이딩 봇 프레이밍이므로, **시안의 레이아웃·색·컴포넌트는 채택하되 브랜딩·카피는 402 Guard 로 치환하고 가드 KPI 카드(유출 0.00)를 첫 화면 최상단에 추가한다.** 치환표는 kickoff §3. (트레이딩 결과 UI 는 유지 — 없애는 게 아니라 가드 서사를 얹는다.)

### 5.1 랜딩 `landing.html` (신규, `/`)

- **상단 스티키 바**: 브랜드/문구 + `시작하기` 버튼(→ `/login`). 스크롤해도 상단 고정.
- **메인(히어로) 섹션**: 실제 대시보드를 **흐릿하게(blur) 데모처럼** 보여준다. 방법 택1 —
  (a) 대시보드 스크린샷 이미지에 CSS `filter: blur()`, (b) `<iframe src="/app">` 위에 반투명 오버레이. **간단·안전한 건 (a) 스크린샷.**
- **하단(About) 섹션**: 스크롤 내리면 나오는 소개 — 402 Guard 가 무엇이고 왜 필요한지(§ 카피는 `docs/differentiation.md`·`docs/submission.md` 에서 가져온다).

### 5.2 로그인·지갑 등록 `login.html` (`/login`)

- 현재 로그인/회원가입 **자리표시 껍데기**가 있다(실 인증 없음, 자격증명 전송 안 함). 여기에 **지갑 등록 단계**를 얹는다.
- 지갑 등록 완료 후 `/app`(대시보드)로 이동. **실제 인증 백엔드는 제출 후 로드맵**이므로, 지금은 UI 흐름(등록 → 대시보드 진입)만 만든다.

### 5.3 대시보드 `index.html` (`/app`)

- **현재 기능 전부** 여기 그대로. 시안(디자인)의 배치·스킨을 입힌다.
- ⚠ **시안은 "멀티 종목 엔진 구현 이전" 기준**이라 **멀티 종목 UI(종목 체크박스·포커스 드롭다운·종목별 요약표)와 가드 KPI 카드가 시안에 없다.** → 시안을 통째로 재구현하지 말고, **이미 동작하는 마크업을 유지한 채 시안의 레이아웃/색/간격을 입히고, 없는 요소(멀티종목·가드카드)는 계약(§2.2)에 맞춰 추가**한다.

### 5.4 라우트 변경 스니펫 (백엔드가 §5 준비되면 적용)

`web/server.py` 의 `index()`/`login()` 부근을 이렇게 바꾼다:

```python
@app.get("/")
async def landing() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "landing.html"))

@app.get("/app")
async def dashboard() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
```

> **적용 시점**: 프론트가 `landing.html` 을 실제로 채운 뒤. 그 전에 바꾸면 백엔드 개발 중 `/` 가 빈 페이지가 된다. 프론트는 그 전에도 `/static/landing.html` 로 개발 가능하므로 서두를 필요 없다. 또한 `login.html` 의 "대시보드로" 링크(`href="/"`)는 이 변경 후 `href="/app"` 으로 바꾼다(프론트 담당).

---

## 6. 프론트 첫 작업 (우선순위 — 심사 부서 리포트 반영)

심사 부서(`docs/reports/judging_latest.md`)가 지목한 **가장 큰 갭 = 402 Guard 서사가 첫 화면에 안 보임.** 백엔드 데이터는 이미 준비돼 프론트 배선만 하면 되는 최고효율 작업이다.

1. **[최우선] 가드 KPI 카드** — `index.html` 에 카드 추가 + `app.js` 가 `state.guard`(`attempts`·`blocked`·`ap2_rejected`·`leak_usdc`)를 렌더, `DEFAULT_LAYOUT` 최상단 배치. **"유출 0.00 USDC" 를 첫 화면에.**
2. **[리브랜딩]** `index.html` 헤더/`<title>`/태그라인을 `AutoTrader Agent` → **`402 Guard — 에이전트 지출 승인 게이트`** 로(문구는 `login.html:22-23` 재사용). 첫 스크롤에 수익률·평가손익보다 **지출 통제(시도·차단·유출)** 가 먼저 보이게 `DEFAULT_LAYOUT` 재배열.
3. **랜딩 페이지**(§5.1) 구축.
4. **로그인+지갑 등록**(§5.2) 흐름.

---

## 7. 구조 정합성 점검 결과 (사용자 요청 4)

| 점검 항목 | 상태 | 조치 |
|---|---|---|
| 프론트/백 파일 집합 분리 | ✅ 분리됨(`web/static/**` vs 나머지) | 병합 충돌 위험 없음 |
| 유일 공유 파일 `server.py` | ✅ 백엔드 단독 소유로 해소 | 프론트는 라우트를 §2.1 로 받음 |
| 상태 스키마 계약 | ✅ §2.2 로 동결·문서화 | 백엔드는 필드명 무단 변경 금지 |
| `node_modules`·`package.json` stray | ✅ 미추적 → gitignore 반영 | 실수 커밋 방지 |
| `.claude/worktrees` 잔재 | ✅ 미추적 확인 | 커밋 안 됨, 무해 |
| 프론트 단독 실행 가능성 | ✅ dry 모드(시크릿 불필요) | §3 셋업 절차 |

**결론: 구조적으로 병렬 작업·병합에 문제 없음.** 지켜야 할 규칙은 단 두 가지 — ①프론트는 `web/static/**` 만, ②라우팅·스키마는 백엔드 단독.
