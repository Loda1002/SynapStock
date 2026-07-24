# 402 Guard 버그 스캔 리포트
> 생성: 2026-07-24 20:00 KST
> 근거 스냅샷: docs/reports/_bugscan_20260724_192451.json
> 스캔 범위: 앱 전체 모듈(agents·payments·market·web·shared·config) · 렌즈 4종(정확성·엣지케이스·에러처리·커버리지) · 심각도 전부
> 원칙: 읽기 전용 — 코드 자동 수정 없음. 재현 절차 + 수정안 '제안'만. 마커는 점검 지점이지 버그 확정이 아니다.

## 종합 요약
- KPI: 검토 후보 15건 · 확인 13건 · 반려 2건 · [심각 0 · 높음 2 · 중간 2 · 낮음 8 · 정보 1]
- 현재 기준선: 테스트 6종 통과 · red_team 유출0·오탐0 · 미커버 ["web.briefing","web.server"]

| # | 심각도 | 렌즈 | 모듈 | 제목 |
|---|---|---|---|---|
| BUG-01 | 높음 | 에러처리 | payments | `get_token_balance_base` 가 RPC 실패를 0 으로 삼켜 미배송 오탐 통과 |
| BUG-02 | 높음 | 정확성 | agents | 매도 정산 배송검증 부재 — payout 실패해도 settled (결함 I 매도측 구멍) |
| BUG-03 | 중간 | 정확성 | payments | `GUARD_AMOUNT_MISMATCH` 가 실제 매수 흐름에서 구조적 발화 불가 |
| BUG-04 | 중간 | 정확성 | config | 라이브 세션 웹 시작 게이트 기본값 역전(기본 열림) |
| BUG-05 | 낮음 | 정확성 | payments | `authorize` 비멱등 — 중복 order_id 이중계상·release 과다환입 |
| BUG-06 | 낮음 | 엣지케이스 | payments | 주문번호 정규식이 트레일링 개행 허용(`$` vs `\Z`) |
| BUG-07 | 낮음 | 에러처리 | agents | Gemini buy 의 `spend_usdc=NaN` 이 InvalidOperation→'호출 실패' 오귀속 |
| BUG-08 | 낮음 | 엣지케이스 | agents | dust 예산 0-금액 청구서가 Guard/AP2 통과 → 0-수량 settled |
| BUG-09 | 낮음 | 에러처리 | market | 열 누락 CSV 행이 ValueError 대신 원시 TypeError 로 크래시 |
| BUG-10 | 낮음 | 정확성 | web | 첫 화면 KPI `attempts` 가 AP2 거부(reject_count) 누락 |
| BUG-11 | 낮음 | 엣지케이스 | web | 인플라이트 매수 중 auth 교체 시 실패 매수 예약분 원복 누락 |
| BUG-12 | 낮음 | 에러처리 | web | 부팅 복원에서 `load_defaults`/`load_last_briefing` 무제한 대기 |
| BUG-13 | 정보 | 에러처리 | agents | `quote` 가 price <= 0 미방어 — 0/음수 종가 봉에서 DivisionByZero |

## 확정 버그

### [BUG-01] `get_token_balance_base` 가 RPC 실패를 0 으로 삼켜 check_delivery 기준선 오염 → 미배송 오탐 통과 — `payments/x402_solana.py:309`
- **심각도 / 렌즈**: 높음 / 에러처리
- **증상**: `get_token_balance_base` 는 모든 예외를 `except` 로 삼켜 0 을 반환한다(ATA 미존재=진짜 0 과 429/타임아웃=불명 을 구분 못 함). `guard.check_delivery` 는 '잔액 증가분(delta)' 오라클이라 엔진(`web/engine.py:773-776`)이 정산 전 기준선 `before_stock` 을 이 함수로 읽는다. 재매수(ATA 존재·기존 보유량 > 0) 상황에서 before 읽기가 일시적으로 실패해 0 이 되면, 정산 후 잔액(기존 보유분 그대로)이 delta 로 오인돼 브로커가 주식을 전달하지 않아도 '도착 확인(OK)' 이 내려간다 → 402 Guard 핵심 방어(미배송 탐지)가 조용히 무력화.
- **재현**: 재매수 시나리오(기존 보유 5,000,000 base). before 읽기가 rpc_retry 소진으로 예외→0 반환. 브로커 미배송으로 정산 후 실제 잔액=5,000,000(불변). check_delivery: `cur - before = 5,000,000 - 0 = 5,000,000 >= expected_increase(예 180,000)` → ok=True. → 기대: `GUARD_DELIVERY_UNCONFIRMED` 보류 / 실제: 통과. (check_delivery 오탐은 인프로세스 재현. '일시적 RPC 실패' 순간 자체는 오프라인 강제 불가 — 오염 경로 `except→0` 는 코드로 확정.)
- **근거**: `payments/x402_solana.py:306-310`, `web/engine.py:773-776`, `payments/guard.py:191` (열어 확인)
- **수정안**: AccountNotFound(진짜 0)만 0 반환하고 그 외 예외는 상위로 전파. check_delivery/엔진은 기준선 읽기 실패를 '미확인(보류)' 으로 취급해 오염된 기준선(0)을 신뢰하지 않게 한다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 오염 기준선→오탐 통과를 인프로세스로 직접 재현. 자금유출은 아님(check_demand 이 수취인 allowlist·금액 정합을 이미 검증) — 피해는 USDC 지급됐는데 주식 미배송을 settled 로 오정산 + 포지션/한도 오염 + 세션 정지 누락. 매수측에서 닫은 결함 I 를 조용히 재개통하는 조건부 안전망 우회라 높음 유지.

