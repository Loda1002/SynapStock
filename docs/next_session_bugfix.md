# 다음 대화 브리프 — 버그 부서 확정 건 수정

> **이 문서 하나만 읽고 시작하면 된다.** 근거 리포트는 `docs/reports/bug_latest.md`.
> 작성 2026-07-31. 기준 커밋은 이 문서를 담은 커밋. 마감 **2026-08-03 23:59 KST**.

## 0. 먼저 알아야 할 것 — 리포트를 그대로 믿으면 안 된다

버그 부서(2026-07-31, 에이전트 27/27·오류 0)가 확정 20건을 냈다. 그중 **2건은 리포트 서술이
틀렸고, 내가 소스로 대조해 정정했다.** 아래 수정 명세는 정정본이다.

| 건 | 리포트가 말한 것 | 실제 (소스 대조로 확인) |
|---|---|---|
| **M5** | 수정 위치 `payments/x402_http.py:174` | **틀렸다.** `:174` 는 클라이언트측 `parse_payment_required` 이고 `:176` 이 `ValueError` 를 이미 잡는다. 진짜 위치는 **`x402_http.py:148`**(`except (KeyError, TypeError)` — `ValueError` 누락) + 뿌리는 `shared/a2a_messages.py:74` |
| **M7** | 중간 — "화면 등락률 부호가 뒤집힌다" | **심사 경로에는 영향 없다.** `app.js:1026` 이 `changeBasis === "prev-close" ? prevClose : sessionOpen` 이고 `engine.py:1870` 이 **재생 피드면 `prev-close`** 를 쓴다. 촬영·심사·라이브는 전부 실데이터 재생 → 부호 반전은 **개발용 합성 패턴 피드에서만**. 낮음으로 강등 |

**리포트 본문 상단에 전달 파이프라인 결함 고지가 있다** — 종합 단계 초안이 비어서 재현
스크립트로 재구성했다는 내용이다. 재구성 자체는 대체로 정확했지만(M1·M2·M3·M4·M6·M8 은
소스에서 기전 확인), 위 2건처럼 **인용 위치·심각도 근거가 틀린 사례가 실재한다.**
→ **이번 수정에서도 파일을 열어 확인한 뒤 손대는 것을 원칙으로 한다.**

## 1. 이번 대화에서 할 것 (사용자 승인 범위)

심각도가 아니라 **심사 노출 × 수정 안전성** 순서다. 위에서부터.

### A. 심사(8/3)에 실제로 노출되는 것 — 먼저 한다

#### A-1. M5 — 무인증 x402 엔드포인트가 형식 오류에 500(비JSON)을 준다

- **왜 1순위**: `POST /broker/orders` 는 **배포 URL의 무인증 공개 엔드포인트**이고 제출 영상이
  `curl -i` 로 찍는 축③ 표면이다. **심사위원이 직접 밟을 수 있는 유일한 건.**
- **위치(정정본)**: `payments/x402_http.py:137-150` `decode_payment_header`
  - `:148` 이 `except (KeyError, TypeError) as e:` — **`ValueError` 가 없다.**
  - 새는 경로: `:147 PaymentPayload.from_dict(data)` → `shared/a2a_messages.py:74`
    `x402_version=int(d.get("x402Version", X402_VERSION))` → `int('abc')` = `ValueError`
  - → `web/broker_service.py:164` 의 `except X402ProtocolError` 가 못 잡아 **500**.
- **수정 방향**: `:148` 에 `ValueError` 를 추가한다. 함께 L7(타입 검증 공백)도 닫는다 —
  `serializedTransaction=123` · `network={}` · `x402Version=1.5` 가 지금 **디코더를 통과한다**
  (`a2a_messages.py:71-74` 가 형변환 없이 대입). `X402ProtocolError` 로 감싸면 핸들러가
  이미 400 으로 바꾼다(`broker_service.py:165` = `invalid_payment_header`).
- **정상 동작 대조군(이미 400 을 준다 — 이 형태로 맞추면 된다)**: payload 키 누락 · raw base64 아님.
- **회귀**: `scripts/test_http402.py`(53건).

#### A-2. M2 — `settle_sale` 이 지급 실패에도 `delivered_amount` 에 전액을 싣는다

- **왜**: 증빙 아카이브(`artifacts/tx/`)와 엔진 회계에 **일어나지 않은 인도**가 기록된다.
  저장소가 public 이고 축④가 이력 기반 심사라 파일을 열면 두 필드가 서로를 반박한다.
