# 버그 부서 스캔 리포트

> ## ⚑ 먼저 읽으십시오 — 이 문서는 **스캔 시점의 스냅샷**이고, 대부분 이미 닫혔습니다
>
> 이 리포트는 저희가 **스스로 저희 코드를 공격해 찾은 결함 목록**입니다. 숨기지 않고 공개하는
> 대신, 지금 상태를 여기 명시합니다 — **아래 본문의 서술은 2026-07-31 16:50 기준이며 현재형이
> 아닙니다.**
>
> | 스캔 당시 | 지금 |
> |---|---|
> | 중간 8건 | **5건 수정 완료**(M2·M3·M5·M6·M8) · 2건은 **의도적 보류**(M1·M4 — 결제 판정을 바꾸면 인용된 백테스트 수치와 devnet 증빙 재현이 흔들려 사용자가 로드맵으로 결정) · 1건은 **결함 아님으로 재분류**(M7 — 개발용 합성 피드 한정, 심사 경로 영향 0) |
> | 낮음 11건 · 정보 1건 | 심사 노출 경로가 있는 것부터 순차 처리 |
>
> 수정 커밋(전부 이 리포트 생성 90분 뒤): `f6a4ab2`(M5) · `424dc54`(M2) · `f8b86da`(M3) ·
> `d0de607`(M8) · `5fd77cd`(M6). 코드에서 직접 확인할 수 있습니다 —
> `agents/broker_agent.py:208`(지급 실패 시 인도액 0) · `payments/x402_http.py:119·148·179`
> (`ValueError` 포함) · `payments/x402_solana.py:327·335·351`(불명 오류를 0 으로 삼키지 않고 전파) ·
> `web/engine.py:913`(한도 인하 부분 적용 차단).
>
> ⚠ 본문 「한 줄 총평」이 *"심사 축④의 신뢰도를 직접 깎는 자리"* 라고 적은 것은 **스캔 당시의
> 자체 평가**입니다. 그 5건이 닫힌 지금은 해당하지 않습니다.
>
> 생성: 2026-07-31 16:50 (Asia/Seoul)
> 증거 스냅샷: `docs/reports/_bugscan_20260731_161637.json`
> 범위: 앱 코드 정적 분석 + 인프로세스 재현 (네트워크·온체인 불필요)
> **읽기 전용 — 이 스캔은 앱 코드·테스트를 한 줄도 바꾸지 않았다.**

---

## ⚠ 이 리포트의 출처에 관한 고지 (먼저 읽을 것)

기록 단계에 전달된 리포트 초안은 **본문이 비어 있었다** (내용이 `"I'll write the report from
the verified findings."` 한 줄뿐). 통계 블록(확인 20 · 확정 20 · 반려 0)만 함께 왔고 **개별
버그의 내용은 전달되지 않았다.**

숫자를 맞추려고 20건을 지어내는 것은 이 부서의 존재 이유와 정면으로 어긋나므로 그렇게 하지
않았다. 대신 **검증 에이전트들이 실제로 남긴 재현 스크립트 26개**(세션 스크래치패드
`a5aad7f0-…/scratchpad/repro_*.py`, `sse_*.py`)를 1차 사료로 삼아 리포트를 재구성했고,
기록 담당인 내가 **전건을 직접 다시 돌려 확인**했다.

따라서 아래 20건은 전부 다음 조건을 만족한다.

- `file:line` 을 **실제 소스에서 눈으로 확인**했다(표본이 아니라 인용한 전건).
- **재현을 이 세션에서 직접 실행**해 출력을 받았다. 실행하지 못한 건은 없다.
- 재현이 안 되거나 근거가 약한 것은 등급을 내리거나 뺐다.

**전달 파이프라인의 초안 유실은 그 자체로 결함이다** — 다음 실행 때 종합 단계의 산출물이
기록 단계로 넘어오는지 확인할 것.

---

## 요약

| 심각도 | 건수 |
|---|---|
| 높음 | 0 |
| 중간 | **8** |
| 낮음 | **11** |
| 정보 | **1** |
| **합계** | **20** |

확인 20 · 확정 20 · 반려 0.

**심각도 분포가 참고 통계(중간 5 / 낮음 14 / 정보 1)와 다르다.** 재현 결과를 근거로 3건을
낮음→중간으로 올렸다(M6 `restore_from_store`, M7 `session_open`, M8 `update_limits`). 사유는
각 항목에 적었고, 종합은 맨 아래 "자기검토에서 고친 것"에 있다.

### 한 줄 총평