### [BUG-02] 매도 정산에 배송검증 부재 — 라이브에서 브로커 USDC 지급 tx 실패해도 status=settled 로 포지션 차감·예산 환입 — `agents/broker_agent.py:192`
- **심각도 / 렌즈**: 높음 / 정확성
- **증상**: `settle_sale` 은 판매자의 주식 전송 확정(confirmed) 하나로 status 를 정한다(`status='settled' if (not live or confirmed) else 'failed'`). 주식은 넘어갔지만 브로커→판매자 USDC 지급 tx 가 실패한 경우(`if not paid: payout_sig=''`)에도 settled 를 반환한다. `_sell_cycle`(`web/engine.py:856-858`)은 매수측과 달리 check_delivery(USDC 도착 재조회)를 호출하지 않아, `on_sale_completed`(`agents/trading_agent.py:403-405`)가 apply_sell 로 포지션을 없애고 credit_sale 로 대금을 환입하며 realized_pnl 까지 가산한다. 판매자는 주식을 온체인에서 잃고 USDC 는 못 받았는데 앱은 정상 매도로 기록 = 오정산. 매수측에서 check_delivery 로 닫은 결함 I 의 매도측 대칭 구멍.
- **재현**: 라이브 전용 브랜치라 하류 절반을 오프라인 재현 — `status='settled'`·`delivery_tx_signature=''` 인 PaymentCompleted 로 on_sale_completed 호출 → 포지션 1→0 차감, 잔여예산 70→100 환입(지급 tx 는 빈 문자열). 상류는 settle_sale 정적 로직상 paid=False 여도 confirmed=True 면 settled. → 기대: USDC 미도착이면 매도 미확정(포지션 유지·환입 보류) / 실제: settled 로 확정.
- **근거**: `agents/broker_agent.py:182-192`, `web/engine.py:856-858`(매도측 check_delivery 부재; 매수측 794-803 존재), `agents/trading_agent.py:403-405` (열어 확인)
- **수정안**: 매도측도 정산 후 판매자 USDC 잔액 증가분을 check_delivery 로 재조회하고, 미확인이면 status='partial' 로 강등해 apply_sell/credit_sale/realized 가산을 건너뛰고 세션을 정지한다. 또는 settle_sale 이 paid=False 면 status='partial'/'failed' 를 반환하도록 상류에서 막는다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 오정산(앱 원장이 온체인보다 자금 과대계상)이나 발동 조건이 라이브 모드 + 주식 confirmed + 두 번째 레그(payout) 실패로 한정돼 무조건 아님 → 높음 유지(심각 인플레 없음). 드라이런(not live)은 settled 가 의도된 동작이라 문제없음(라이브 전용).

### [BUG-03] `GUARD_AMOUNT_MISMATCH` 가 실제 매수 흐름에서 구조적으로 발화 불가 — 금액 검사 무효화 — `payments/guard.py:134`
- **심각도 / 렌즈**: 중간 / 정확성
- **증상**: check_demand 는 `required.amount` 를 `to_base_units(quote.total_usdc)` 와 비교하는데, 프로덕션 배선(`web/engine.py:732→742→753`, `run_demo.py:256→262→269`)에서 guard 에 넘기는 quote 는 브로커가 required 를 만들 때 쓴 '바로 그 브로커 quote' 다. `make_payment_required` 도 `amount = to_base_units(quote.total_usdc)` 로 설정하므로 guard 의 expected_amount 와 항상 동일 → `GUARD_AMOUNT_MISMATCH` 는 실제 매수 흐름에서 절대 발화하지 않는다. 게다가 어디에서도 '브로커 총액 vs 사용자 의도 spend' 를 대조하지 않아, 악성/버그 브로커가 의도 spend 30 에 대해 per_trade 한도(예 45) 안쪽 44.94 를 청구해도 서명 단계까지 통과한다. red_team 의 '금액 위조 차단' 은 required 와 별도 quote 의 인위적 불일치를 주입해 만든 것이라 실제 데이터 흐름을 반영하지 못한다(데모 신뢰성 이슈).
- **재현**: 실 파이프라인(per_trade_max=45·의도 30). 악성 브로커 `quote.total=44.93` → make_payment_required → check_demand 결과 ok=True·code=OK(AMOUNT_MISMATCH 미발화) → build_payment 서명 성공, AP2 spent=44.93. 대조군: `required=44.93` 에 별도 `quote=32.10` 을 인위 주입하면 그제서야 GUARD_AMOUNT_MISMATCH 발화. → 기대: 의도 spend(30) 초과 청구 차단 / 실제: 44.93 결제 통과.
- **근거**: `payments/guard.py:132-134`, `agents/broker_agent.py:92`, `agents/trading_agent.py:338-339`, `web/engine.py:732-753`, `run_demo.py:256-269`, `payments/ap2_mandate.py:126` (열어 확인)
- **수정안**: 가드에 브로커와 독립적인 금액 상한을 주입 — check_demand 에 `decision.spend_usdc`(또는 독립 시세×수량+기대 수수료)를 넘겨 `quote.total_usdc` 가 그 상한(+허용 슬리피지)을 넘으면 차단. 최소한 build_payment/엔진에서 `quote.total_usdc <= decision.spend_usdc` 를 명시 검증. 브로커 출력 quote 를 '합의 견적' 으로 무검증 신뢰하지 말 것. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 실 파이프라인으로 재현 성공. 심각으로 안 올리는 이유: 악성/버그 브로커 전제, 최악 손실도 per_trade_max·잔여예산으로 상한(무한 드레인 아님), 정직한 브로커에선 실제 유출 없음. 낮추지 않는 이유: 제품 플래그십 방어(구매자 보호·금액 정합)의 핵심 축이 실 흐름에서 텅 비었고 red_team 데모가 인위 주입으로 '차단됨' 을 연출(심사축 '실제 구동' 직결). 수취인 스왑 변형은 독립 allowlist(`guard.py:127`)로 실제 차단되나, 금액-부풀리기 변형(수취인 정상·자기정합 quote)은 미차단이 핵심.