- **위치**: `agents/broker_agent.py:204` `delivered_amount=payout_amount,`
- **이미 있는 정답이 같은 파일에 있다** — 매수 레그 `:288-294`:
  ```python
  # 보내지도 않은 수량을 실어 보내지 않는다 — 미배송이면 0
  delivered_amount=(to_base_units(quantity, self.stock_decimals)
                    if status == "settled" else 0),
  ...
  reason=("" if status != "partial"
          else "대금은 수령됐으나 주식 전달 tx 가 확정되지 않았습니다 (미배송)"),
  ```
- **수정 방향**: `:204` 를 `delivered_amount=(payout_amount if status == "settled" else 0)` 로.
  매도 레그에는 **`reason` 인자가 아예 없다**(`:199-207` 확인) — 대칭으로 추가한다.
  문안은 방향을 뒤집어서: *"주식은 수령했으나 USDC 지급 tx 가 확정되지 않았습니다 (대금 미지급)"*.
- **재현 기대값(리포트 실측)**:
  `지급 실패 → status=partial · delivered_amount=19940000 · reason=''` → 고치면 `0` + 사유.
- **⚠ 먼저 볼 것**: `scripts/test_settlement.py` 가 partial 매도에서 `delivered_amount` 를
  어떻게 단언하는지. 기대값 갱신이 필요할 수 있다.

#### A-3. M3 — `get_token_balance_ui` 가 RPC 실패를 `'0'` 으로 삼킨다

- **왜**: 이 함수가 **증빙 아카이브의 `balances_before`/`balances_after` 와 교차검증 입력**이다
  (`run_demo.py:99-100 snapshot_balances`). "유출 0.00"을 파일로 증명하는 경로가 여기다.
- **위치**: `payments/x402_solana.py:321-328` (`except Exception: return "0"`)
- **형제 함수가 이미 고쳐져 있다** — `get_token_balance_base:331-344` 는 BUG-01 수정으로
  `_is_account_not_found(e)` 일 때만 0 을 주고 나머지는 `raise` 하며, **독스트링 `:334-336` 이
  그 이유를 설명한다.** UI 쪽만 옛 방식으로 남았다.
- **수정 방향**: `base` 와 같은 형태로 맞춘다.
- **⚠ 이 건은 한 줄이 아니다 — 호출부 3곳을 함께 봐야 한다**:
  - `run_demo.py:99-100` — 전파되면 스냅샷이 실패한다. **'모름'을 '0' 으로 적지 않는 것이
    목적**이므로, 잡아서 `null`/`"unknown"` 으로 기록하고 교차검증을 '판정 불가'로 두는 편이
    맞다(M4 와 같은 계열: "읽어 봤는데 0" 과 "읽지도 못했다"는 다른 사실이다 — BUG-11 때
    확립한 원칙과 같다).
  - `scripts/demo_delegation.py:236,290` — A-lite 증빙.
  - `web/server.py:262` — 지갑 배지. **호출부에서 감싸면 화면 회귀 0.**

#### A-4. L1 — 아카이브 파일명이 분 단위라 같은 분의 두 세션이 덮어쓴다

- **위치**: `web/engine.py:1679` (`"%Y%m%d_%H%M"`, 초 없음) · `:1725` (`open(path,"w")`, 존재 검사 없음)
- **대조**: `:662` 의 `session_id` 는 `"%Y%m%d_%H%M%S"` 로 초를 포함한다.
- **재현(리포트 실측)**: 41초 간격 두 세션 → 파일 1개만 남고 세션 #1 의 `payment_tx` 가 사라진다.
- **왜 지금**: 잃는 것이 **온체인 tx 증빙**이고, **재촬영 중에 충분히 가능**하다.
- **수정**: `:1679` 에 초를 넣는 한 줄.
- **⚠ BUG-21(보류 결정)과 다른 건이다** — 그건 `session_id` 형식이라 화면 라벨·아카이브
  파일명·Firestore 키가 함께 흔들려 위험>가치로 보류됐다. 이건 **파일명뿐**이라 훨씬 싸다.
  다만 손대기 전에 그 파일명을 파싱하는 곳이 없는지 한 번 확인할 것.

### B. 값싸고 무해한 것 — 시간이 남으면

#### B-1. M8 — `update_limits` 의 0 가드가 옛 지출액을 남긴다 (`bb07954` 회귀)