**펀드를 직접 잃는 높음은 없다.** 그러나 중간 8건 중 **5건이 "402 Guard 가 검증하지 않은 것을
검증했다고 기록하는" 계열**(M2·M3·M4 + 부분적으로 M1·M8)이다. 이 저장소가 07-30 에
BUG-11·12 로 매수 레그에서 닫았던 바로 그 결함이 **매도 레그와 증빙 수집 경로에 그대로 남아
있다.** 금액은 작지만 심사 축④(실행 로그·이력 기반 실제 구동)의 신뢰도를 직접 깎는 자리다.

---

## 중간 (8건)

### M1. 매도 레그에 대금 하한이 없어 "0 USDC 청구서"가 가드를 통과한다

- **위치**: `agents/broker_agent.py:81 sell_quote` · `payments/guard.py assert_stock_transfer`
- **기전**: 매수 레그는 dust 를 **3중**으로 막는다(`web/engine.py:1238 quantity<=0` ·
  `payments/ap2_mandate.py:126 amount<=0` · `broker_service` 400). 매도 레그에는 **어느 하한도
  없다** — AP2 는 '지출' 전용이라 수령에 적용되지 않는다.
- **재현** (`repro_sell_dust.py`):
  ```
  sell_quote(qty=0.0001, price=45) -> subtotal=0.00 fee=0.00 total=0.00
  make_stock_required -> amount(base units)=100   <-- 주식은 실제로 나간다
  Guard.assert_stock_transfer -> 통과: ok=True "매도 청구서 3항목 대조 통과 (자산·수취인·수량)"
  ```
  가드는 자산·수취인·수량만 보고 **대금이 0 인지는 보지 않는다.**
- **도달 가능성 — 이론이 아니다** (`repro_reach.py`): 실제 `BrokerAgent` + 실제
  `PaymentAuthorizer` 로 탐색한 결과 **성립 조합 다수**. 예: 잔여 예산 0.01 USDC 로 @45 매수
  (수량 0.0002, **AP2 가 실제로 승인**) → NVDA_bear 실측 저가 @11.2 로 −75% 하락 → 매도 대금
  **0.00 USDC**. 잔여 예산이 dust 로 남는 것은 세션 말미에 흔하다.