### [BUG-04] 라이브 세션 웹 시작 차단 게이트가 기본값 역전(기본 열림) — `config.py:73`
- **심각도 / 렌즈**: 중간 / 정확성  (후보 높음 → 검증 중간 하향)
- **증상**: `allow_live_from_web` 이 .env 미설정 시 기본 True 다(`_get("ALLOW_LIVE_FROM_WEB", "1")`). 그런데 `config.py:72` 주석('기본 차단, 시연 직전에만 켠다'), `web/engine.py:230-233` 의 '이중 안전장치' 주석·에러('ALLOW_LIVE_FROM_WEB=1 필요'), 배포 런북이 모두 '기본 닫힘' 을 전제한다 — 코드가 자기 문서·에러메시지와 정반대. 그 결과 이중 안전장치(`engine.py:232 if live and not CFG.allow_live_from_web`)가 기본 무력화돼, `--allow-unauthenticated` 배포 + CONTROL_TOKEN 미설정(로컬 습관) 조합이면 URL 만 알면 누구나 라이브(온체인 전송) 세션을 시작·탈취할 수 있다. 지갑키가 마운트된 devnet 시연 중이면 방문자가 데모 지갑의 실제 온체인 거래를 개시 가능(블래스트 반경은 devnet 테스트 토큰). 402 Guard '지출 게이트' 서사와 정면 배치.
- **재현**: `ALLOW_LIVE_FROM_WEB` 환경변수 없이 `... -c "from config import CFG; print(CFG.allow_live_from_web)"` → True(실측 완료). 이 상태로 `POST /api/engine/start {"mode":"live"}` 하면 `if live and not CFG.allow_live_from_web` 가 False 라 통과(키 존재 시 라이브 개시). → 기대: 기본 차단 → EngineError('웹에서 라이브 세션 시작 차단') / 실제: 통과.
- **근거**: `config.py:72-73`, `web/engine.py:230-234`(게이트·주석·에러) (열어 확인) + 인프로세스 실측
- **수정안**: 기본값을 '0' 으로 — `_get("ALLOW_LIVE_FROM_WEB", "0")...`. 그러면 주석·에러·런북과 일치하고 '시연 직전 =1 로 연다' 운영 절차가 실제 성립한다. 로컬 dry 개발은 live=False 라 이 게이트를 안 타므로 무영향. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 후보 높음→중간 하향. (a) CONTROL_TOKEN 이 1차 게이트이고 이건 명시적 2차 심층방어 레이어, (b) 실 악용은 `--allow-unauthenticated` + CONTROL_TOKEN 미설정 + 지갑키 마운트 + 라이브 세션 동시 성립 필요, (c) 반경은 devnet 테스트 토큰뿐(실자금 유출 없음). 그래도 문서·에러·런북 4곳과 정면 모순되는 실제 correctness 결함이라 정보/낮음은 아님.