- **위치**: `web/engine.py:869-877`. `if new_spend > 0:` 이 대입을 건너뛰어 에이전트가 옛 값을 유지.
- **`git show bb07954` 로 확인함 — 이 블록은 SPEND_PCT 전환 때 신설됐다.** 즉 신규 회귀다.
- **결과 둘**: ①화면(`_rules_snapshot` 은 새 예산으로 계산)과 엔진이 갈라진다 — CLAUDE.md 가
  "이 저장소가 반복해서 밟은 부류"로 적어 둔 바로 그것 ②**가드 KPI 오염** — 공격이 없는데
  `guard_block_count` 가 오른다("시도 N건 중 M건 차단"이 대표 지표다).
- **수정 방향**: `start()` 가 이미 가진 거부 검사(`:541-543`, 문안까지 있다 —
  *"예산 X USDC 의 30% 가 0 이 됩니다 — 예산을 올리세요."*)를 `update_limits` 에도 적용해
  **아예 받지 않는다.** 부분 적용을 남기지 않는 것이 요점.
- **도달성은 낮다** — 예산을 0.017 USDC 미만으로 내려야 한다. 그래서 A 보다 뒤다.

#### B-2. L11 — `brain='rule'` 세션인데 브리핑이 Gemini 클라이언트를 만든다

- **위치**: `web/briefing.py:62` (`if not CFG.gemini_api_key:`) — `generate_briefing_text` 소스에
  `brain` 문자열이 **0건**이다(재현으로 확인). 키만 있으면 호출한다.
- **왜**: 화면이 **"규칙 기반 (사용자 지정 — Gemini 미사용)"이라 적어 놓고** 호출한다.
  무료 티어 일일 쿼터가 이 프로젝트의 반복 병목이었고 축② 증빙이 거기 걸려 있다.
- 크래시는 없다(폴백 정상). 라벨과 실제를 맞추는 일이다.

### C. 조건부 — 판단이 필요한 것

#### C-1. M6 — `restore_from_store` 가 상한·불변식을 우회한다

- **위치**: `web/engine.py:338-339`. Firestore 값을 **상한 검사 없이** 대입한다
  (`except (KeyError, InvalidOperation): pass` 뿐).
- 같은 값을 `update_limits` 로 넣으면 정상 거부된다(`:794-796` + `MAX_BUDGET_USDC` 검사).
- **재현(리포트 실측)**: 복원 후 `budget=999999` 로 **실제 mandate 가 서명된다** ·
  `per_trade > budget` 도 통과 · `Infinity` 면 `start()` 가 `InvalidOperation` 으로 크래시.
- **도달 경로**: 배포가 `MAX_BUDGET_USDC` 를 10000 → **1000 으로 낮췄다.** 상한이 높던 시절에
  저장된 defaults 문서가 남아 있으면 지금 부팅 시 현재 상한을 넘겨 복원된다.
- **⚠ 현재는 잠복이다** — 배포 예산이 100 이라 그런 문서가 실제로 있는지 미확인.
  **손대기 전에 Firestore `defaults` 문서의 실제 값을 먼저 확인할 것.** 값이 정상이면
  우선순위가 더 내려간다.
- **수정 방향**: 복원 직후 `update_limits` 와 같은 검사를 태우고 초과분은 상한으로 클램프 +
  로그. `is_finite()` 도 함께.

#### C-2. BUG-20 — 07-27 잔여 중 반드시 함께 고칠 것

- **BUG-20** — `update_limits` 가 새 authorizer 에 `spent_usdc` 만 이월하고 `_reservations` 는
  안 옮긴다(`web/engine.py:855-863` 확인 — `_reservations` 복사가 없다). 그 뒤 `_buy_cycle` 의
  finally 가 `release(order_id)` 를 불러도 새 auth 엔 그 예약이 없어 **실패한 결제의 예산이
  세션 끝까지 묶인다.** BUG-03 과 같은 계열(한도가 조용히 사라진다)이고 **B-1 과 같은 함수라
  맥락이 겹친다** — B-1 을 할 때 함께 보는 것이 싸다. 수정은 1~2줄
  (`new_auth._reservations = dict(old._reservations)`, 추세 멀티는 종목별).

### D. 07-27 잔여 건 승계 — **목록은 승계하되 픽스 범위는 넓히지 않는다**