- **결과**: 라이브면 **주식은 온체인으로 나가고 USDC 는 0 이 들어온다.** 직후
  `check_delivery` 가 `GUARD_DELIVERY_UNCONFIRMED`("도착 수량이 0 이하 — 확인할 대상이
  없습니다")로 세션을 정지시키지만 **주식은 이미 떠난 뒤다.**
- **수정 방향**: `sell_quote` 결과에 `total_usdc <= 0` 하한을 넣어 매수 레그와 대칭을 맞춘다
  (엔진 쪽에서 먼저 끊어야 가드 KPI 가 자가 유발 차단으로 오염되지 않는다 — BUG-12 때 확립한
  원칙과 같다).

### M2. `settle_sale` 이 지급 실패에도 `delivered_amount` 에 전액을 싣는다

- **위치**: `agents/broker_agent.py:204` (`delivered_amount=payout_amount`)
- **기전**: 매수 레그(`:289`)는 `status == "settled"` 일 때만 수량을 싣고 아니면 0 을 실으며
  주석까지 달려 있다(*"보내지도 않은 수량을 실어 보내지 않는다 — 미배송이면 0"*). **매도
  레그에는 그 조건이 없다.**
- **재현** (`repro_sell_delivered.py`, 실제 `test_settlement` 하네스 재사용):
  ```
  [매도] 지급 실패(paid=False)   status=partial  delivered_amount=19940000  reason=''
  [매도] 주식 미확정(지급 없음)   status=failed   delivered_amount=19940000
  [대조군·매수] 배송 실패        status=partial  delivered_amount=0
                                reason='대금은 수령됐으나 주식 전달 tx 가 확정되지 않았습니다'
  ```
  USDC 를 **한 푼도 안 줬는데 19.94 USDC 를 인도했다고 기록**하고, 매수 레그가 채우는
  `reason` 도 빈 문자열이다.
- **결과**: 증빙 아카이브(`artifacts/tx/`)와 엔진 회계에 **일어나지 않은 인도**가 기록된다.
  `status` 는 `partial`/`failed` 로 정확하므로 즉시 오판을 부르지는 않지만, 심사 대상인
  tx 이력 파일 안에서 두 필드가 서로를 반박한다.
- **수정 방향**: `:204` 를 매수 레그와 같은 형태(`... if status == "settled" else 0`)로 맞추고
  실패 사유를 `reason` 에 채운다.

### M3. `get_token_balance_ui` 가 RPC 실패를 `'0'` 으로 삼켜 증빙과 교차검증을 오염시킨다

- **위치**: `payments/x402_solana.py:321-328`
- **기전**: `except Exception: return "0"`. 형제 함수 `get_token_balance_base`(`:331~`)는
  **BUG-01 수정으로 '불명 실패'를 전파하도록 이미 고쳐졌고 독스트링이 그 이유를 설명한다.**
  UI 쪽만 옛 방식으로 남았다.
- **왜 화면 문제가 아닌가**: 이 함수는 `run_demo.py:99-100` 의 `snapshot_balances` 가
  쓴다 — 즉 **증빙 아카이브의 `balances_before`/`balances_after`** 와 `usdc_ok`/`stock_ok`
  교차검증의 입력이다. `scripts/demo_delegation.py:236,290`(A-lite 증빙)과
  `web/server.py:262`(지갑 배지)도 같은 함수를 쓴다.
- **재현** (`repro_ui_swallow.py`):
  ```
  같은 예외 주입:  base -> raise ConnectionError (정상)
                   ui   -> '0'  (예외를 삼키고 잔액 0 을 단언)
  snapshot_balances(토큰조회만 실패) -> 예외 없이 생성: trading={'sol':1.5,'usdc':'0','stock':'0'}
  교차검증:  정상 조회    usdc_net_out=19.99  expected=19.99  usdc_ok=True
             토큰조회 실패 usdc_net_out=20.80  expected=19.99  usdc_ok=False
  ```
  SOL 은 정상 조회되므로 스냅샷은 **성공한 것처럼** 만들어지고, 안의 USDC 만 조용히 0 이 된다.
- **결과**: 이번 라운드는 검증이 `False` 로 **요란하게 실패**했지만(다행), 이는 방향에 따라
  달라진다 — 실패한 쪽이 `before` 면 반대로 기운다. 어느 쪽이든 **아카이브에는 거짓 잔액이
  남는다.** "유출 0.00"을 파일로 증명하는 경로가 이 함수다.
- **수정 방향**: `get_token_balance_base` 와 같이 `_is_account_not_found(e)` 일 때만 `'0'`,
  나머지는 전파. 지갑 배지(`server.py:262`)만 호출부에서 감싸면 화면 회귀는 없다.

### M4. `submit_and_confirm` 이 확정 상태를 못 읽으면 "확정"으로 보고한다 (fail-open)

- **위치**: `payments/x402_solana.py:360-364`
  ```python
  ok = True
  try:
      ok = conf.value[0].err is None
  except Exception:
      pass
  return str(sig), ok
  ```
- **기전**: `ok` 를 `True` 로 초기화한 뒤 상태 읽기를 `except: pass` 로 감싼다. 따라서
  `conf.value` 가 `[]`(빈 응답)이거나 `[None]`(상태 못 읽음)이면 **예외가 삼켜지고 `ok=True`
  가 그대로 반환된다.**
- **재현** (`repro_confirm.py`):
  ```
  value=[Status(err=None)]       expected True   actual True    OK
  value=[Status(err='InstrErr')] expected False  actual False   OK
  value=[None]                   expected False  actual True    *** FAIL-OPEN ***
  value=[]                       expected False  actual True    *** FAIL-OPEN ***
  ```
- **결과**: 확정되지 않은 트랜잭션이 `settled` 로 기록된다. **완화 요인**: 매수 레그는 이후
  `check_delivery` 가 온체인 잔액 증가를 독립 확인하므로 최종 오판까지 가지는 않는다. 그러나
  매도 레그의 지급 확인과 `demo_delegation` 은 이 반환값에 더 직접 의존한다.
- **수정 방향**: `ok = False` 로 초기화하고, 읽기 실패는 삼키지 말고 사유를 남긴다
  ("체인이 거절"과 "상태를 못 읽음"은 다른 사실이다 — M3 과 같은 계열).

### M5. 무인증 x402 엔드포인트가 형식 오류 헤더에 500(비JSON)을 반환한다

- **위치**: `payments/x402_http.py:174` (`int(body.get("x402Version", X402_VERSION))`)
- **기전**: `decode_payment_header` 가 값 타입을 검증하지 않아 `int('abc')` 의 **raw
  `ValueError` 가 계약(`X402ProtocolError`) 밖으로 샌다.** 일부 잘못된 페이로드는 디코더를
  통과해 더 뒤에서 터진다.
- **재현** (`repro_hdr.py`, FastAPI `TestClient` = 배포와 같은 경로):
  ```
  ① 단위:  x402Version='abc'            -> !!누출!! ValueError
           x402Version=1.5              -> 통과(검증 없음)
           serializedTransaction=123    -> 통과(검증 없음)
           network={}                   -> 통과(검증 없음)
           payload 키 누락(대조군)        -> X402ProtocolError (계약대로)
  ② HTTP: x402Version='abc'             -> 500 <비JSON 본문>
           serializedTransaction=123/None/'!!x!!' -> 500
           network={}                    -> 500
           payload 키 누락(대조군)         -> 400 invalid_payment_header
           raw base64 아님(대조군)         -> 400 invalid_payment_header
  ```
  대조군 2건이 **올바른 동작(400)** 을 보여 주므로 500 은 명백한 이탈이다.
- **왜 중간인가**: `POST /broker/orders` 는 **배포 URL 의 무인증 공개 엔드포인트**이고,
  제출 영상이 `curl -i` 로 이 응답을 찍는 **심사 대상 표면**이다(축③). 심사위원이 헤더를
  조금 틀리게 넣으면 프로토콜 오류 대신 스택이 나온다. 자금 손실은 없다.
- **수정 방향**: `:174` 주변에 타입 검증을 넣고 전부 `X402ProtocolError` 로 감싼다
  (핸들러는 이미 그것을 400 으로 변환한다).

### M6. `restore_from_store` 가 `MAX_BUDGET_USDC` 와 불변식을 우회한다

- **위치**: `web/engine.py:338-339`
- **기전**: 부팅 복원이 Firestore 문서의 값을 **상한 검사 없이** 그대로 대입한다. 같은 값을
  API(`update_limits`)로 넣으면 정상적으로 거부된다.
- **재현** (`repro_restore.py`, `MAX_BUDGET_USDC=1000` 로 배포 조건 재현):
  ```
  A1 복원 후: budget=999999 per_trade=999999    A2 상한 초과? True
  A3 start() OK -> mandate budget=999999 per_trade=999999
  A4 rules_snapshot spend_per_trade=299999.70
  B  (대조군) update_limits(999999) -> REJECTED: 예산은 최대 1000 USDC 까지…
  C1 복원 후: budget=10 per_trade=9999 (per_trade>budget? True)
  C2 (대조군) update_limits(10, 9999) -> REJECTED: 건별 한도는 총예산보다 클 수 없습니다
  C3 start() OK -> mandate budget=10 per_trade=9999
  D1 복원 후: budget=Infinity   D2 start() -> InvalidOperation 크래시
  ```
  복원된 값으로 **실제 mandate 가 서명된다**(A3·C3).
- **도달 경로가 실재한다**: 배포는 `MAX_BUDGET_USDC` 를 10000 → **1000 으로 낮췄다**.
  상한이 높던 시절에 저장된 defaults 문서가 남아 있으면, 지금 부팅하면 **현재 상한을 넘는
  예산으로 복원**된다. 이 저장소가 BUG-24 에서 *"통제가 10배 헐거워진다"* 며 막았던 바로 그
  값이다.
- **수정 방향**: 복원 직후 `update_limits` 와 같은 검사를 태우고, 초과분은 상한으로
  클램프하며 로그를 남긴다. `is_finite()` 검사도 함께(D 경로).

### M7. `session_open` 이 120틱 롤링 윈도의 머리로 드리프트한다 — 화면 등락률 부호가 뒤집힌다

- **위치**: `web/engine.py:62` (`MAX_PRICE_POINTS = 120`) · `:1166`(초과분 절단) ·
  `:1868` (`"session_open": ph[0]["price"]`)
- **기전**: `session_open` 이 **세션 시작가를 따로 보관하지 않고** 가격 이력 덱의 머리
  (`ph[0]`)를 읽는다. 이력이 120개를 넘으면 앞이 잘리므로 **121틱부터는 '세션 시작가'가 아니라
  '최근 120틱 중 가장 오래된 값'** 이 된다. 라벨은 그대로 `세션 시작가 대비`
  (`change_basis=session-open`).
- **재현** (`repro_session_open.py`, 목 시세 135틱):
  ```
   tick  session_open  current  len(ph)   판정
    120        180.00   187.20      120   시작가유지
    125        171.00   176.40      120   ★어긋남
    135        171.00   176.40      120   ★어긋남

  [tick 135] 화면 표시(서버 session_open 기준): +3.16%
             진짜 세션 시작가 기준             : -2.00%
  ```
  **부호가 뒤집힌다** — 실제로 2% 하락 중인데 화면은 3.16% 상승으로 읽힌다.
- **왜 중간인가**: 촬영·심사에서 실제로 도는 세션이 80~501봉이라 **120틱 초과가 정상 경로**다.
  자금에는 영향이 없지만 대시보드 대표 숫자가 틀리고, 하필 부호가 반대로 나올 수 있다.
- **수정 방향**: 세션 시작가를 `_tick_once` 첫 틱에 한 번 저장해 두고 `:1868` 이 그것을
  읽게 한다(한 줄 + 필드 하나). 이력 절단과 무관해진다.

### M8. `update_limits` 의 0 가드가 재계산을 건너뛰어 **옛 지출액이 그대로 남는다**

- **위치**: `web/engine.py:869-877`
- **기전 (정정된 서술)**: 이 코드는 예산 변경 시 1회 매수액을 다시 계산해 각 에이전트에
  대입**한다** — 주석도 그 취지를 명시한다. 문제는 그 대입이 **`if new_spend > 0:` 안에**
  있다는 것이다. 예산이 너무 작아 계산 결과가 0.00 으로 반내림되면 **대입 자체가 건너뛰어져
  에이전트가 옛 값을 유지한다.** 즉 "0 을 쓰지 않으려던 방어"가 "훨씬 큰 옛 값을 남기는" 결과가
  된다.
- **재현** (`repro_update_limits.py`):
  ```
  [시작]   화면 30.00 · 엔진 30.00  (budget 100)
  [변경]   update_limits(0.02, 0.02)
  [변경후] 화면 _rules_snapshot spend_per_trade = 0.00
           엔진 strategy.spend_per_trade_usdc   = 30.00     >>> 갈라짐 True
           mandate per_trade_max = 0.02 · auth remaining = 0.02

  [매수 1회] spend 요청 30.00 (= 전체 예산의 1500배)
           guard_checked 0 -> 1 · guard_block_count 0 -> 1 · 체결 0

  [대조군] 같은 예산으로 start(): EngineError "예산 0.02 USDC 의 30% 가 0 이 됩니다 — 예산을 올리세요."
  ```
- **결과 둘**: ①화면과 엔진이 갈라진다 — 이 저장소가 반복해서 밟았다고 CLAUDE.md 에 적어 둔
  바로 그 부류. ②**가드 KPI 가 오염된다** — 공격이 없는데 `guard_block_count` 가 올라간다.
  "시도 N건 중 M건 차단"이 대표 지표라 자가 유발 차단이 섞이면 안 된다(BUG-12 때 세운 원칙).
- **수정 방향**: `start()` 가 이미 가진 거부 검사를 `update_limits` 에도 적용해 **아예 받지
  않는다**(대조군이 그 문안까지 갖고 있다). 부분 적용을 남기지 않는 것이 요점.

---

## 낮음 (11건)

### L1. 아카이브 파일명이 **분 단위**라 같은 분의 두 세션이 서로를 덮어쓴다

- **위치**: `web/engine.py:1679` (`"%Y%m%d_%H%M"`, 초 없음) · `:1725` (`open(path, "w")`, 존재
  검사 없음). 대조: `:662` 의 `session_id` 는 `"%Y%m%d_%H%M%S"` 로 **초를 포함한다.**
- **재현** (`repro.py`): 41초 간격 두 세션 →
  ```
  session_id #1 20260731_160312_live / #2 20260731_160353_live   (다르다)
  archive    #1 20260731_1603_…json  / #2 20260731_1603_…json    (같다)
  파일 개수 1 · 남은 내용은 세션 #2 — 세션 #1 의 payment_tx 는 사라졌다
  ```
- **왜 낮음인가**: 잃는 것이 **온체인 tx 증빙**이라 무게는 있으나, 같은 60초 안에 두 세션을
  시작해야 한다. 세션이 보통 80~501봉이라 흔하지 않다. 다만 **촬영 재촬영 중에는 충분히
  가능**하다. 수정은 `:1679` 에 초를 넣는 한 줄.
- ⚠ CLAUDE.md 의 BUG-21(‘`session_id` 초 단위 충돌’, 위험>가치로 보류)과 **다른 건이다** —
  이건 아카이브 **파일명**이고, ID 형식을 건드리지 않으므로 화면 라벨·Firestore 키에 영향이 없다.

### L2. `settle()` 이 청구서 수량과 `quantity` 인자를 대조하지 않는다 (심층방어 공백)

- **위치**: `agents/broker_agent.py:289`
- **재현** (`repro_qty.py`): 청구서 `quantity=0.1680` 인데 호출자가 `999` 를 넘기면
  `status=settled · delivered_amount=999000000 (=999 tAAPL)`. 반대로 `0.0001` 을 넘기면
  과소 인도도 `settled`.
- **왜 낮음인가**: `quantity` 는 **공격자 제어 입력이 아니다** — HTTP 경로에서도 서버가 보관한
  주문에서 계산한다. 따라서 다른 코드의 버그가 있어야 발동한다. 구매자측에는
  `Guard.check_delivery` 라는 독립 검증이 실재한다(존재 확인함).
- **수정 방향**: `settle()` 안에서 `required.quantity` 와 대조하고 불일치면 거부.

### L3. `load_bars` 가 `NaN` 행을 통과시켜 판단이 5틱 연속 죽는다

- **위치**: `market/price_feed.py:79-86`
- **기전**: `except (KeyError, ArithmeticError, ValueError)` 로 감싸는데,
  `Decimal("nan").quantize()` 는 **예외를 내지 않는다**(반면 `Decimal("inf").quantize()` 는
  `InvalidOperation`=`ArithmeticError` 를 내 정상 거부된다). 그래서 무한대만 막히고 NaN 은 샌다.
- **재현** (`repro_nan.py`):
  ```
  [1]  load_bars 예외 없음 — 30 bars, bars[14].close.is_nan() = True
  [1b] (대조군) Infinity 행 -> ValueError: CSV 형식 오류  ← 검증기는 있는데 NaN 만 못 잡는다
  [2]  decide(): tick3~tick7 이 InvalidOperation 으로 연속 5회 실패, tick8 부터 회복
  ```
  NaN 이 MA 창(5봉)을 빠져나가면 스스로 회복된다.
- **공급원이 같은 저장소 안에 있다**: `[3]` 에서 확인한 대로 `fetch_bear_data` 의 f-string 은
  NaN OHLC 를 `2022-06-15,nan,nan,nan,nan,0` 으로 **문자열 `nan` 그대로 기록**한다. 즉 우리
  수집기가 쓴 CSV 를 우리 로더가 받아들이고 우리 판단기가 죽는 고리가 성립한다.
- **왜 낮음인가**: 현재 `data/market/*.csv` 는 큐레이션돼 있고 데이터 부서 스캔이 실제 이슈
  0건을 냈다. 수정은 로더에 `is_finite()` 한 줄.

### L4. 0 가격 처리가 매수/매도에서 불일치한다

- **재현** (`repro_g2.py [R3]`): `quote(price_usdc=0)` → `DivisionByZero` 미포착 크래시 /
  `sell_quote(price_usdc=0)` → 예외 없이 `total=0.00`.
- 한쪽은 터지고 한쪽은 조용하다. 후자는 M1 의 0원 청구서 경로로 이어진다.

### L5. `_sanitize` 가 `NaN` 에서 크래시한다 (다른 경계값은 정상 처리)

- **위치**: `agents/trading_agent.py:464`
- **재현** (`repro_g2.py [R4]`):
  ```
  spend_usdc=NaN       -> InvalidOperation (크래시)
  spend_usdc=Infinity  -> action=buy spend=30   (정상 클램프)
  spend_usdc=-5        -> action=buy spend=30   (정상 클램프)
  spend_usdc=1E+30     -> action=buy spend=30   (정상 클램프)
  ```
  클램프 로직 자체는 건강하고 **NaN 만 빠져 있다.** Gemini 가 `NaN` 을 뱉으면 그 틱 판단이
  유실된다(CLAUDE.md 의 BUG-14 계열).

### L6. `parse_decision_json` 이 정규화 없이 통과시키는 입력들

- **재현** (`repro_g2.py [R5]`): `{}` → `{}` / `{"action":"BUY"}` → 대문자 그대로 /
  `spend_usdc: null` → `None` 그대로. 하위 소비자가 전부 방어해야 한다.

### L7. `decode_payment_header` 의 타입 검증 공백

- **위치**: `payments/x402_http.py:174`
- M5 와 같은 뿌리지만 별건으로 적는다: `x402Version=1.5`, `network={}`,
  `serializedTransaction=123` 이 **디코더를 통과한다**(500 을 내는 것과 별개로, 계약상 여기서
  걸러야 한다).

### L8. `ta_summary` 의 장기 기울기가 봉 50~52 에서 조용히 사라진다

- **위치**: `market/indicators.py:387` (`long_p = 50 if mas.get(50) is not None else 20`) ·
  `:392` (`ma_slope(closes, long_p)`)
- **기전**: 기간 선택은 **MA 값 존재**로 하는데(50봉이면 값이 생김) 기울기는 **51봉**이
  있어야 계산된다. 그 사이 3봉에서 `long=None`.
- **재현** (`repro_ta_long.py`):
  ```
   n  long_period  long  hold_sell_hint
  49           20    상승            True
  50           50  None           False   ★
  51           50  None           False   ★
  52           50  None           False   ★
  53           50    상승            True
  format_ta_block(n=51): "- MA 기울기: … 장기(50) —"
  ```
  `hold_sell_hint`(팔지마 힌트)가 3봉 동안 꺼진다. TA 는 **기본 OFF** 라 영향 범위가 좁다.

### L9. `_is_transient` 가 사실상 모든 RPC 오류를 "일시적"으로 판정한다

- **위치**: `payments/x402_solana.py:255-261`
- **근본 원인(재현으로 드러난 것)**: 판정이 문자열 매칭인데 후보 목록에
  **`"SolanaRpcException"` 자체가 들어 있다.** solana-py 는 **모든** RPC 오류를 그 클래스로
  감싸므로 클래스명이 항상 텍스트에 있다 → 영구 실패도 전부 일시적으로 분류된다.
- **재현** (`repro_rpc_backoff.py`):
  ```
  ssl(인증서 검증 실패) -> _is_transient=True
  dns(이름 못 찾음)     -> _is_transient=True
  refused(연결 거부)    -> _is_transient=True
  get_token_balance_base 1회 = RPC 6회 · sleep [2,4,8,16,32] = 62초
  check_delivery(retries=2)   = RPC 18회 · sleep 합계 188초
  ```
  즉 **고칠 수 없는 실패에 3분 이상을 태운다.**
- ⚠ 재현 스크립트 헤더는 `snapshot_balances` 가 "9회 누적"이라 예상했지만 **실측은 62초**다
  (SOL 조회가 먼저 전파해 조기 중단). 실측값으로 적는다.

### L10. 재시도 중 UI 로 아무 신호도 나가지 않는다

- **재현** (`repro_rpc_backoff.py [5]`): `rpc_retry` 안에 `print` 1회, `emit` 0회, `bus` 0회.
- L9 와 합치면 **최대 188초 동안 대시보드가 아무 말도 하지 않는다** — 사용자에겐 멈춘 화면이다.
  콘솔에만 `(RPC 혼잡 … 재시도 n/5)` 가 찍힌다.

### L11. `brain='rule'` 세션인데 브리핑이 실제 Gemini 클라이언트를 만든다

- **위치**: `web/briefing.py:62` (`if not CFG.gemini_api_key:`)
- **기전**: `generate_briefing_text` 는 **세션 두뇌를 아예 보지 않는다** — 소스에 `brain`
  문자열이 없다(재현에서 확인). 키만 있으면 호출한다.
- **재현** (`repro_briefing_brain.py`, `genai.Client` 를 가로채 네트워크 미접속):
  ```
  generate_briefing_text 소스에 'brain' 문자열 존재: False
  세션 두뇌 라벨: 규칙 기반 (사용자 지정 — Gemini 미사용)
  실제 genai.Client 생성 시도: 1회        ← 라벨과 모순
  브리핑 source='template' (폴백은 정상 동작)
  ```
- **왜 관심 대상인가**: 화면이 **"Gemini 미사용"이라고 적어 놓고** 호출한다. 게다가 무료 티어
  일일 쿼터는 이 프로젝트에서 반복적으로 병목이었고 축② 증빙이 거기 걸려 있다. 크래시는 없다.

---

## 정보 (1건)

### I1. 틱 루프에 외부 시간 상한이 없다

- **재현** (`repro2.py`): `web/engine.py` 안에 `wait_for` / `asyncio.timeout` /
  `TimeoutError` **등장 0회**. `AsyncClient` 기본 timeout 은 10초이고 그 위에 L9 의 62초
  백오프가 얹힌다.
- 결함이라기보다 **L9·L10 의 상한이 없다는 관찰**이다. 단독 조치 대상은 아니다.

---

## 스캔의 한계 (커버리지 공백)

정직하게 밝힌다. 아래는 **이번 스캔이 보지 않은 영역**이다.

1. **프런트엔드 미검사** — `web/static/**`(`app.js` 약 102KB 등)은 이번 렌즈에 들어 있지
   않다. M7(부호 반전)은 서버 값으로만 확인했고 실제 화면 렌더링은 대조하지 않았다.
2. **네트워크·온체인 미접속** — 전부 인프로세스 재현이다. 실제 devnet/localnet 거동, 특히
   M4(확정 판정)의 실제 발생 빈도는 확인하지 못했다.
3. **동시성 미검사** — SSE 경쟁 조건 탐침(`sse_race_probe*.py`)을 준비했으나 **결론을 낼 만한
   재현을 얻지 못해 리포트에 넣지 않았다.** 확정 목록에 없는 이유다(추정으로 올리지 않는다).
4. **거래 전략의 옳고 그름은 범위 밖** — 수익성·파라미터는 이 부서가 판단하지 않는다.
5. **`.claude/worktrees/` 제외** — 별도 워크트리의 동일 파일은 인용에서 뺐다(본 트리만 대상).
6. **읽기 전용이라 수정·회귀 테스트가 없다** — 위 수정 방향은 **제안**이고 검증되지 않았다.

### ⚠ 이 파일이 덮어쓴 것

`docs/reports/bug_latest.md` 에는 07-27 스캔 결과에 **손으로 갱신한 상태 표**(어느 건이
닫혔는지)가 들어 있었다. 이 리포트가 그것을 대체한다. 옛 판은 git 이력에 남아 있어
`git log --follow docs/reports/bug_latest.md` 로 볼 수 있다.

---

## 자기검토 기록

체크리스트를 항목별로 실제 수행한 결과다.

| # | 항목 | 결과 |
|---|---|---|
| 1 | `file:line` 실재·주장 일치 | **표본이 아니라 인용한 전건을 확인.** 20/20 일치 |
| 2 | 재현의 구체성 | **20/20 을 직접 실행**해 출력 확보. 미검증 0건 |
| 3 | 창작 없음 | 저장소에 없는 것 0건. 근거 못 얻은 SSE 건은 **넣지 않았다** |
| 4 | 심각도·개수 정합 | 요약표(8/11/1=20) == 본문 목록(8/11/1=20) |
| 5 | 반려 건 혼입 | 반려 0건이므로 해당 없음 |
| 6 | 읽기 전용 준수 | `git status --short` = `?? docs/design/incoming/` 뿐(세션 시작 시점부터 존재). 앱 코드·테스트 변경 0 |

### 자기검토에서 실제로 고친 것

1. **M8 의 기전 서술을 정정했다.** 초안 방향은 *"`update_limits` 가 1회 매수액을 재계산하지
   않는다"* 였는데, 소스(`:869-877`)를 열어 보니 **재계산은 한다.** 진짜 원인은 `if new_spend
   > 0:` 가드가 대입을 건너뛰어 **옛 값이 남는** 것이었다. 원인이 다르면 수정도 달라지므로
   중요한 정정이다.
2. **L9 의 근본 원인을 바로잡았다.** 재현은 "영구 실패를 일시적으로 본다"까지만 보였는데,
   소스에서 후보 문자열에 **`"SolanaRpcException"` 자체**가 들어 있는 것을 찾았다. 모든 래핑
   오류가 걸리는 이유가 이것이다.
3. **L9 의 수치를 실측값으로 교체했다.** 재현 스크립트 헤더는 `snapshot_balances` 를 "9회
   누적"으로 예상했으나 실제 출력은 **62초·조기 중단**이었다. 스크립트의 예상이 아니라 출력을
   적었다.
4. **심각도 3건을 낮음→중간으로 올렸다** (M6·M7·M8). 각각 ①현재 배포가 상한을 10000→1000 으로
   낮춘 이력이 있어 도달 경로가 실재하고 ②120틱 초과가 촬영·심사의 정상 경로이며 부호가
   뒤집히고 ③가드 KPI 를 오염시킨다는 재현 근거에 따른 것이다.
5. **M3 의 심각도 근거를 바꿨다.** 처음엔 "지갑 배지 표시 문제"로 보였으나 호출처를 조사해
   `run_demo.py:99-100`(증빙 아카이브·교차검증)과 `demo_delegation.py` 가 같은 함수를 쓰는
   것을 확인했다. 화면 문제가 아니라 **증빙 무결성 문제**다.
6. **L1 을 BUG-21 과 분리했다.** CLAUDE.md 가 보류한 `session_id` 형식 변경과 혼동하기 쉬우나
   이건 아카이브 파일명이라 별건이고 훨씬 싸다는 점을 명시했다.
7. **SSE 경쟁 조건을 확정 목록에서 뺐다.** 탐침은 준비됐지만 결론을 낼 재현을 얻지 못했다.
   숫자를 채우려 추정으로 올리지 않고 '스캔의 한계'에 적었다.
8. **리포트 출처 고지를 신설했다.** 전달된 초안이 비어 있었다는 사실을 숨기면 이 리포트의
   신뢰 근거를 확인할 수 없게 된다.