### [BUG-05] `authorize` 비멱등 — 동일 order_id 재승인 시 spent·예약 이중계상, release 과다환입 — `payments/ap2_mandate.py:139`
- **심각도 / 렌즈**: 낮음 / 정확성
- **증상**: authorize 는 order_id 중복 여부를 검사하지 않고 `spent_usdc += amount` 및 `_reservations[order_id] += amount` 를 무조건 누적한다. 같은 order_id 로 두 번 호출되면 spent 가 2배 계상되고, 이후 release(order_id) 한 번이 누적분(2×amount)을 한꺼번에 되돌려 spent 를 과다 감소시킨다(→ remaining 부풀림 → 후속 거래 예산 초과 지출 가능). 현재 엔진/run_demo 는 매 사이클 새 order_id 를 써서 미트리거지만, 재시도·재처리 로직이 붙는 순간 예산 회계가 깨진다.
- **재현**: `authorize('ord_dupe000001', 30)` 2회 → spent=60(기대 30), remaining=40(기대 70). `release('ord_dupe000001')` 1회 → 60 전액 되돌려 spent=0. → 기대: 중복 승인 거부 또는 멱등 처리 / 실제: 이중계상·과다환입.
- **근거**: `payments/ap2_mandate.py:115-140,142-151` (열어 확인) + 인프로세스 재현
- **수정안**: authorize 진입부에서 order_id 가 이미 `_reservations` 에 있으면 MandateError(중복 주문)로 거부하거나 기존 예약을 그대로 반환(멱등)한다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 현재 어떤 호출 경로에서도 미트리거(order_id 는 매 주문 `uuid.uuid4().hex[:10]` 신규, 사이클당 authorize 1회·finally 에서 settle/release 1회)라 지금 발생하는 자금유출/크래시가 아니라 방어적 견고성 공백 → 낮음. 재시도/재처리 로직 확장 시 즉시 회계 붕괴.

### [BUG-06] 주문번호 정규식이 트레일링 개행 허용(`$` vs `\Z`) — 대사 키에 개행 유입 가능 — `payments/guard.py:54`
- **심각도 / 렌즈**: 낮음 / 엣지케이스
- **증상**: `_ORDER_RE = r'^ord_[0-9a-f]{10}$'` 에서 `$` 는 문자열 끝 직전의 개행도 매치하므로 `'ord_00aabb1122\n'` 이 형식 검사를 통과한다. order_id 는 온체인 Memo(`AT1:{order_id}`)의 대사 키가 되므로, (악성 브로커 위협모델에서) 개행 포함 order_id 가 Memo/로그에 섞여 대사·표시를 오염시키고 '순수 10-hex' 가정을 깬다(로그 인젝션). 자금 유출은 아니나 대사 키 무결성 흠집.
- **재현**: `_ORDER_RE.match('ord_00aabb1122\n')` → True(통과), `'ord_00aabb1122\nEVIL'` → False, `'ord_00aabb112'` → False. → 기대: 순수 10-hex 만 통과 / 실제: 단일 트레일링 개행 통과.
- **근거**: `payments/guard.py:54,109` (열어 확인) + 인프로세스 재현
- **수정안**: 종단 앵커를 `\Z` 로 바꾸거나 fullmatch 사용 — `re.compile(r'^ord_[0-9a-f]{10}\Z')` / `_ORDER_RE.fullmatch(...)`. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 금액·수취인·자산·건별 한도는 `guard.py:117-142` 에서 독립 검증되므로 유출/오정산/크래시 없음 — 피해는 대사 키 개행 유입(로그 라인 분할·표시 오염)으로 한정 → 낮음. 참고: `expected_order_id` 대조(112행)가 브로커 order_id 를 자기 자신과 비교하는 구조라 개행을 걸러내지 못하는 점이 이 흠이 실현되는 이유이기도 함.

### [BUG-07] Gemini buy 의 `spend_usdc=NaN` 이 `_sanitize` 비교에서 InvalidOperation 을 던져 '호출 실패' 로 오귀속 — `agents/trading_agent.py:320`
- **심각도 / 렌즈**: 낮음 / 에러처리  (후보 중간 → 검증 낮음 하향)
- **증상**: Gemini 호출이 형식상 성공(유효한 buy 반환)했어도 `spend_usdc` 가 NaN 이면 `gemini_decider.py:301` 의 `Decimal(str(nan))` 이 조용히 `Decimal('NaN')` 을 만든다(예외 없음). 이어 `_sanitize` 의 `d.spend_usdc > 0`(line 320) 비교가 `decimal.InvalidOperation` 을 던지고, decide() 의 broad `except`(199)가 잡아 규칙 폴백으로 강등하며 reason 에 'Gemini 호출 실패 → 규칙 폴백' 을 붙인다. 성공한 호출이 실패로 기록돼 심사축(AI 활용도)의 gemini/rule 출처가 오염되고, 값을 한도 안으로 clamp 하려던 `_sanitize` 가 오히려 결정을 통째 폐기한다. `web/engine.py:473-477` 은 동일 NaN 을 API 입력 경로에서만 `is_finite()` 로 막았고, 이 Gemini 출력 경로는 미방어. (Infinity 는 `min()` clamp 로 안전, NaN 만 예외.)
- **재현**: FakeBrain 이 `Decision(action='buy', spend_usdc=Decimal('NaN'), source='gemini')` 반환, 워밍업(`_history=[100]*5`) 후 매수신호 틱(MA5=98, price=90)에서 `decide('AAPL', 90)`. → 기대: source='gemini' / 실제: source='rule-fallback', reason='...(MA5 98.00 −2%) — Gemini 호출 실패(InvalidOperation) → 규칙 폴백'.
- **근거**: `agents/trading_agent.py:320,199-203`, `agents/gemini_decider.py:301`, `web/engine.py:473-477` (열어 확인) + 인프로세스 재현
- **수정안**: gemini_decider.decide 의 spend 파싱에서 유한수 여부 검사(`if not spend.is_finite(): spend = strategy.spend_per_trade_usdc`)로 NaN/Infinity 를 필드 정화 단계에서 흡수하거나, `_sanitize` 에서 비교 전에 유한값만 clamp 한다. NaN 을 '호출 실패' 가 아니라 '필드 정화' 로 처리해 성공한 Gemini 결정을 보존. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 후보 중간→낮음 하향. 예외가 199 에서 잡혀 크래시 전파 없음, 폴백이 한도 내 정상 규칙 매수를 산출해 유출·오정산 없음. 피해는 심사 2축(AI 활용도) 증빙 오염(성공한 Gemini 결정이 '호출 실패' 로 라벨) + `_sanitize` 의도 무력화. 발동 전제는 LLM 이 spend_usdc 에 bare NaN 을 내는 비전형 출력.