**새 리포트는 07-27 잔여 건을 승계하지 않았다. "확정 20건"이 전부가 아니다.**
그렇다고 **전부 고치는 것이 더 안전하지는 않다** — 마감 3일 전에 변경 표면만 넓어진다.
아래 분류는 2026-07-30 에 이미 한 차례 검토한 결과를 그대로 잇는다(CLAUDE.md 의 "잔여 '낮음'
12건 + '정보' 1건" 블록).

**승계해서 고친다 (전부 `is_finite()`·0 검사 한 줄 계열 — 결제 판정에 닿지 않는다)**

| 건 | 내용 | 비고 |
|---|---|---|
| **BUG-20** | 위 C-2 | B-1 과 같은 함수 |
| **BUG-15** | 가격 0 인 봉이 피벗이면 `_cluster_levels` 가 0으로 나눠 `ta_summary` 크래시 | 새 리포트 미승계 |
| **BUG-18** | `tick_interval_sec=NaN` 이 안전범위 클램프를 통과해 틱 루프 폭주 | 새 리포트 미승계 |
| **BUG-19** | 적립식 회당 금액이 NaN 이면 500 으로 새고 Infinity 면 세션이 시작됨 | 새 리포트 미승계 |
| ~~BUG-14~~ | Gemini `spend_usdc` NaN → 그 틱 판단 유실 | **새 리포트 L5 와 같은 건** — B 단계에서 함께 |

→ BUG-15·18·19 는 **한 커밋으로 묶는다.** 개별 가치는 낮지만 같은 계열이라 한 번에 싸다.
새 리포트의 L3(`load_bars` 가 NaN 행 통과)도 같은 계열이니 함께 보면 된다.

**승계하지 않는다 (이유 명시 — 다음 세션이 다시 꺼내지 말 것)**

| 건 | 승계 안 하는 이유 |
|---|---|
| **BUG-13** | 견적 총액이 요청 지출을 최대 0.01 초과. **⚠ 견적 산식을 바꾸면 소개서·문서에 인용된 백테스트 수치(`+77.55%` 등)가 흔들린다 — §2-1 에서 제외한 M1·M4 와 정확히 같은 위험 계열이다.** 승계하면 그 결정과 모순된다 |
| **BUG-21** | `session_id` 초 단위 충돌. 형식을 바꾸면 화면 라벨 파싱·아카이브 파일명·Firestore 문서 키가 함께 흔들린다. **위험 > 가치**로 이미 보류 결정. (A-4 의 L1 은 파일명뿐이라 **다른 건**이다) |
| **BUG-22** | 라이브 `start()` 동시 호출 중복 세션. 배포본은 `ALLOW_LIVE_FROM_WEB=0` + `CONTROL_TOKEN` + `max-instances 1` 이라 **도달 경로가 없다** |
| **BUG-23** | SSE 큐 포화 시 조용한 유실. 수정이 `app.js` 를 건드리는데 **디자인 최종본이 아직 안 왔다.** 가드 KPI 는 `fetchState` 로 복구되므로 실제 손실은 로그 줄뿐 |
| **BUG-16·17** | 재생 구간·CSV 오류 메시지. 둘 다 **요란하게 실패**하고 안내가 나간다 |
| **BUG-25** | 정보 — `to_dict/from_dict` 사문화. 런타임 영향 0 |

원문은 `docs/reports/bug_20260727_134231.md`, 상태 표는
`git show bf06b61:docs/reports/bug_latest.md` 에 있다.

## 2. 절대 건드리지 않는 것

### 2-1. 사용자가 명시적으로 제외한 2건 — 동작 변경이라 마감 3일 전에 할 일이 아니다

- **M4** (`payments/x402_solana.py:360-364`) — `ok=True` 초기화 + `except: pass` 로 확정 상태를
  못 읽으면 확정으로 보고(fail-open). **고치면 확정 실패가 partial/failed 로 늘어난다** →
  기존 devnet 증빙 재현과 테스트 기대값이 흔들린다.
- **M1** (`agents/broker_agent.py:81 sell_quote`) — 매도 대금 하한 없음. **고치면 매도 경로를
  끊는다** → 백테스트 수치(소개서·문서에 인용된 `+77.55%` 등)와 데모 수익률이 흔들린다.

> **이 판단의 근거**: 과거 리포트 제안 2건이 틀렸고 그대로 적용했으면 데모 수익률이 통째로
> 깨진 채 나갈 뻔했다(BUG-05 의 `confirmed` 조건이 드라이런 정상 매도까지 포지션에서 빼게
> 만드는 안이었다). M4·M1 은 정확히 같은 계열이다. **로드맵으로 넘긴다.**

### 2-2. 결함이 아닌 것 (부서가 오탐하지 않았지만, 다음 세션이 오해하지 않도록)

- **M7** — 위 §0. 개발용 합성 패턴 피드 한정. 심사 경로 영향 0. 고치려면 한 줄이지만 우선순위 없음.
- **라이브 모드에서 종목 체크박스가 전부 잠기고 해제되는 것** — `app.js:1864` 의도.
  서버가 라이브에서 종목을 `CFG.stock_symbol` 로 강제한다.
- **`[data-slim-pause]`·`[data-btn-briefing]` 이 `display:none` 인데 DOM 에 남아 있는 것** —
  지우면 `app.js` 가 null 참조로 죽는다.
- **`[data-fab-menu]` 가 아무 동작도 없는 것** — 누를 곳 미정이라 미배선(전달서 지시).
- **A-lite(`payments/delegation.py`)가 제품에 배선돼 있지 않은 것** — 의도.
  `wired_into_product: false` 가 그 경계다. **C4(엔진 배선)는 8/3 전 금지.**

## 3. 검증 게이트 (커밋 전마다)

```powershell
Get-ChildItem scripts/test_*.py | ForEach-Object { & ".venv\Scripts\python.exe" -m ("scripts." + $_.BaseName) *> $null; "{0,-30} rc={1}" -f $_.Name, $LASTEXITCODE }
& ".venv\Scripts\python.exe" -m scripts.red_team --report
```

- **테스트 23종 전부 rc=0** · `red_team` rc=0 · **유출 0.00 · 오탐 0**.
- ⚠ **`python scripts/test_xxx.py` 로 돌리면 6종이 `ModuleNotFoundError` 로 실패한다** —
  `-m scripts.xxx` 가 정본이고 **시스템 python 이 아니라 `.venv\Scripts\python.exe`** 를 쓴다.
- ⚠ **테스트 파일 수 23개를 유지한다** — 새 파일을 만들지 말고 기존 파일에 회귀를 넣는다.
  소개서·대본·README 의 "**단위 테스트 23종**" 표기가 걸려 있다(낭독 대사와 어긋나면 재촬영 사유).
- ⚠ **하드 검사 8종 카운트 불변** — `payments/guard.py` 의 `check_demand` 를 건드리지 않는다.
  위 수정 중 어느 것도 그 함수에 손대지 않는다.
- **음성 대조를 수행할 것** — 옛 코드로 되돌려 리포트의 값이 재현되는지 확인한 뒤 복원한다.
  이 저장소가 매 라운드 해 온 방식이고, 회귀 테스트가 실제로 그 결함을 잡는다는 증거다.
- **작업 사이클**: 기능(=버그) 하나마다 `구현 → 검증 → 커밋 → git push origin main`.
  **푸시는 상시 승인이다**(묻지 않는다).

## 4. Cloud Run 재배포

수정 대상이 전부 런타임 코드다(`payments/`·`agents/`·`web/`). 다만 배포본은
`ALLOW_LIVE_FROM_WEB=0` 이라 라이브 경로가 잠겨 있고, **M5 만 드라이런/무인증 경로에서
동작이 바뀐다**(400 응답). 심사 URL 이 500 을 주지 않게 하려면 **M5 수정 후 재배포 1회**가 필요하다.

런북 `docs/deploy_cloud_run.md` §4. ⚠ **`GEMINI_MODEL=gemini-flash-latest` 포함 필수**
(`--set-env-vars` 는 기존 env 를 전부 덮어쓴다). 프로젝트·서비스 모두 `synapstock`,
리전 `asia-northeast3`. ⚠ 세션이 도는 중에는 `services update` 를 하지 않는다(§6).

## 5. 이 라운드 이후 남는 것

**⚑ 사용자 결정(2026-07-31): 심사 부서는 이 수정 라운드가 끝난 뒤에 돌린다.**
순서를 바꾸지 말 것 — 지금 돌리면 곧 고칠 것을 근거로 축을 평가하게 되고, 수정 후 어차피
다시 돌려야 한다. **수정·검증·푸시가 끝나면 그때 새 대화에서:**

```
Workflow({scriptPath: ".claude/workflows/judge-dept.js"})
```

리포트는 `docs/reports/judging_latest.md` 로 덮어쓰인다. 착수 전에 이 문서와
`docs/reports/bug_latest.md` 를 함께 넘겨야 부서가 "이미 닫힌 것"을 결함으로 세지 않는다.

그 뒤에 남는 것:

- 제출물 3종(소개서 PDF · 3분 영상 · 구글폼) — **디자인 최종본 수령 후** 착수.
  ⚠ 마감 **2026-08-03 23:59 KST**.
- M4 · M1(§2-1 제외 결정) · M7 · 나머지 낮음 건 · 07-27 잔여 미승계 건