### [BUG-08] dust 잔여예산 매수 시 0-금액 청구서가 Guard/AP2 를 통과 → 0-수량 매수가 settled 로 기록 — `agents/broker_agent.py:72`
- **심각도 / 렌즈**: 낮음 / 엣지케이스
- **증상**: 잔여예산이 소액(예 0.01 USDC)인데 매수신호가 뜨면 `_decide_by_rule` 이 `remaining_usdc > 0` 만 검사하고(결과 수량 > 0 미검사) spend=0.01 로 buy 를 낸다. `broker.quote` 가 quantity 를 ROUND_DOWN 0.0001 로 내려 0.0000 이 되고 subtotal/fee/total 모두 0 → make_payment_required amount=0. Guard.check_demand 는 금액(0==0)·건별 한도(0 > limit 아님)를 통과하고(ok=True, OK), AP2 authorize 도 0 ≤ 한도라 통과 → 0-수량·0-금액 매수가 서명되고 settled 로 기록되며 apply_buy(0)이 실행된다. 라이브면 0-토큰 전송을 시도한다. 유출은 없지만 '지출 승인 게이트' 가 0-금액 주문을 정상 통과.
- **재현**: `BrokerAgent(fee_bps=30).quote('AAPL', 0.01, 200)` → quantity=0.0000, total=0.00; `make_payment_required(q).amount == 0`; `Guard.check_demand(req, q, expected_order_id=req.order_id)` → ok=True, code='OK'. → 기대: 최소수량 미달이면 견적 거부/보류 / 실제: 0-금액 주문이 게이트 통과.
- **근거**: `agents/broker_agent.py:71-78`, `payments/guard.py:132-142` (열어 확인) + 인프로세스 재현
- **수정안**: quote 에서 `quantity <= 0` 이면 명시적 사유로 견적을 거부(빈 견적/예외)하거나, decide/`_sanitize` 에서 예상 수량이 최소단위(0.0001) 미만이면 hold('예산 부족 — 최소 수량 미달')로 낸다. 방어선으로 Guard.check_demand 에 `amount > 0` 검사를 추가. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 유출·크래시 없음(`apply_buy` 가 `new_qty > 0` 가드로 0/0 division 회피). 영향은 거래로그에 무의미한 0-수량 settled 매수 오염 + 라이브 시 0-금액 SPL 전송 1건 가스 낭비. 조건 좁음(잔여예산 dust 밴드 `0 < remaining < ~0.02 USDC`(price=200)에 들면서 동시에 매수신호). 실재하나 저확률 엣지 → 낮음.

### [BUG-09] `load_bars`: 열 누락 CSV 행이 친절한 ValueError 대신 원시 TypeError 로 크래시 — `market/price_feed.py:82`
- **심각도 / 렌즈**: 낮음 / 에러처리
- **증상**: load_bars 는 행 파싱 실패를 전부 `ValueError('CSV 형식 오류...')` 로 감싸려 한다(except 튜플=KeyError/ArithmeticError/ValueError, line 82). 그러나 열이 누락된 짧은 행은 `csv.DictReader` 가 그 값을 None(restval)으로 채우고, `Decimal(None)` 은 TypeError 를 던진다. TypeError 는 except 튜플에 없어 그대로 전파 → 경로·행 컨텍스트가 담긴 의도된 오류 메시지가 사라지고 'conversion from NoneType to Decimal is not supported' 원시 크래시가 호출자(ReplayPriceFeed 초기화·backtest·web 엔진 부팅)로 새어 나간다. 데이터 손상은 없으나(둘 다 예외) 모듈의 오류-래핑 계약이 이 malformation 에서만 깨진다.
- **재현**: 헤더 `date,open,high,low,close,volume` 에 짧은 행 `2026-01-02,10,11,9`(close/volume 누락) 한 줄 CSV 로 load_bars. → 기대: `ValueError('CSV 형식 오류(...): {row}')` / 실제: `TypeError('conversion from NoneType to Decimal is not supported')`. 대조: 값이 있으나 파싱 불가한 `...,abc,100` 행은 정상적으로 ValueError 로 감싸짐(ArithmeticError→catch).
- **근거**: `market/price_feed.py:76-83` (열어 확인) + 재현
- **수정안**: line 82 except 튜플에 TypeError 추가 — `except (KeyError, ArithmeticError, ValueError, TypeError) as e:`. 또는 Decimal 변환 전 필수 필드 None 여부를 검사해 명시적 ValueError 를 raise. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 데이터 손상·유출 없음(둘 다 예외로 로드 실패), 차이는 오직 진단 품질(누락열 케이스에서만 원시 TypeError 로 컨텍스트 소실). 호출자는 어느 쪽이든 부팅 실패하므로 정상경로를 크래시로 만드는 결함은 아님 → 낮음. 트리거는 열 누락으로 손상된 CSV(수동 편집·API 이상)일 때뿐(정상 fetch 데이터엔 없음).

### [BUG-10] 첫 화면 KPI `guard.attempts` 가 AP2 거부(reject_count)를 누락 — `web/engine.py:1128`
- **심각도 / 렌즈**: 낮음 / 정확성
- **증상**: 매수 시도의 3결말(GuardError=차단→guard_block_count, MandateError=AP2 거부→reject_count, 성공→trades buy 행) 중 attempts 공식이 `guard_block_count + 매수 trade 수` 로 '차단+성공' 만 세고 'AP2 거부' 를 빠뜨린다. reject_count > 0 이면 시도 수가 과소집계되고, 제품 대표 지표인 첫 화면 KPI(시도·차단·유출·오탐)의 내부 정합이 깨진다(차단율=blocked/attempts 과대 표시 소지).
- **재현**: 한 세션에서 build_payment 가 MandateError 1회(`reject_count=1`)하고 정상 매수 2건 체결(buy 2행, guard_block_count=0) 후 `GET /api/state` → guard.attempts = 0+2 = 2. 실제 결제 시도는 3건(거부1+성공2). → 기대 attempts=3 / 실제 2.
- **근거**: `web/engine.py:1128`(attempts 공식), `web/engine.py:760-765`(MandateError→reject_count, trade 미추가) (열어 확인)
- **수정안**: attempts 에 reject_count 포함 — `self.guard_block_count + self.reject_count + len([t for t in self.trades if t['side']=='buy'])`. blocked/rejected/passed 합과 attempts 가 항상 일치하게. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 자금유출·크래시 아닌 KPI/리포팅 정합 문제, reject_count > 0 일 때만. 현재 프런트(web/static)가 attempts/ap2_rejected 를 아직 렌더링하지 않아 후보의 '차단율 과대표시' 는 미래(가드 KPI 카드 완성 후) 가정 → 낮음.

### [BUG-11] 인플라이트 매수 중 한도 변경(auth 교체) 시 실패 매수의 예약분 원복 누락 — `web/engine.py:505`
- **심각도 / 렌즈**: 낮음 / 엣지케이스
- **증상**: `update_limits` 가 새 PaymentAuthorizer 를 만들며 `spent_usdc` 만 이월(engine.py:505)하고 `_reservations` 는 빈 dict 로 시작한다. 라이브 매수 사이클이 `await broker.settle`(RPC 양보) 도중 긴급정지→update_limits 로 `self._auth` 가 교체되면, 그 매수가 실패(settled=False)할 때 finally 의 `self._auth.release(order_id)`(engine.py:819)가 '새' auth 를 대상으로 호출된다. 새 auth 에는 그 order_id 예약이 없어(pop→None) release 가 no-op → 실패 매수 예약금이 예산에 되돌지 않고 spent 에 영구 잔류 → remaining 이 세션 내내 과소. 방향은 fail-safe(예산 과잉 축소일 뿐 과다지출 아님).
- **재현**: live 세션 매수 1건(30 USDC 예약)이 broker.settle 대기 중 → `POST /api/trading/pause` → `POST /api/mandate`(update_limits)로 auth 교체 → 그 매수 settled=False → finally 가 새 auth.release(order_id) 호출(예약 없음→0). → 기대: 30 USDC 예산 복원 / 실제: spent 에 30 잔류, remaining 30 감소 고정.
- **근거**: `web/engine.py:493-508`(spent 만 이월), `web/engine.py:815-819`(finally settle/release), `payments/ap2_mandate.py:142-155` (열어 확인)
- **수정안**: update_limits 즉시적용 분기에서 인플라이트 예약 이관 — `new_auth._reservations = dict(self._auth._reservations)`. 또는 예약 dict 가 비었을 때만 immediate 적용을 허용하고 인플라이트 존재 시 next-session 으로 미룬다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · PaymentAuthorizer 레벨 누수를 결정론적으로 직접 재현(auth 교체 후 new_auth.release=0, spent 30 잔류 vs 미교체 old auth.release=30, spent 0 복원). async 인터리빙(라이브 settle 양보 중 pause→update_limits)은 연결 코드 정독 확인(실네트워크 미구동). 방향 fail-safe(과소지출)라 유출·과다지출·크래시 없음 → 낮음. 트리거 좁음(라이브 + 인플라이트 settled=False 타이밍에 pause+update_limits 겹침).

### [BUG-12] 부팅 복원에서 ping 만 타임아웃, `load_defaults`/`load_last_briefing` 은 무제한 대기 — `web/engine.py:200`
- **심각도 / 렌즈**: 낮음 / 에러처리
- **증상**: `restore_from_store` 가 `store.ping()` 만 `asyncio.wait_for(..., timeout=10)`(engine.py:199)로 감싸고, 이어지는 `load_defaults`(engine.py:200)·`load_last_briefing`(engine.py:207)은 타임아웃 없이 직접 await 한다. 감싼 try/except 는 예외만 잡지 '멈춤(hang)' 은 못 잡는다. Firestore 가 ping 응답 뒤 후속 쿼리에서 스톨하면(하부 gRPC 데드라인이 없다는 전제) lifespan startup 이 무한 대기 → Cloud Run 준비/헬스체크 타임아웃으로 부팅·배포가 막힐 수 있다. 임박한 Cloud Run 배포 경로에 직접 영향.
- **재현**: `FIRESTORE_ENABLED=1` 에서 ping 은 통과하나 이후 defaults/briefings 조회가 네트워크 스톨(파티션). restore_from_store 가 load_defaults 에서 무한 대기 → lifespan 이 yield 로 못 넘어가 서버가 서빙 준비 안 됨. → 기대: ping 처럼 상한 시간 후 기본값(.env)으로 계속 / 실제: 무기한 블록(하부 클라 데드라인 부재 시). (스크래치 스텁 store 로 재현 — ping 통과+load_defaults 영구 스톨 시 restore 가 상한 없이 대기, except 도 예외 없어 못 잡음.)
- **근거**: `web/engine.py:198-218`, `web/server.py:61`(lifespan startup 이 restore 를 블로킹 await) (열어 확인) + 스크래치 재현
- **수정안**: 각 load 호출을 wait_for 로 감싸거나(`await asyncio.wait_for(self.store.load_defaults(), timeout=10)`) restore_from_store 본문 전체를 하나의 wait_for 로 묶어 상한을 보장한다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 유출·크래시 아닌 부팅 지연/Cloud Run startup 타임아웃 리스크, 트리거 좁음(FIRESTORE_ENABLED=1 + ping 성공 직후 후속 조회 스톨). '무한 대기(hang forever)' 표현은 과장 소지 — 실 google-cloud-firestore AsyncClient 는 gapic 기본 데드라인이 있어 완전 무한이 아니라 수십초~수분 후 예외로 빠질 수 있음(후보도 '하부 데드라인 없다는 전제' 로 조건부 명시). 코드가 상한을 보장하지 않고 라이브러리 기본값에만 의존하는 것은 사실 → 낮음. 재현 스크립트는 스크래치패드에만 작성해 저장소 미잔류.

### [BUG-13] `quote`/`sell_quote` 가 price <= 0 을 미방어 — 0(또는 음수) 종가 봉이면 DivisionByZero — `agents/broker_agent.py:72`
- **심각도 / 렌즈**: 정보 / 에러처리  (후보 낮음 → 검증 정보 하향)
- **증상**: quote 는 `spend / (price_usdc * (1 + fee_rate))` 로 수량을 구하는데 `price_usdc=0` 이면 `decimal.DivisionByZero` 를 던진다. price 는 피드에서 오고 `load_bars`(`market/price_feed.py:65-87`)는 종가가 양수인지 검증하지 않아 CSV 에 0/음수 종가 행이 있으면 그대로 Bar 가 되고 get_price 가 0 을 반환한다. 그 틱에서 decide 가 buy 를 내면 `_buy_cycle`→broker.quote 에서 예외가 난다. **단, 제품 주 런타임(web 엔진 `_run_loop`)은 `_tick_once` 를 try/except 로 감싸 예외를 ERROR 이벤트로 방출하고 루프를 계속 돌므로 세션은 죽지 않는다(후보의 '틱 루프 중단' 주장은 주 런타임에서 틀림).** quote 가 무방비인 곳은 `scripts/backtest.py`(개발용 스크립트)뿐. 실데이터엔 0 종가가 없어 트리거는 좁다.
- **재현**: `BrokerAgent(...).quote('AAPL', 30, 0)` → `decimal.DivisionByZero`. load_bars 는 `Decimal(row['close'])` 만 파싱하고 > 0 검사가 없어 close=0 행을 통과시킴. 음수 가격은 예외 없이 quantity=-6.0000 을 조용히 반환. → 기대: 잘못된 가격 거부 / 실제: 미처리 산술예외(고립 호출) — 주 런타임은 try/except 로 흡수.
- **근거**: `agents/broker_agent.py:72`, `market/price_feed.py:65-87`, `web/engine.py:635-638`(_tick_once try/except) (열어 확인) + 재현
- **수정안**: quote/sell_quote 진입부에서 `price_usdc <= 0` 이면 명확한 예외/견적 거부로 처리하고, load_bars 에서 OHLC 중 하나라도 0 이하인 행을 'CSV 형식 오류' 로 거부(근본 차단). (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true(단위 수준) · 후보 낮음→정보 하향. 주 런타임(`web/engine.py:635-638` try/except)은 세션이 안 죽고, quote 무방비는 backtest.py(개발 스크립트)뿐, 트리거는 close=0/음수 손상 CSV 인데 실 파이프라인 데이터엔 없음(후보도 인정). 유출·오정산·주 런타임 크래시 없음 → 정보. 음수 가격은 예외 대신 음수 수량을 조용히 산출하는 별개 무증상 변형(고친다면 load_bars OHLC 양수 검증이 근본적).

## 반려된 후보 (오탐 방지 기록)
- **submit_and_confirm 이 컨펌 파싱 실패 시 ok=True fail-open** — `payments/x402_solana.py:326` — 반려 사유: 코드 '형태' 는 fail-open 이 맞다(`ok=True` 기본 + `except: pass`). 그러나 저장소 고정 solana-py 0.38.0 계약상 `confirm_transaction` 이 '반환' 하면 `conf.value[0]` 은 항상 non-None(break 조건 = `value[0] is not None` + rank≥Confirmed)이라 328행이 예외를 던지지 않는다. 미확정이면 라이브러리가 `UnconfirmedTxError`/`TransactionExpiredBlockheightExceededError` 를 raise → rpc_retry(예외 재-raise, None 미반환)를 지나 함수 밖으로 전파(confirm 호출이 try 블록 밖)=fail-closed. `value==[]` 도 라이브러리 내부 `resp.value[0]` IndexError 로 앱 328행 도달 전 전파. '미확정 tx 가 confirmed=True 로 보고' 는 고정 라이브러리 계약상 발생 불가 → **real=false**. (잔여 위생: fail-open '형태' 라 solana-py 버전 변경 시 잠재화 — 방어 하드닝(기본 False + `err is None` 명시)은 권고, severity 중간→정보.)
- **_cluster_levels 가격 0 원소에서 float division by zero** — `market/indicators.py:135` — 반려 사유: 고립 호출(`_cluster_levels([0.0, 1.0, 1.005])`)·합성 0-저가 봉으로는 ZeroDivisionError 재현되나, 실제 피드로는 0-가격 봉 산출 불가(MockPriceFeed base≥100×0.96~1.04≈96~260, ReplayPriceFeed 실주가 CSV low > 0, quantize(0.01)로도 0 없음). load_bars 가 0/음수를 안 막지만 CSV 는 fetch_market_data 실데이터라 0 봉이 없다 — 402 Guard 런타임 입력면(브로커 청구서·mandate)이 아닌 개발 데이터 산출물. 어떤 실제 입력으로도 미발화 → 트리거 도달 불가, **real=false**(정보). 후보 스스로 '실질 트리거 없음/실피드 재현 불가/정보' 로 정확히 분류. (저비용 방어 하드닝: line 135 분모 0 가드 또는 load_bars OHLC 양수 검증 — 데이터 도메인상 필수 아님.)

## 스캔 한계·미검증
- **커버리지 공백(미커버 모듈)**: `web.briefing`, `web.server` 는 이번 스냅샷이 커버하지 못한 모듈이라 정적 마커·재현 대상에서 빠졌다 — 이 두 곳의 결함은 이번 스캔 범위 밖이다. 특히 `web.server`(라우트·인증·CONTROL_TOKEN 게이트)는 BUG-04 의 배포 위협모델과 인접하므로 별도 점검 권장.
- **일시적 RPC 실패 자체는 오프라인 강제 불가**: BUG-01 은 오염 경로(`except→0`)와 델타 오라클 오탐을 인프로세스로 확정했으나, 'RPC 실패(429/타임아웃)가 나는 순간' 자체는 실네트워크에서만 발생 — 코드 제어흐름으로 성립을 확인했다.
- **라이브 전용 경로는 하류 절반만 재현**: BUG-02(매도 정산 배송검증)·BUG-11(인플라이트 예약 원복)은 하류 상태변화를 오프라인에서 결정론 재현했고, 실네트워크 라이브 인터리빙(settle 양보 중 pause+update_limits)은 연결 코드 정독으로 확인(실 구동 미실시).
- **라이브러리 버전 의존 가정**: BUG-12(Firestore 스톨)는 스크래치 스텁으로 재현했고, 실 google-cloud-firestore AsyncClient 의 gapic 기본 데드라인 유무는 라이브러리 버전에 의존(완전 무한 대기 여부 미확정). 반려 후보 `x402_solana.py:326` 의 결론도 저장소 고정 solana-py 0.38.0 소스 계약에 근거 — 두 건 모두 의존 버전 변경 시 재평가 필요.
- **렌즈·시간 제약**: 정적 마커(bare except·TODO·assert)는 점검 좌표일 뿐이며, 마커가 없는 순수 로직 결함(미선언 엣지케이스 조합 등)은 이번 렌즈 4종(정확성·엣지케이스·에러처리·커버리지)이 놓쳤을 수 있다. 확정 13건·반려 2건의 인용 file:line 은 전수 열어 확인했다.

## 참고 통계(파이프라인 산출)
{
  "checked": 15,
  "confirmed": 13,
  "dropped": 2,
  "by_severity": {
    "높음": 2,
    "중간": 2,
    "낮음": 8,
    "정보": 1
  }
}

## 증거 스냅샷
docs/reports/_bugscan_20260724_192451.json
