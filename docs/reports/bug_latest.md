# 402 Guard 버그 스캔 리포트
> 생성: 2026-07-27 13:42 (Asia/Seoul)
> 근거 스냅샷: docs/reports/_bugscan_20260727_131137.json
> 스캔 범위: 앱 전체 모듈(agents·payments·market·web·shared·config) · 렌즈 4종(정확성·엣지케이스·에러처리·커버리지) · 심각도 전부
> 원칙: 읽기 전용 — 코드 자동 수정 없음. 재현 절차 + 수정안 '제안'만. 마커는 점검 지점이지 버그 확정이 아니다.

---

## ⚠ 이 리포트는 **2026-07-27 13:42 스캔 시점의 스냅샷**입니다 — 현행 상태가 아닙니다

스캔 이후 아래 10건이 수정·푸시됐습니다. 리포트 본문은 **재현 절차의 근거로 남기기 위해
스캔 당시 그대로** 두고, 상태만 여기에 적습니다.

| 건 | 상태 | 해결 커밋 |
|---|---|---|
| **BUG-01** (높음) 매수 레그: 주식 전달 실패해도 `settled` | ✅ 수정 | `2981739` — settled/partial/failed 3분기 + 판매자측 409 분기 |
| **BUG-02** (중간) 청구서 `decimals` 미검증 → AP2 차감액 위조 | ✅ 수정 | `7a2b251` — 단위 검사 추가, 매도 레그 대칭 |
| **BUG-03** (중간) `release()` 의 0 클램프가 추세추종 복리 자본을 삼킴 | ✅ 수정 | `bc063fd` — 클램프 제거(죽은 코드), 회귀 6건 + 음성 대조 |
| **BUG-04** (중간) 하드 차단 건에 직전 건의 의미 대조 판정이 붙어 나감 | ✅ 수정 | `7a2b251` — `SemanticVerdict` 에 `order_id` 봉인 |
| **BUG-05** (중간) partial 매도에서 포지션 미차감 → 총자산 과대계상 | ✅ 수정 | `a8dcdb8` — `confirmed` 조건은 partial 에만(드라이런 정상 매도 보호) |
| **BUG-06** (중간) 체결되지 않은 판단이 회고에 사실처럼 남아 프롬프트 오염 | ✅ 수정 | `fbec112` — 기록을 판단→체결 시점으로, 조건은 포지션 변동과 동일 |
| **BUG-07** (중간) 한도 인하가 종목 몫을 사용액 밑으로 밀어 AP2 총예산 집행이 깨짐 | ✅ 수정 | `77e8eaa` — 재슬라이스 전 종목별 검사(재서명 앞) + 음수 몫 바닥 처리 |
| **BUG-08** (중간) 첫 화면 KPI '시도' 분모가 매수만 셈 | ✅ 수정 | `7a2b251` — 분모·분자 대칭 |
| **BUG-09** (중간) 끝난 세션의 마무리가 다음 세션의 긴급정지를 해제 | ✅ 수정 | `0bec828` — 회귀 `test_finalize_race` 18건 신설 |
| **BUG-10** (중간) 합성 인트라바 문구가 코드보다 강하게 단언 | ✅ 수정 | `400296b` — 문구 정정(알고리즘은 사용자 보류 결정대로 유지) |

**따라서 '중간' 잔여는 0건입니다**(표의 9건 전부 + 높음 1건이 닫혔습니다).
**BUG-11·BUG-12 도 2026-07-30 에 닫혔습니다** — 아래 완전성 비평이 *"금액 기준으로만 낮음이고
심사 신뢰도 관점에서는 먼저 볼 값어치가 있다"* 고 지목한 2건입니다(402 Guard 가 검증하지 않은
것을 "확인했다"고 기록하는 자리).

| 건 | 상태 | 해결 커밋 |
|---|---|---|
| **BUG-11** (낮음) 기대 증가분 0 이면 온체인을 못 읽고도 '도착 확인' | ✅ 수정 | `7652326` — 0 이하는 확인 대상 없음으로 보류 + '미도착'과 '재조회 실패' 구분 |
| **BUG-12** (낮음) 수량 0 견적의 0원 청구서가 전 계층 통과·틱마다 반복 | ✅ 수정 | `8dc7325` — 엔진·CLI 에서 결제로 만들지 않음 + AP2 `amount > 0` 하한 |

따라서 남은 것은 '낮음' 12건·'정보' 1건입니다. ⚠ 심각도는 **재현된 금액 영향** 기준이라
수정 우선순위와 다릅니다 — "낮음이니 전부 안전"으로 읽지 마세요.
⚠ **하드 검사 8종 카운트는 그대로입니다** — BUG-11 은 `check_demand` 가 아니라 정산 후
배송 확인 계층(`check_delivery`)이고, BUG-12 의 하한은 AP2 `authorize` 에 넣었습니다.
소개서·대본·README 의 "8종" 표기를 고칠 필요가 없습니다.
BUG-01 수정이 만든 후속 결함(매수 레그가 유출 KPI 에서 빠지던 비대칭)은 별건으로 `8fa20e1`
에서 닫혔습니다. 현행 기능 상태의 단일 출처는 `docs/FEATURES.md` 입니다.

⚠ **심각도 표기 주의**: BUG-03·06·07 은 이 리포트 기준 **'중간'** 입니다. 계획 문서에
한때 "전부 낮음"으로 적혀 있었는데 사실이 아닙니다 — 특히 BUG-07 은 적대 검증에서
낮음→중간으로 **상향**됐고, 재현 시 AP2 총예산을 15 USDC 초과 승인하는 것이 실측됐습니다.

---

## 종합 요약
- KPI: 검토 후보 27건 · 확인 25건 · 반려 2건 · [심각 0 · 높음 1 · 중간 9 · 낮음 14 · 정보 1]
- 현재 기준선: 테스트 20종 통과 · red_team 유출0·오탐0 · 미커버 ["web.briefing"]
- **확정 25건 전부 reproduced=true 다** — 24건은 인프로세스에서 직접 재현했고, BUG-22 만 네트워크 I/O 경계(get_client·snapshot_balances)를 대체한 라이브 하니스로 재현했다. 재현 불가 0건. localnet·devnet RPC 는 한 건도 쓰지 않았다.
- **결제 본체(payments·agents) 9건**이 가장 무겁다. 특히 BUG-01(매수 레그 배송 실패를 settled 로 보고)은 매도 레그에 이미 적용된 수정(BUG-02 라운드)이 매수 레그에만 빠진 비대칭이다.
- **중복 2건**: BUG-12·BUG-20 은 `docs/reports/bug_latest.md` 의 기존 BUG-08·BUG-11 과 같은 결함이다(미수정 상태로 잔존, 줄번호만 리팩터로 이동). 새 건으로 집계하지 말 것.
- **심각도 조정 7건**: 상향 2(BUG-07·BUG-08 낮음→중간), 하향 5(BUG-12·BUG-13·BUG-16·BUG-18·BUG-19 중간→낮음). 상세 근거는 각 항목의 적대 검증 줄에 있다.

| # | 심각도 | 렌즈 | 모듈 | 제목 |
|---|---|---|---|---|
| BUG-01 | 높음 | 에러처리 | agents | 매수 레그: 주식 전달 tx 가 실패해도 `status="settled"` — 매도 레그의 partial 과 비대칭 |
| BUG-02 | 중간 | 정확성 | payments | 청구서의 `decimals` 를 아무도 검증하지 않는다 — AP2 예산 차감이 브로커가 준 값에 좌우된다 |
| BUG-03 | 중간 | 정확성 | payments | `release()` 의 0 클램프가 추세추종 세션의 복리 자본(음수 spent)을 삼킨다 |
| BUG-04 | 중간 | 정확성 | payments | 하드 차단된 청구서에 '직전 건'의 의미 대조 판정이 그대로 붙어 로그로 나간다 |
| BUG-05 | 중간 | 정확성 | agents | partial 매도에서 포지션이 차감되지 않아 총자산이 과대계상된다 |
| BUG-06 | 중간 | 정확성 | agents | 체결되지 않은 판단이 '직전 행동 회고'에 사실처럼 기록돼 다음 틱 프롬프트를 오염시킨다 |
| BUG-07 | 중간 | 정확성 | web | 추세추종 멀티에서 예산을 낮추면 슬라이스 잔여가 음수가 되고 AP2 총예산 집행이 깨진다 |
| BUG-08 | 중간 | 정확성 | web | 첫 화면 KPI 의 '시도' 분모가 매수만 세면서 매도측 차단은 분자에 넣는다 |
| BUG-09 | 중간 | 엣지케이스 | web | 끝난 세션의 `_finalize` 가 await 뒤에 전역 상태를 덮어써 '다음 세션'의 긴급정지를 해제한다 |
| BUG-10 | 중간 | 정확성 | market | 합성 인트라바가 실제 일봉의 고가·저가를 통과하지 않는다(UI 선택지 2/4/8 전부) |
| BUG-11 | 낮음 | 엣지케이스 | payments | `expected_increase_units` 가 0 이면 잔액을 못 읽어도 '도착 확인'으로 통과한다 |
| BUG-12 | 낮음 | 엣지케이스 | agents | 수량 0 견적이 방어 계층을 전부 통과 — 잔여 예산이 줄지 않아 반복된다 (기존 BUG-08 재발견) |
| BUG-13 | 낮음 | 정확성 | agents | 견적 총액이 요청 지출을 최대 0.01 USDC 초과 — 정상 거래가 AP2 '예산 초과'로 거부 |
| BUG-14 | 낮음 | 에러처리 | agents | Gemini 가 `spend_usdc` 에 NaN 을 돌려주면 그 틱 판단이 통째로 유실된다 |
| BUG-15 | 낮음 | 엣지케이스 | market | 가격 0 인 봉이 피벗이 되면 `_cluster_levels` 가 0으로 나눠 `ta_summary` 가 크래시 |
| BUG-16 | 낮음 | 엣지케이스 | market | 종료일만 지정하면 워밍업 오프셋이 구간을 넘어 '재생 구간이 비어 있습니다'로 죽는다 |
| BUG-17 | 낮음 | 에러처리 | market | 열이 잘린 CSV 행에서 TypeError 가 포장되지 않아 문제 행 정보가 사라진다 |
| BUG-18 | 낮음 | 엣지케이스 | web | `tick_interval_sec=NaN` 이 안전범위 클램프를 통과해 틱 루프가 폭주한다 |
| BUG-19 | 낮음 | 엣지케이스 | web | 적립식 회당 금액이 NaN 이면 500 으로 새어나가고 Infinity 면 세션이 그대로 시작된다 |
| BUG-20 | 낮음 | 정확성 | web | 세션 중 한도 변경이 진행 중인 결제 예약을 버려 실패한 결제의 예산이 영구 소모된다 (기존 BUG-11 재발견) |
| BUG-21 | 낮음 | 엣지케이스 | web | `session_id` 가 초 단위라 같은 초에 시작한 두 세션이 Firestore 문서를 덮어쓴다 |
| BUG-22 | 낮음 | 엣지케이스 | web | 라이브 모드에서 `start()` 동시 호출이 중복 세션을 만든다(전역 싱글턴 엔진) |
| BUG-23 | 낮음 | 에러처리 | web | SSE 구독자 큐가 차면 이벤트를 조용히 버리고 클라이언트는 유실 사실을 얻지 못한다 |
| BUG-24 | 낮음 | 에러처리 | config | 숫자형 환경변수를 빈값으로 주입하면 임포트 단계에서 앱 전체가 죽는다 |
| BUG-25 | 정보 | 커버리지 | shared | `PaymentRequirements.to_dict/from_dict` 가 x402 와이어 포맷과 어긋난 채 사문화 |

## 확정 버그

### [BUG-01] 매수 레그: 주식 전달 tx 가 실패해도 `status="settled"` — 매도 레그의 partial 과 비대칭 — `agents/broker_agent.py:272`
- **심각도 / 렌즈**: 높음 / 에러처리
- **증상**: `settle()` 은 line 260 에서 주식 전달 결과를 `delivered` 로 받아 line 261-262 에서 서명만 비우고 그 값을 버린다. status 는 line 272 에서 USDC 결제 확정(`confirmed`)만 보고 정해지므로, 구매자가 USDC 를 온체인으로 보냈는데 주식이 한 주도 안 온 상황이 `settled` 로 보고된다. `delivered_amount` 에는 보내지도 않은 수량이 그대로 실린다. 같은 상황의 매도 대칭인 `settle_sale` 은 line 190-197 에 3분기(settled/partial/failed)가 실재한다 — 매수 레그만 그 수정이 안 들어갔다. `web/engine.py:1215-1235` 의 `check_delivery` 가 웹 엔진 경로는 덮고 있으나, devnet 온체인 증빙을 만드는 `run_demo.py` 에는 배송 재검증이 없어(`run_demo.py:327` 이 `completed.status` 를 그대로 기록) `artifacts/tx/` 아카이브에 `status: settled, delivery_tx: ""` 인 거래가 성공으로 남는다.
- **재현**: 저장소 자체 하니스(`scripts/test_settlement.py` 의 FakeClient)로 `bk.settle(..., live=True, client=FakeClient([True, False]))`(1차 확정=구매자 USDC, 2차 실패=브로커 주식 전달) → 기대: `status="partial"`, `delivered_amount=0`(매도 대칭) / 실제: `status='settled' · delivery_tx_signature='' · delivered_amount=168000 · reason=''`. 이어서 `ta.on_completed(...)` 가 포지션을 `0.1680` 으로 반영(지갑 실제 0)하고 영수증은 `confirmed=True, note=''` 로 정상 거래처럼 찍힌다. 동일 조건 매도 대조군 `settle_sale` 은 `status='partial'` 반환.
- **근거**: `agents/broker_agent.py:258-272`(열어 확인) · 대칭 비교 `agents/broker_agent.py:188-197` · 보상 로직 `web/engine.py:1213-1235` · 미보상 경로 `run_demo.py:300-333` · `payments/x402_solana.py:334-352`(온체인 실패 시 예외가 아니라 `(sig, False)` 반환 — 브로커 잔고 부족·ATA 부재가 정확히 이 경로)
- **수정안**: `settle_sale` 과 같은 3분기로 맞춘다. line 248 부근에 `delivered=True` 초기화를 두고 line 260 의 결과를 살린 뒤 line 272 를 `status="settled" if (not live or (confirmed and delivered)) else ("partial" if confirmed else "failed")` 로 바꾼다. `delivered_amount` 도 미배송이면 0 으로 낮춘다. **⚠ 부작용 주의**: `web/broker_service.py:187` 이 `completed.status != "settled"` 면 **402 Payment Required** 를 돌려준다 — settle() 이 partial 을 반환하기 시작하면 구매자 USDC 가 이미 온체인에서 떠난 상황에 브로커가 재결제를 요구하는 꼴이 되어 이중지불 유도 위험이 생긴다. partial 은 402 가 아니라 별도 상태(409/5xx 계열 + 미배송 명시)로 분기해야 한다. 회귀 확인 대상: `scripts/test_settlement.py`(매수 정산 기대값 갱신 필수) · `scripts/test_leak_kpi.py` · `scripts/test_http402.py`. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 인용 4곳 전부 실물 일치. '심각'으로 올리지 않은 근거 — 웹 엔진은 `check_delivery` → partial 강등 + `_record_leak` + `pause` 로 방어되고, run_demo 도 세션 끝 잔액 교차검증에서 `cross_check.stock_ok=false` 로 남는다. 다만 그 교차검증은 세션 합산이라 어느 거래인지 특정하지 못하고, 개별 거래 행은 여전히 `settled/confirmed=true` 이며 데모는 유령 포지션으로 계속 매매한다. 자금 방어는 서 있고 **기록의 정직성·오정산**이 깨지는 사안.

### [BUG-02] 청구서의 `decimals` 를 아무도 검증하지 않는다 — AP2 예산 차감이 브로커가 준 값에 좌우된다 — `payments/guard.py:188`
- **심각도 / 렌즈**: 중간 / 정확성
- **증상**: `check_demand` 는 금액을 `int(reqs.amount) != to_base_units(quote.total_usdc, self.usdc_decimals)` 로 묶지만, 같은 청구서의 `reqs.decimals`(단위 자체)는 검사 6종 어디에서도 보지 않는다. 그 필드는 (a) `agents/trading_agent.py:483` `from_base_units(reqs.amount, reqs.decimals)` 로 **AP2 가 차감할 금액**을 만들고 (b) `trading_agent.py:516·562` 에서 실제 `transfer_checked` 의 decimals 로 들어간다. 악성/버그 브로커가 amount 는 정직하게 두고 decimals 만 9 로 올리면 가드를 전부 통과한 뒤 AP2 예산이 1/1000 만 차감된다 — 광고하는 3중 통제('금액=AP2 mandate')의 한 축이 상대방이 채우는 필드에 매달려 있다. 매도 레그(`check_stock_transfer:350-353`)도 동일하게 미검증.
- **재현**: mandate(예산100·건별50)+Guard(usdc_decimals=6), quote.total=30.00, amount=30000000 고정하고 decimals 만 변경 → `decimals=6: guard.ok=True/OK, AP2차감=30, spent=30 remaining=70` / `decimals=9: guard.ok=True/OK, AP2차감=0.03, spent=0.03 remaining=99.97`. 기대: 단위 불일치로 차단. 실제: 30 USDC 청구서를 3,300번 반복해도 예산 100 이 소진되지 않는다.
- **근거**: `payments/guard.py:186-190`·`348-353`(열어 확인, `reqs.decimals` 를 기대값과 비교하는 라인이 파일 전체에 0건 — 파일 내 `decimals` 등장 11곳 전수 확인) · `agents/trading_agent.py:483,504-507,516,562` · `payments/x402_http.py:173`(`decimals=int(extra.get("decimals", 6))` — 원격 402 JSON 무검증 수용)
- **수정안**: `check_demand` 의 금액 검사 바로 앞에 단위 검사를 넣는다 — `if int(reqs.decimals) != int(self.usdc_decimals): return self._block(GUARD_ASSET_MISMATCH, "청구서 단위(decimals)가 결제 자산 단위와 다릅니다", str(self.usdc_decimals), str(reqs.decimals))`. `check_stock_transfer` 는 이미 `stock_decimals` 를 파라미터로 받으므로(`guard.py:295`) 대칭 적용이 가능하다. 신규 코드 신설보다 기존 `GUARD_ASSET_MISMATCH` 재사용이 더 작은 변경이다(단위도 자산 정체성의 일부). 보강으로 AP2 에 넘길 금액을 브로커 필드가 아니라 검증된 `quote.total_usdc` 에서 유도하면 근본 차단. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · **라이브 자금 유출은 없다** — `payments/x402_solana.py:112-119` 가 `transfer_checked` 를 쓰므로 민트 실제 decimals 와 다른 값은 SPL 프로그램이 온체인에서 거부한다(매 건 tx 실패+수수료 소모). 실피해는 ①드라이런/데모에서 '한도 초과 불가'가 실제로 강제되지 않는 예산 오계상 ②원격 브로커가 단위를 쥐는 신뢰 경계 결함. 부분 완화 1건: `guard.py:243` 의 LLM 의미 대조 라벨이 오염된 그 필드로 만들어져 우연히 걸릴 여지가 있으나, `semantic is None` 이면 통과하는 선택적·비결정적 계층이라 결정론적 단위 검사의 대체물이 못 된다.

### [BUG-03] `release()` 의 0 클램프가 추세추종 세션의 복리 자본(음수 spent)을 삼킨다 — `payments/ap2_mandate.py:150`
- **심각도 / 렌즈**: 중간 / 정확성
- **증상**: `release` 는 실패한 결제의 예약분을 되돌리는 함수인데 `self.spent_usdc = max(Decimal(0), self.spent_usdc - amt)` 로 0 에서 잘라낸다. 그런데 `credit_sale(allow_surplus=True)`(`ap2_mandate.py:171`, 추세추종에서 `trading_agent.py:591` 이 호출)는 spent 를 의도적으로 음수까지 내려 '실현이익 재투자'를 표현한다(독스트링 `:162-170` 이 설계상 정당한 상태로 명시). 이 상태에서 매수 1건이 release 되면 음수 spent 가 0 으로 올라붙어, 벌어놓은 운용 한도가 세션이 끝날 때까지 영구히 사라진다.
- **재현**: 실제 제품 클래스(`TradingAgent.on_sale_completed`, `Strategy(mode="trend")`)로 매수100 정산 → 150 매도 정산 → 재진입 매수150 → release → 기대: `spent=-50 / remaining=150`(원상복구) / 실제: **`spent=0 / remaining=100`** — 실현이익 50 USDC 가 한도에서 증발.
- **근거**: `payments/ap2_mandate.py:150`·`157,162-173`(열어 확인) · `agents/trading_agent.py:591` · `web/engine.py:1246-1251`(finally 의 release) · `web/engine.py:1191`(기준선 조회 실패 시 release). 도달 경로 다수 실재: `broker_agent.py:272`(라이브 미확정)·`:221-227`(중복·리플레이 차단 → 드라이런에서도 failed)·`:236-241`(verify_payment 실패)·`engine.py:1230`(배송 미확인 partial).
- **수정안**: `payments/ap2_mandate.py:150` 을 `self.spent_usdc -= amt` 한 줄로 바꾼다. `release` 의 멱등성은 `_reservations.pop`(`:147-149`)이 담당하고 `_reservations` 는 `authorize`(`:139`)에서만 기록되므로 amt 는 항상 authorize 가 더한 값과 같다 — 비잉여 모드에서 클램프는 절대 발동하지 않는 죽은 코드다. `credit_sale` 쪽 클램프(`:172-173`)는 의미가 있으니 그대로 둘 것. 회귀 테스트로 '매도 잉여 → 매수 실패 → release' 시퀀스 추가. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 반려 사유 4종(멱등 안전망·기존 알려진 한계·도달 불가·상위 보정) 전부 해당 없음을 개별 확인. 특히 `engine.py:1403-1404` 의 알려진 한계 6번은 "release 가 잔여 한도를 **부풀린다**"로 방향이 정반대라 이 건은 미문서화. 자금이 새거나 오정산되지 않고 방향이 보수적이라 '심각'은 아니나, 발동 시 세션 끝까지 영구적이고 아무 로그도 남기지 않는 조용한 손상이며 화면의 '잔여 예산'을 틀리게 만든다. 후보의 `allow_surplus` 플래그 승격안보다 위 1줄 수정이 더 작다. `scripts/` 에 `payments.ap2_mandate` 전용 테스트 파일 없음(스냅샷 `dedicated_test_file=false` 와 일치 — 파일 목록으로 재확인).

### [BUG-04] 하드 차단된 청구서에 '직전 건'의 의미 대조 판정이 그대로 붙어 로그로 나간다 — `payments/guard.py:237`
- **심각도 / 렌즈**: 중간 / 정확성
- **증상**: `self.last_semantic = None` 리셋이 `check_semantics` 안에만 있는데, 하드 검사(`assert_demand`)에서 차단되면 `check_semantics` 는 애초에 호출되지 않는다(`trading_agent.py:493-500` 이 `assert_demand` → `assert_semantics` 순서). Guard 는 세션 1개를 전 종목이 공유하므로(`web/engine.py:505`) 직전 주문의 판정이 남아 있고, 엔진의 GuardError 핸들러가 `_emit_semantic(...)`(`engine.py:1164·1295`)을 불러 그 스테일 판정을 **차단된 주문의 order_id 로** GUARD_SEMANTIC 이벤트에 실어 보낸다. 의미 대조가 돌지도 않은 건에 '청구서 의미 대조 통과'가 찍힌다.
- **재현**: 항상 match 를 돌려주는 semantic 스텁으로 ①1건차 정상 청구서 `check_semantics` 실행 ②2건차는 pay_to 만 악성으로 바꿔 `assert_demand` 실행 → `2건차 차단: GUARD_PAYEE_UNKNOWN` 직후에도 `guard.last_semantic` 이 1건차 값 `{'code':'OK','verdict':'match','llm_called':True}` 그대로 잔존, `semantic.stats.checked` 는 1 에 머묾. `_emit_semantic` 페이로드 재현 시 `{'side':'buy','order_id':'ord_0000000002','code':'OK','verdict':'match',...}`. 기대: 차단 건에는 판정 이벤트가 없거나 'not-checked'.
- **근거**: `payments/guard.py:237`(리셋 지점이 저장소 전체에서 여기 하나) · `payments/guard.py:219-221`(assert_demand 가 먼저 raise) · `agents/trading_agent.py:493-500`·`545-554` · `web/engine.py:877-887`·`1164`·`1295`·`505` · `web/static/js/app.js:850-862`
- **수정안**: 리셋 지점을 `assert_demand`/`assert_stock_transfer` 진입부로 옮긴다(`check_demand` 진입부에 두면 red_team·테스트가 직접 호출하는 경로에 부수효과가 생긴다). 더 견고한 대안은 `last_semantic` 에 order_id 를 함께 담고 `_emit_semantic` 이 일치할 때만 방출하는 것 — 공유 Guard 의 종목 간 오염까지 함께 막힌다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · **KPI 수치는 오염되지 않는다**(후보 서술 정정) — checked/passed/llm_calls 증가는 `InvoiceSemanticChecker.check` 안에서만 일어나고 차단 건에는 호출되지 않으므로 `engine._semantic_stats` 집계는 정확하다. 결함은 건별 이벤트(GUARD_SEMANTIC)와 화면 로그 한 줄에 국한. `scripts/red_team.py` 도 무영향(`:261` 이 공격마다 새 Guard 생성). 사용자 눈에 보이는 영향은 확인됨 — `app.js:853` 이 `if (d.ok)` 일 때만 로그를 찍는데(주석: 차단 건은 guard_blocked 가 남기므로 중복 회피) 스테일 판정이 ok=true 라 억제가 통하지 않아, 같은 order_id 로 모순되는 두 줄이 남는다. 발화 조건: 두뇌가 붙어 semantic 계층이 존재 + 앞서 최소 1건 통과 + 이후 하드 차단(brain=rule·추세추종·적립식은 semantic=None 이라 무해).

### [BUG-05] partial 매도에서 포지션이 차감되지 않아 총자산이 과대계상된다 — `agents/trading_agent.py:587`
- **심각도 / 렌즈**: 중간 / 정확성
- **증상**: `on_sale_completed` 는 `status == "settled"` 일 때만 `apply_sell` 한다. 그런데 `settle_sale` 의 partial(`broker_agent.py:194-195`)은 `confirmed and not paid` — 여기서 `confirmed` 는 **판매자 주식 전송 tx 의 온체인 확정**(`broker_agent.py:174`)이므로 주식은 이미 지갑을 떠났고 USDC 지급만 실패한 상태다. 주식은 사라졌는데 `position.quantity` 는 그대로 남아 `engine.py:1658·1670` 의 총자산 카드에 실린다. 매수 레그의 partial(돈만 나가고 물건 안 옴 → 포지션 미가산)은 보수적으로 옳지만, 매도 레그는 부호가 반대라 같은 규칙을 쓰면 과대계상이 된다.
- **재현**: `FakeClient([True, False])`(주식전송 확정 / USDC지급 실패)로 `settle_sale` → `status=partial, confirmed=True, 지급 tx=''` → `on_sale_completed` 호출 → 기대: 포지션 0(주식은 온체인 확정 전송으로 나감), 대금 미도착은 유출로만 계상 / 실제: **포지션 0.1680 유지**, 총자산 반영 `gross=31.08 / net=30.99`, 영수증 `confirmed=True, note=''` 로 정상 매도와 구분 불가.
- **근거**: `agents/trading_agent.py:585-591`(열어 확인) · `agents/broker_agent.py:174,188-197` · `web/engine.py:1326`(재검증 진입 조건이 settled/partial 둘 다 confirmed=True 전제)·`1658,1670` · `scripts/test_settlement.py:195-200`
- **수정안**: '주식이 나갔는가'와 '대금이 들어왔는가'를 분리한다 — line 587 을 `if completed.status in ("settled", "partial") and completed.confirmed: self.position.apply_sell(quantity)` 로 넓히고, `credit_sale`(line 591)만 `status == "settled"` 안에 남긴다. **`scripts/test_settlement.py:199`('partial 매도는 포지션 미차감')의 기대값 갱신 필수**(line 200 예산 미환입은 사실이므로 유지). (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 후보 서술 1건 정정 — "`_record_leak` 이 같은 사건을 유출로도 세므로 이중 낙관"은 부정확하다. `guard_leak_usdc` 는 총자산에서 차감되지 않으므로 유출 KPI 는 정확하고 총자산만 낙관적이다(두 숫자가 서로 모순). 이 확인은 오히려 수정안을 뒷받침한다 — 포지션을 차감해도 이중계상이 발생하지 않는다. '심각'이 아닌 근거: 드라이런은 `broker_agent.py:190-191` 로 진입 불가, `engine.py:1371` 이 직후 세션을 정지시켜 오염이 누적되지 않으며, 이 결함 자체가 추가 유출을 만들지 않는다. `_record_leak` 독스트링의 알려진 한계 6건에 없는 **신규 미문서화 결함**이다. ⚠ `engine.py:1332` 경로(기준선 조회 실패로 강등)에서는 실제로 USDC 가 도착했는데 예산 환입도 안 되고 포지션도 남는 이중 왜곡이 되므로, 포지션 차감만으로 그 하위 경로가 완전히 정확해지지는 않는다.

### [BUG-06] 체결되지 않은 판단이 '직전 행동 회고'에 사실처럼 기록돼 다음 틱 프롬프트를 오염시킨다 — `agents/trading_agent.py:248`
- **심각도 / 렌즈**: 중간 / 정확성
- **증상**: `decide()` line 247-248 은 판단이 buy/sell 이기만 하면 `_last_action` 에 기록한다. 그 판단은 이후 402 Guard 차단(`engine.py:1162`)·AP2 거부(`:1169`)·브로커 실패(`:1142-1145`)·수량 0 견적 어느 것으로도 무산될 수 있는데, 무산돼도 기록은 남는다. 다음 틱의 `_retrospective`(line 179-188)가 그것을 '몇 봉 전 매수 @ 얼마'라는 확정 사실로 만들어 `gemini_decider.py:41` 의 [지표] 블록에 싣고, 같은 프롬프트의 [현재 상태](`:48`)는 보유 수량 0 을 말한다 — 모델에게 모순된 컨텍스트가 들어간다. `_decide_trend`(line 374·378)도 진입 전에 미리 기록한다.
- **재현**: `TradingAgent(brain=None)` 에 `preload_history([180]*20)` 후 결제를 한 번도 실행하지 않고 decide 만 호출 → 틱1 `buy | 가격 170 ≤ 매수기준 174.44` 로 `_last_action` 기록(실제 보유 0) → 기대: 회고='이번 세션 매수·매도 이력 없음' / 실제: `2봉 전 매수 @ 170 USDC → 현재가는 그 대비 +7.06%`, 실제 보유·평단 0. trend 모드도 동일.
- **근거**: `agents/trading_agent.py:179-188·225·247-248·374·378`(열어 확인, 저장소 전체에서 `_last_action` 은 110·181·221·248·374·378 6곳뿐이고 되돌리는 코드가 없다) · `agents/gemini_decider.py:41,48` · `web/engine.py:1101,1142-1145,1162,1169,1191`
- **수정안**: 기록 시점을 '판단'이 아니라 '체결'로 옮긴다 — line 247-248 을 지우고 `on_completed`(line 575)·`on_sale_completed`(line 587)의 `status == "settled"` 분기에서 `_last_action` 을 세운다(`_decide_trend` 의 374·378, 시간청산 221 도 함께 제거). 주의 2가지: ①`_retrospective`(`:184`)가 호출마다 `bars_ago += 1` 부수효과를 내므로 봉 카운트 기준(체결 봉=0)을 다시 맞춰야 한다 ②엔진이 심볼별 agent 에 `on_completed` 를 호출하므로 멀티 종목에서 종목별 회고 유지 확인 필요. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 상위 보정 없음(`_sanitize`·`_rule_gate` 는 둘 다 기록 이전 단계). ⚠ 후보 symptom 중 "축② 지표(gemini_share)의 입력이 어긋난다"는 **과장** — `_ai_stats()` 는 판단 출처 카운트만 집계하고 회고 문자열을 쓰지 않으므로 KPI 수치는 오염되지 않는다. 오염되는 것은 프롬프트 컨텍스트뿐이며, 소개서·리포트에 "KPI 가 틀어진다"로 옮겨 적지 말 것. 반대로 후보가 **과소평가**한 부분: `guard.py:265-270` 의 `GUARD_LLM_UNVERIFIED` 때문에 Gemini 일일 한도 소진 세션에서는 매수마다 차단 경로를 타므로, 한 번의 예외가 아니라 세션 내내 회고가 거짓으로 굳는다.

### [BUG-07] 추세추종 멀티에서 예산을 낮추면 슬라이스 잔여가 음수가 되고 AP2 총예산 집행이 깨진다 — `web/engine.py:773`
- **심각도 / 렌즈**: 중간 / 정확성 (후보 신고 '낮음' → **상향**)
- **증상**: `update_limits` 는 총액에 대해서만 `budget_total < spent` 를 검사하고(`engine.py:754`, `_total_spent()` 합산), 멀티 추세 재슬라이스(`:770-784`)는 각 슬라이스가 그 종목이 이미 쓴 금액보다 큰지 확인하지 않는다. 한 종목이 많이 쓴 상태에서 예산을 낮추면 그 슬라이스의 `remaining_usdc` 가 음수가 되고(`ap2_mandate.py:112-113` 은 바닥 처리 없는 단순 뺄셈), `_total_remaining()`(`:1621-1626`)이 음수를 양수와 상계해 화면의 '가용 현금'·'총자산'·추세 수익률이 전부 어긋난다.
- **재현**: 예산 100(슬라이스 50/50) → AAPL 로 45 authorize+settle → `pause("human")` → `update_limits(60, 30)` → 실제: `AAPL 슬라이스예산=30.00 사용=45 잔여=-15.00 / TSLA 잔여=30.00 / 총 잔여=15.00 / total_asset_usdc=15.00 / pnl.return_pct=-75.00`(손실이 없는데 -75% 표시). **이어서 TSLA 로 30 authorize 가 승인**됐고(의도상 추가 가능액 15), 세션 총 사용 75 vs 새 예산 60 = **15 초과**. 대조군(한도 미변경)은 총 사용 95 ≤ 100 으로 불변식 유지 → 재슬라이스가 원인임이 분리 확인.
- **근거**: `web/engine.py:754·770-784·1621-1626·1669-1670·1694-1703`(열어 확인) · `payments/ap2_mandate.py:112-113` · `payments/guard.py`(budget·remaining 검사 전무 — 총예산 집행은 오직 PaymentAuthorizer 책임) · `web/server.py:360-366`(API 도달 경로)
- **수정안**: 재슬라이스 전에 종목별로 검사한다 — `if any(new_slice[s] < agents[s].auth.spent_usdc for s in symbols): raise EngineError(...)`. 사용자 경험상으로는 균등분할 대신 '이미 쓴 금액 + 남은 예산 비례 배분'으로 재산정하는 쪽이 낫다. 어느 쪽이든 `_total_remaining` 이 음수 슬라이스를 `max(Decimal(0), ...)` 로 바닥 처리하도록 방어를 함께 넣을 것. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · **심각도 낮음→중간 상향**. 후보는 표시 왜곡만 봤으나 검증 결과 **AP2 총예산 집행 자체가 깨진다**(15 USDC 초과 승인 실측) — "사용자가 한도를 낮췄는데 그만큼 안 줄어든다"는 402 Guard 헤드라인 주장과 정면으로 어긋난다. '높음' 이상으로 올리지 않은 이유: 추세 멀티는 드라이 전용이라(`engine.py:401-405` 라이브 멀티 거부) 실제 온체인 자금이 나가는 경로가 아니다. 참고로 후보 로그의 `-75.00` 은 '포지션 없이 spent 만 45'인 인위적 상태라 크기가 부풀려져 있다 — 실제 매매 흐름이면 왜곡 폭은 15 USDC(25%p)이고, 예산 초과 15 는 두 경우 동일.

### [BUG-08] 첫 화면 KPI 의 '시도' 분모가 매수만 세면서 매도측 차단은 분자에 넣는다 — `web/engine.py:1775`
- **심각도 / 렌즈**: 중간 / 정확성 (후보 신고 '낮음' → **상향**)
- **증상**: `state_snapshot` 의 guard 블록은 `attempts = guard_block_count + len([t for t in trades if t["side"]=="buy"])` 로 계산한다. 그런데 `guard_block_count` 는 `_sell_cycle` 의 차단(`engine.py:1294`)에서도 증가하고, 성공한 매도는 분모에 전혀 들어가지 않는다. `GUARD_BASELINE_UNREAD` 로 중단된 매수(`:1191-1200`)는 `_complete_trade` 에 도달하지 못해 시도에서 빠진다. 결과적으로 '시도 N건 중 M건 차단'이 서로 다른 모집단을 섞고, 오차 방향이 제품에 유리하다(분모 과소 → 차단율 과대).
- **재현**: 드라이 세션에서 매수 3건 성공 → 매도 청구서 pay_to 를 악성 지갑으로 스왑(red_team `attack_stock_payee_swap` 과 동형) → 가드 차단 1건 → 정상 매도 1건 성공 → 실제: `state_snapshot()["guard"] = {'attempts': 4, 'blocked': 1, 'ap2_rejected': 0, 'leak_usdc': '0'}`. 화면은 "시도 4건 중 1건 차단"이지만 실제 가드를 태운 청구서는 5건(매수 3 + 매도 차단 1 + 매도 성공 1). 기대: '시도'는 가드를 통과시켜야 했던 청구서 전체.
- **근거**: `web/engine.py:1774-1779`·`1163`·`1294`·`1191-1200`(열어 확인) · `web/static/index.html:46-47`·`web/static/js/app.js:378`(첫 화면 최상단 KPI 로 실제 렌더 중 — `data-guard-attempts` span 과 대입 라인 양쪽 확인)
- **수정안**: 가드를 태운 청구서를 세는 카운터를 따로 둔다 — `_buy_cycle` 의 try 진입 직전과 `_sell_cycle` 의 `engine.py:1286` 직전에 `self.guard_checked += 1` 하고 attempts 는 그 값을 쓴다. 그러면 `GUARD_BASELINE_UNREAD`·AP2 거부도 자동으로 분모에 포함된다. blocked 를 buy/sell 로 분리해 노출하면 레그별 대칭이 오히려 증빙이 된다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · **심각도 낮음→중간 상향**. 과거 동류 후보(BUG-10, `docs/reports/bug_latest.md:107`)를 낮음으로 낮춘 근거였던 "프론트가 attempts 를 아직 렌더링하지 않음"이 더 이상 사실이 아니다 — 지금은 `index.html:46` 의 첫 화면 최상단 '지출 시도' KPI 로 표시된다(재확인 완료). 오차 방향이 제품에 유리하고, 매수/매도 양 레그 대칭이 이 제품의 고유 차별점인데 정작 대표 지표가 매도 레그를 비대칭으로 센다. 다만 `ap2_rejected` 는 별도 필드로 노출되므로 후보 서술 중 그 부분은 완화 필요.

### [BUG-09] 끝난 세션의 `_finalize` 가 await 뒤에 전역 상태를 덮어써 '다음 세션'의 긴급정지를 해제한다 — `web/engine.py:1489`
- **심각도 / 렌즈**: 중간 / 엣지케이스
- **증상**: `_finalize` 는 finally 에서 `status="idle"`(`:1471`)을 먼저 세운 뒤 세션 요약 영속저장을 await 하고(`:1476-1481`, 타임아웃 5초), 그 다음에야 `trading_enabled=True`·`pause_info=None`(`:1488-1490`)을 실행한다. status 가 이미 idle 이라 그 await 구간에 새 세션이 `start()` 를 통과할 수 있다(유일한 선점 검사가 `:338` 의 `status != "idle"`, HTTP 층에도 잠금 없음). 결과적으로 끝난 세션 A 의 마무리 코드가 실행 중인 세션 B 의 긴급정지를 해제하고, B 에게 `engine_stopped` 를 쏘며(`:1491`), B 가 이미 비운 trades 를 보고 A 의 종료 브리핑이 조용히 스킵된다(`:1497`).
- **재현**: `save_session` 이 0.5초 지연되는 SlowStore(Firestore 배포본 흉내)로 A `_finalize` 진행 중 B 시작 + `pause("human")` → 실제 로그: `[A finalize 진행중] status='idle'` → `[세션 B 시작] status='running'` → `[긴급정지 직후] trading_enabled=False` → **`[A finalize 완료후] trading_enabled=True pause_info=None`**, 이벤트 `['trading_paused','engine_stopped']`. 기대: B 의 긴급정지 유지.
- **근거**: `web/engine.py:1471-1498`·`338`·`1041,1046`(재생 소진 시 안내를 먼저 방출 → 사용자가 그 직후 시작 가능) · `web/server.py:316-321`
- **수정안**: **⚠ 후보의 1순위 안(`finalizing_sid = self.session_id` 캡처 후 비교)은 이 버그를 못 막는다** — `engine.py:625` 가 session_id 를 초 단위로 만들어 같은 초 재시작이면 A·B 가 동일해지고, 그게 정확히 이 버그의 발생 조건이다(재현에서 A=B=`20260727_132811_dry`). 권장은 대안 쪽: `was_paused` 계산·`trading_enabled`/`pause_info` 리셋·ENGINE_STOPPED emit 을 영속저장 await **앞**(`:1476` 위)으로 옮기고, 브리핑도 `_finalize` 초입에서 `self.trades`/`self.decisions` 를 스냅샷한 목록으로 만든다. 세션 구분자를 쓰려면 id 대신 단조 증가 카운터를 캡처할 것. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 범위를 좁혀 기록한다 — 긴급정지 해제까지 가려면 그 창 안에서 시작과 정지 **두 동작**이 모두 일어나야 한다. 시작만으로도 생기는 피해는 실행 중 B 에게 세션종료 이벤트가 뜨고, ENGINE_STOPPED 페이로드가 B 의 값(trades 0·tick 0)을 A 의 실적으로 보고하며, A 의 종료 브리핑이 누락되는 것. Firestore 저장 요약은 await 전에 A 값으로 계산되므로 영속 데이터는 무오염. `self._task=None`·`last_archive_path` 는 await 앞이라 B 의 stop() 은 정상 동작.

### [BUG-10] 합성 인트라바가 실제 일봉의 고가·저가를 통과하지 않는다(UI 선택지 2/4/8 전부) — `market/price_feed.py:193`
- **심각도 / 렌즈**: 중간 / 정확성
- **증상**: `IntradayReplayFeed._explode` 는 웨이포인트 4개(시가·저가·고가·종가)를 지나는 조각선형 경로를 만들지만, 샘플 지점을 `t=(k+1)/sub` 로만 잡고 각 서브바의 high/low 를 '두 끝점의 max/min'(`:193`)으로만 계산한다. 고가·저가 웨이포인트는 t=1/3·2/3 에 있어 sub 가 3의 배수일 때만 걸리는데, UI 선택지는 2·4·8 뿐이다(`web/static/index.html:162-165`). 그런데 독스트링(`price_feed.py:157`)과 UI 툴팁(`index.html:161`)은 둘 다 "실제 시가·고가·저가·종가를 지나는 결정론적 경로"라고 단언한다. 이 high/low 는 `engine.py:1076`·`608` 로 캔들차트에 나가고, ta_mode 를 켜면 `find_pivots`(`indicators.py:118-119`)의 입력이 된다.
- **재현**: 상승일 실 O/H/L/C = 100.00/110.00/95.00/108.00 → `sub=2: 집계 high 108.00 / low 100.00`(저가 95 로의 눌림이 봉에 0개) · `sub=4·8: 109.50 / 96.25` · `sub=3·6·9: 110.00 / 95.00`(일치). 하락일 110/112/95/100 → `sub=2: 110.00/100.00`, `sub=4·8: 111.50/96.25`. 전 구간에서 마지막 인트라바 종가는 실 일봉 종가와 정확히 일치(종가 경로 정상).
- **근거**: `market/price_feed.py:190-197`·`143-151`·`157`(열어 확인) · `web/static/index.html:161-165` · `web/engine.py:608,1076` · `agents/trading_agent.py:197,176` → `market/indicators.py:118-119` · `scripts/test_intraday.py:47-59`(봉 수·종가 보존·결정론만 검증, **집계 high/low 대조 없음**)
- **수정안**: 각 서브바의 hi/lo 를 두 끝점만이 아니라 그 서브바의 x 구간 안에 들어오는 웨이포인트까지 포함해 계산한다(종가 경로 무변경 → `test_intraday` 기존 4개 축 그대로 통과). 더 단순한 대안은 sub 를 3의 배수로만 허용(UI 3/6/9) — 재현에서 sub=3·6·9 가 실 고가·저가와 일치함을 확인했다. 어느 쪽이든 독스트링(`:157`)과 UI 툴팁(`index.html:161`)이 코드와 일치해야 하며, `test_intraday` 에 '하루 집계 high/low == 실 일봉 high/low' 검증을 추가할 것. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 자금 경로에 닿지 않아 '높음'은 아니다(`get_price` 는 `bar.close` 만 반환, 판단·정산은 전부 종가 기반). '낮음'으로 낮추지 않은 이유 3: ①심사위원이 읽는 UI 툴팁이 검증 가능한 허위 문구다 ②캔들차트가 직접 오염된다 ③ta_mode 를 켜면 판단 입력까지 오염된다. 다만 기본값(일봉 sub=1, `ta_mode=False`)에서는 발현하지 않는다. ⚠ 제목 표현 정정: `_seg_interp` 가 만드는 **연속 경로 자체는 웨이포인트를 통과**한다 — 문제는 이산 샘플 그리드가 그 지점을 건너뛰고 hi/lo 를 끝점만으로 계산하는 것이다(알고리즘 결함이 아니라 '샘플 그리드 vs UI 선택지' 불일치). **코드 수정을 미루더라도 툴팁·독스트링 문구 정정만은 먼저 해두는 편이 낫다**(1분 작업, 심사 신뢰도 직결).

### [BUG-11] `expected_increase_units` 가 0 이면 잔액을 못 읽어도 '도착 확인'으로 통과한다 — `payments/guard.py:408`
- **심각도 / 렌즈**: 낮음 / 엣지케이스
- **증상**: 도착 판정이 `cur - before_units >= expected_increase_units` 라 기대 증가분이 0 이면 항상 참이고, `balance_reader` 가 예외를 내면 `cur = last`(=before_units)로 대체되므로(`:406-407`) 온체인을 한 번도 못 읽은 상태에서도 ok=True 가 나온다. 배송 검증 계층이 '검증했다'고 말하면서 실제로는 아무것도 확인하지 않는다.
- **재현**: `balance_reader` 가 매번 `RuntimeError('RPC 429')` 를 던지게 하고 retries=2 로 호출 → 실제: `ok=True code=OK | 온체인 재조회 확인 — 자산 +0 base units 도착`(before_units=500 이어도 동일). 대조군 expected=1 은 정상적으로 `GUARD_DELIVERY_UNCONFIRMED`. 도달 경로: `trading_agent.py:465-468` 의 `_sanitize` 가 `remaining <= 0` 만 막고 `spend = min(spend_per_trade, remaining)` 로 넘기므로 **예산 꼬리**에서 단일 종목 라이브로 도달한다 — 주가 178 기준 remaining 0.0170 → qty 0.0000 · expected_inc 0(무검증 통과) / 0.0179 → 정상. 0 결제는 예산을 안 깎아 **매수 신호마다 반복**된다. 매도 레그(`engine.py:1328`)도 0.0001주 x $40~$49.99 에서 동일.
- **근거**: `payments/guard.py:402-421`·`150-211`(check_demand 6종에 `amount > 0` 검사 없음) · `agents/broker_agent.py:69-76` · `web/engine.py:1217,1223,1328,1346` · `payments/ap2_mandate.py:115-127`(authorize 에 하한 없음) · `web/broker_service.py:145-147`(HTTP 402 경로는 이미 `quote.quantity <= 0` 을 400 으로 막음)
- **수정안**: 두 곳을 막는다 — ①0/음수 청구서를 서명 대상에서 제외(`broker_service.py:145` 와 대칭이 되게 브로커/엔진 쪽 `engine.py:1125` quote 직후에서 막는 편이 낫다) ②`check_delivery` 진입부에서 `if expected_increase_units <= 0: return self._block_delivery(GUARD_DELIVERY_UNCONFIRMED, "기대 증가분이 0 — 검증 불가")`(매수·매도 양쪽 호출부에 그대로 적용). `guard.py:404-407` 의 `except: cur = last` 는 expected>0 일 때 정상이라 건드릴 필요 없으나, '조회 실패'와 '증가분 부족'을 detail 에서 구분하면 진단이 쉽다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 후보의 도달 경로 주장(멀티 종목 분할)은 **틀렸다** — `check_delivery` 는 live 전용인데 `engine.py:401-405` 가 라이브 멀티를 거부하므로 그 경로로는 도달 불가. 대신 더 현실적인 '예산 꼬리' 경로가 확인됐다. 심각도 낮음 유지(기본 설정에서 실제 이동 금액 0, 매도 노출 $0.005 미만)이나 성격은 무겁다 — 402 Guard 가 RPC 3회 연속 실패 후에도 "온체인 재조회 확인"을 아카이브에 남기고, 예산 꼬리에서는 고착·반복된다(무의미한 온체인 tx + SOL 수수료). **낮음 중 위쪽으로 다룰 것.** 참고: `STOCK_DECIMALS` 를 4 미만으로 주면 `total_usdc>0` 인데 `expected_inc=0` 인 진짜 유출 경로가 열리지만 기본값 6 에서는 도달 불가(설정 의존 가설).

### [BUG-12] 수량 0 견적이 방어 계층을 전부 통과 — 잔여 예산이 줄지 않아 반복된다 — `agents/broker_agent.py:72` **(기존 BUG-08 재발견)**
- **심각도 / 렌즈**: 낮음 / 엣지케이스 (후보 신고 '중간' → 하향)
- **증상**: `spend / (price × (1+fee))` 가 0.0001 미만이면 `ROUND_DOWN` quantize 가 수량을 0.0000 으로 만들고 subtotal·fee·total·amount 가 전부 0 이 된다. 이 0원 청구서는 어디에도 안 걸린다 — Guard 는 청구액==견적이라 통과(`guard.py:186-207` 의 `!=` 정합 검사, 의도상한·건별한도도 `>` 비교라 0 은 무관), AP2 `authorize(0)` 는 4검사 전부 통과 후 `spent += 0`, `verify_payment` 도 0==0 통과 → `status=settled`. 결정적으로 잔여 예산이 그대로라 매수 신호가 뜨는 한 반복된다.
- **재현**: 잔여 0.01·가격 178·30bps 로 실경로(decide→quote→build_payment[Guard+AP2]→settle→on_completed) 3틱 → 모두 `견적수량=0.0000 · 청구액(base)=0 · Guard 차단 0 · AP2 거부 0 · status=settled · 잔여예산 0.01 불변 · spent=0`. 대조군(예산 100)은 100→90→80→70 정상 차감. 실제 `data/market/TSLA_bear.csv` 481틱 세션(dust 0.01)에서 **0-수량 settled 매수 112건** 발생, 잔여·포지션 불변.
- **근거**: `agents/broker_agent.py:70-78` · `agents/trading_agent.py:405,418` · `payments/ap2_mandate.py:115-140` · `payments/guard.py:186-207` · `web/engine.py:1122-1147,1217` · `shared/models.py:33`(`if new_qty > 0` 가드가 0/0 나눗셈 회피 → 크래시 없음) · `web/broker_service.py:145`(HTTP 경로는 이미 400 으로 차단)
- **수정안**: 체결 가능 최소 단위를 명시적으로 거른다 — `trading_agent._decide_by_rule(:418)`·`_decide_dca`·`_decide_trend` 에서 지출액이 '주식 최소단위 × 가격 × (1+수수료)' 미만이면 hold 로 되돌리고 사유에 '잔여 예산이 최소 체결 단위 미만'을 남긴다. 가장 얇은 보강은 `web/engine.py:_buy_cycle` 에서 `quote.quantity <= 0` 이면 조기 종료(=`broker_service.py:145` 를 인프로세스 경로에 이식). (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · **⚠ 중복** — `docs/reports/bug_latest.md:85-91` 의 BUG-08 과 동일 파일·동일 라인(`agents/broker_agent.py:72`)이며 이미 적대검증까지 마친 뒤 미수정 상태로 잔존한다(기존 리포트 원문 대조 완료, 새 건으로 집계하지 말 것). 후보가 새로 보탠 값은 **반복성**이고 이것이 기존 평가("라이브 시 0-금액 SPL 전송 1건")를 뒤집는 유일한 신규 사실이다 — 1건이 아니라 세션당 112건. 다만 "**매 틱** 무한 반복"은 과장이다(매수 신호에 게이트됨; 가격 고정 시 MA5 수렴으로 3틱 만에 신호가 꺼졌다). 정확한 명제는 "매수 신호가 뜨는 매 틱마다, 세션이 끝날 때까지 무제한". 심각도 낮음 유지(이동 금액 0·크래시 없음)이나 한 줄 수정 대비 이득이 커 낮음 중에서는 우선 처리 가치가 있다.

### [BUG-13] 견적 총액이 요청 지출을 최대 0.01 USDC 초과 — 정상 거래가 AP2 '예산 초과'로 거부 — `agents/broker_agent.py:74`
- **심각도 / 렌즈**: 낮음 / 정확성 (후보 신고 '중간' → 하향)
- **증상**: `quote()` 는 수량만 ROUND_DOWN 하고(`:72-73`) subtotal·fee 는 기본 반올림(ROUND_HALF_EVEN)으로 quantize 한다(`:74-75`). 두 번의 올림이 겹치면 `total_usdc` 가 요청 지출을 최대 0.01 USDC 넘어서서 `:70` 주석의 계약("총액이 spend_usdc 를 넘지 않게")이 깨진다. 402 Guard 는 허용치 2센트라 통과시키지만 `PaymentAuthorizer.authorize`(`ap2_mandate.py:130`)의 `amount > remaining` 엄격 비교가 걸려 MandateError 로 무산된다. 추세추종(`trading_agent.py:373`)은 잔여 현금 전액을 지출 의도로 삼아 `spend == remaining` 이라 곧바로 진입 실패이고, 화면엔 안전 계층의 '예산 초과 거부'로 찍혀 오탐처럼 보인다.
- **재현**: 실제 BrokerAgent·OpenPaymentMandate·PaymentAuthorizer(30bps), 예산 185.55·NVDA @ 23.3 → `qty 7.9397 · subtotal 185.00 · fee 0.56 · total 185.56`(요청 +0.01) → `MandateError: 총 예산 초과: 185.56 > 잔여 185.55`. 기대: 수량이 한 틱 더 내려가 체결.
- **근거**: `agents/broker_agent.py:70-78`(열어 확인) · `payments/ap2_mandate.py:130-133,138`(검사가 spent 증가보다 앞 → 예약 누수 없음) · `agents/trading_agent.py:373`(trend 는 `_sanitize` 클램프를 타지 않음) · `web/engine.py:1169-1174` · `payments/guard.py:58-62`+`scripts/test_guard.py:167-172`(이 초과를 이미 인정해 `_INTENT_SLIPPAGE_USDC=0.02` 를 둠 — 완화가 Guard 계층에만 적용되고 AP2 에는 미적용)
- **수정안**: 총액 계산 후 계약을 실제로 강제한다 — `while quantity > 0 and subtotal + fee > spend_usdc:` 로 수량을 한 틱(0.0001) 내려 재계산(subtotal ROUND_DOWN, fee ROUND_UP). ⚠ "fee ROUND_UP + subtotal ROUND_DOWN 이면 초과가 사라진다"는 대체안은 **보장이 아니다** — 표본 125,005 조합에서 초과 0 으로 크게 개선되나 fee 의 ROUND_UP 이 최대 +0.01 을 더하므로 이론적 상한은 여전히 초과 가능. 계약을 확실히 지키려면 clamp 재계산 쪽. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · **후보의 빈도·영향 서술 3건이 재현되지 않아 정정한다**. ①"NVDA_bear 501봉 중 44봉" → 실제 close 기준 **11봉**(open/high/low 다 더해도 24) ②"수수료 50/100bps 에서 더 흔하다" → 같은 조건에서 50bps 185.54(미달)·100bps 185.55(동일), spend 185.00~185.99 대역에서 30bps 0.03% / 50·100bps **0.00%** ③"진입 자체가 무산·반복 실패" → 501 종가 중 초과 11건(97.8% 통과)이라 다음 봉이면 대개 체결된다. 실제 영향은 '1봉 진입 지연 + 정직 거래가 예산 초과 거부로 표시'. 빈도 실측: spend 50~500(0.01 간격) × 21 종가 = 945,021 조합 중 **5건(0.0005%)**, 라운드 금액(30/100/200/500/1000)은 가격 1~600 전 구간 **초과 0** → 기본 설정(spend_per_trade=30)은 면역이고 매도 대금 환입으로 센트가 붙는 추세추종 재진입·예산 소진 직전에만 발동. 그래서 중간→낮음.

### [BUG-14] Gemini 가 `spend_usdc` 에 NaN 을 돌려주면 그 틱 판단이 통째로 유실된다 — `agents/gemini_decider.py:352`
- **심각도 / 렌즈**: 낮음 / 에러처리
- **증상**: `json.loads` 는 비표준 `NaN` 리터럴을 기본 허용한다. `:352` 는 그 값을 `Decimal(str(...))` 로 감싸는데 `Decimal('NaN')` 생성은 성공하므로 `:353` 의 except 가 잡지 못한다. 이 Decision 이 `trading_agent._sanitize(:467)` 의 `d.spend_usdc > 0` 에 닿는 순간 `decimal.InvalidOperation` 이 발생한다(Decimal 순서 비교는 NaN 에서 예외). 문제는 그 호출이 `decide()` 의 try/except 가 아니라 **else 절**(`:241-244`)에 있어 규칙 폴백이 받아주지 못한다는 점이다. 예외는 `engine.py:1056` 의 종목별 격리까지 올라가 그 틱의 판단·매매가 사라진다.
- **재현**: ①`json.loads('{"spend_usdc":NaN}')` → `float('nan')` 통과(코드펜스 섞인 실제 응답 형태로도 `parse_decision_json` 통과) ②`Decimal(str(nan))` → `Decimal('NaN')` 생성 성공(except 미발동) ③NaN spend 를 돌려주는 가짜 두뇌로 `decide()` → 매수신호 충족가·미충족가 **둘 다 InvalidOperation 으로 사망**. **음성 대조군**: 예외를 던지는 두뇌는 정상적으로 `source=rule-fallback` 로 회수 → NaN 경로만 폴백 그물을 빠져나감이 입증.
- **근거**: `agents/gemini_decider.py:349-355` · `agents/trading_agent.py:226-249,462-471` · `web/engine.py:1054-1058`(열어 확인). 범위 검증: `Infinity`/`-Infinity`/`-5`/`9999`/`1e400` 는 클램프 흡수, `null`·`"abc"` 는 `:353` except 회수 → 실제로 터지는 것은 **NaN 하나뿐**.
- **수정안**: `:352` 뒤에 `if not spend.is_finite() or spend < 0: spend = strategy.spend_per_trade_usdc`(is_finite 는 NaN·Infinity 를 예외 없이 걸러낸다). `_sanitize` 이중 방어는 선택. `parse_constant=` 로 리터럴 자체를 거절하는 근본안은 기존 재요청·폴백 흐름과 맞물려 회귀 위험이 조금 더 크다. `-NaN` 에 의존하지 말고 `is_finite()` 로 일괄 처리할 것. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 낮음 유지 — 예외가 `build_payment` 이전 판단 단계라 결제 서명이 시작조차 되지 않고(자금·오정산 없음), 종목별 격리로 세션은 계속 돈다. 트리거는 프롬프트가 내부 생성이라 **공격자 조작 불가**(보안이 아니라 견고성 이슈). 다만 저장소가 이미 `_repair_json`·1회 재요청·정규식 폴백을 둔 것은 형식 위반이 실제로 관측된다는 방증이라 "일어날 수 없는 상황"으로 치부할 근거는 없다. NaN 이 연속되면 매 틱 판단이 조용히 사라져 데모 중 '아무것도 안 하는 에이전트'로 보일 수 있다(데모데이 라이브 리스크).

### [BUG-15] 가격 0 인 봉이 피벗이 되면 `_cluster_levels` 가 0으로 나눠 `ta_summary` 가 크래시 — `market/indicators.py:135`
- **심각도 / 렌즈**: 낮음 / 엣지케이스
- **증상**: `_cluster_levels` 는 `abs(p - out[-1][0]) / out[-1][0]` 로 상대오차를 보는데 분모가 0 이하인지 검사하지 않는다. 가격 목록은 오름차순 정렬되므로 0 이 하나라도 있으면 첫 원소가 되고 두 번째 원소에서 ZeroDivisionError 가 난다. 시세 CSV 에 결손행(저가 0)이 한 줄 섞이면 그 지점이 피벗 저점이 되고 다른 피벗이 하나만 더 있어도 `support_resistance` → `ta_summary` → `TradingAgent.indicators()` 가 예외를 던진다(ta_mode 세션에서 매 틱). `load_bars` 는 0 가격을 거르지 않는다.
- **재현**: ①`ta._cluster_levels([0.0, 10.0, 10.02])` → `ZeroDivisionError: float division by zero`(indicators.py:135) ②저가 0 결손행 + 피벗 고점 1개인 봉 배열로 `support_resistance(bars, Decimal('10'))` → `find_pivots` 가 `([(5,15.0)], [(2,0.0)])` 반환 → 동일 예외. (0 이 유일한 피벗이면 목록 길이 1이라 나눗셈까지 안 가므로 피벗 2개 이상일 때만 터진다 — 그래서 지금까지 안 드러났다.)
- **근거**: `market/indicators.py:131-140`·`143-148`·`375-395` · `agents/trading_agent.py:174-176`(ta_mode 시 매 틱, try 없음) · `market/price_feed.py:76-86`(0·음수 미검사) · `scripts/backtest.py`(`except Exception` 0건 → `--ta` 백테스트는 통째로 사망)
- **수정안**: `_cluster_levels` 에서 기준값이 0 이하이면 병합하지 말고 새 레벨로 시작한다 — `base = out[-1][0]` 를 꺼내 `if out and base > 0 and abs(p - base) / base <= LEVEL_TOL`. 근본 차단은 `load_bars` 에서 0 이하 OHLC 를 ValueError 로 거부(데이터 부서 판정 기준과도 일치). 회귀 방지는 `scripts/test_indicators.py` 에 '가격 0 섞인 봉' 1건. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 실제 영향 제한적이라 낮음 유지 — `data/market/*.csv` 전 행 검사 결과 0 이하 가격 **0건**(현 데이터로는 발화 안 함), `Strategy.ta_mode` 기본 False, 웹 엔진은 `engine.py:1054-1059` 로 종목별 격리(해당 종목만 매 틱 ERROR 후 거래 중단). 자금·오정산은 없고 '결손 데이터 유입 시 조용한 거래 중단(백테스트는 크래시)' 수준. **부수 발견**: 가격이 음수면 `abs(...)/음수` 가 항상 LEVEL_TOL 이하가 되어 무관한 레벨이 전부 병합되는 오작동도 같은 라인에서 발생하며, `base > 0` 조건이 이것도 함께 막는다.

### [BUG-16] 종료일만 지정하면 워밍업 오프셋이 구간을 넘어 '재생 구간이 비어 있습니다'로 죽는다 — `market/price_feed.py:105`
- **심각도 / 렌즈**: 낮음 / 엣지케이스 (후보 신고 '중간' → 하향)
- **증상**: start 미지정 분기에서 재생 시작 인덱스를 `i = min(max(warmup,0), max(len(bars)-1,0))` 로 **먼저** 정하고(`:105`) 그 다음 end 로 j 를 계산해(`:106-108`) `bars[i:j]` 를 자른다(`:109`). i>j 검사가 없어 요청 구간이 워밍업 봉 수(기본 20)보다 짧으면 봉이 실제로 있는데도 슬라이스가 비어 ValueError 가 난다. 오류 메시지가 전체 데이터 범위를 함께 출력해, 요청 날짜가 그 안에 있는데 비었다고 말하는 자기모순이 되고 원인(워밍업 오프셋)을 전혀 가리키지 않는다.
- **재현**: `ReplayPriceFeed('data/market/AAPL_daily.csv', end='2026-03-19', warmup=20)` → `ValueError: 재생 구간이 비어 있습니다 (start=- end=2026-03-19, 데이터 2026-02-27~2026-07-22)`. 해당 CSV 는 100봉이고 2026-03-19 까지 **15봉이 실제로 존재**(i=20, j=15). `scripts/backtest.py --symbol AAPL --to 2026-03-19` 도 종료코드 1·동일 메시지. start 를 함께 주면 정상(15봉·워밍업 0봉) → 데이터는 멀쩡하고 오프셋 계산만 문제. 경계 실측: end=2026-03-26(20봉) 실패 / 03-27(21봉) 정상.
- **근거**: `market/price_feed.py:100-113`(열어 확인) · `scripts/backtest.py:50-51,64,252-258` · `README.md:209`(`REPLAY_START`/`REPLAY_END` 를 각각 선택 항목으로 문서화) · 영향 확대: `IntradayReplayFeed`(`:171` super 호출)·`web/engine.py:164-170`·`run_demo.py:163` 도 같은 생성자
- **수정안**: j 를 먼저 계산한 뒤 워밍업 오프셋을 j 로 클램프한다(`i = min(max(warmup,0), max(j-1,0))`). 그러면 짧은 구간에서 워밍업이 자동으로 줄어들 뿐 재생은 성립한다(start 를 명시하면 이미 warmup 0 으로 재생되는 선례가 있어 기존 동작과 모순되지 않는다). 다만 MA20 이 초반에 성립하지 않아 판단이 달라지므로 클램프 발동 시 "워밍업이 N봉으로 줄었다" 경고를 함께 찍을 것. 남는 진짜 빈 구간에는 현재 메시지를 유지하되 문구를 구분한다. 회귀 방지로 '--to 단독 + 짧은 구간' 케이스 추가(현재 start/end 슬라이싱 전용 테스트가 없다 — `test_intraday.py:73-74` 가 유일한 피드 생성인데 warmup 만 준다). `IntradayReplayFeed` 는 상속만으로 함께 고쳐진다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 중간→낮음 하향: 자금·오정산·잘못된 결과가 없고 예외가 상위에서 반드시 포착돼(`backtest.py:253-258` 안내 + 워크어라운드 한 줄, `engine.py:171-172` EngineError) 조용한 실패가 아니다. 발생 조건도 좁고("start 생략 AND 종료일이 데이터 시작 후 warmup+1봉 이내") README 대표 명령은 영향받지 않는다. ⚠ 후보 주장 정정: 멀티 종목 경로(`backtest.py:102-111`)는 "문제가 없다"기보다 같은 입력을 `[오류] 공통 구간이 너무 짧습니다(15봉) — 워밍업 20봉 이후 재생할 봉이 없습니다.` 로 **정확하게** 거부한다 — 즉 단일 경로만 원인을 오도한다는 취지는 맞다.

### [BUG-17] 열이 잘린 CSV 행에서 TypeError 가 포장되지 않아 문제 행 정보가 사라진다 — `market/price_feed.py:85`
- **심각도 / 렌즈**: 낮음 / 에러처리
- **증상**: `load_bars` 는 `except (KeyError, ArithmeticError, ValueError)` 로 형식 오류를 잡아 '어느 파일 어느 행'을 담은 ValueError 로 바꾸도록 설계돼 있다. 그런데 `csv.DictReader` 는 열이 모자란 행의 빠진 키를 `restval`(기본 None)로 채우므로 `Decimal(None)` → **TypeError** 가 나고, 이 타입은 except 튜플에 없어 그대로 전파된다. 결과는 `conversion from NoneType to Decimal is not supported` 한 줄뿐 — 문제 행이 없다. 다운로드 중단·디스크 부족으로 마지막 행이 잘리는 것은 흔한 실패다.
- **재현**: 임시 CSV 에 `2026-01-05,100,101`(열 부족) → `TypeError: conversion from NoneType to Decimal is not supported`. 대조군 `2026-01-05,,,,,`(빈 값) → 설계대로 `ValueError: CSV 형식 오류(...Y_daily.csv): {'date': '2026-01-05', 'open': '', ...} — [ConversionSyntax]`. 즉 '열 부족 행'만 포장을 빠져나간다.
- **근거**: `market/price_feed.py:74-86`(열어 확인) · `scripts/collect_dataquality.py:247-258` · 직접 호출자 `scripts/backtest.py:95`·`explore_strategy.py:264`·`explore_trend.py:128`(전부 그대로 받음) · `scripts/test_*.py` 어디에도 load_bars 오류 경로 검증 없음(사용처는 `test_trend.py:132` 의 정상 로드뿐)
- **수정안**: `csv.DictReader(f, restval="")` 로 두면 빠진 값이 빈 문자열이 되어 기존 ArithmeticError 경로로 떨어지고 지금 메시지가 그대로 나온다(한 글자 수정, 대조군이 그 경로를 실측으로 보여줌). 대안은 except 튜플에 TypeError 추가. 어느 쪽이든 '열 부족 행' 케이스를 테스트에 1건 넣을 것. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · ⚠ 후보 과장 1건 정정 — "데이터 부서 리포트에서 좌표가 안 나온다"는 절반만 맞다. `collect_dataquality.py:258` 이 `except Exception` 으로 TypeError 도 잡고 감싸는 `inspect_csv` 반환 dict 이 `:284` 에서 `"file": rel` 을 담으므로 **파일 경로는 리포트에 남는다**(잃는 것은 '어느 행'뿐, 스캐너 크래시도 없음). 낮음 타당 — 어느 예외든 로드는 어차피 실패하고 손해는 진단 품질(마감 직전 원인 추적 시간)뿐이다.

### [BUG-18] `tick_interval_sec=NaN` 이 안전범위 클램프를 통과해 틱 루프가 폭주한다 — `web/engine.py:654`
- **심각도 / 렌즈**: 낮음 / 엣지케이스 (후보 신고 '중간' → 하향)
- **증상**: `min(max(_ti, 0.05), 60.0)` 은 NaN 에 무력하다(모든 비교가 False). NaN 이 `_run_loop` 의 `asyncio.wait_for(..., timeout=self.tick_interval)`(`:1013`)에 들어가면 대기가 즉시 끝나 루프가 초당 수만 회 돈다. 매 회전마다 `_process_symbol` → `agent.decide` 가 호출되므로 ①CPU 포화(배포는 max-instances 1·no-cpu-throttling 이라 대시보드 전체가 먹통) ②재생 피드 즉시 소진 ③brain=gemini 면 일일 한도 몇 초 만에 전소.
- **재현**: ①`json.loads('{"tick_interval_sec": NaN}')` → `nan` ②`StartBody(**dict).tick_interval_sec` → `nan`(pydantic 2.13.4 통과, 422 아님) ③실제 HTTP 왕복(FastAPI TestClient, 본문 `{"tick_interval_sec": NaN}`) → **200**, 클램프 결과 `nan` ④0.3초 회전 측정: timeout=nan **14,556회** / 8.0 → 1회 / 0.05 → 6회. **추가 발견**: NaN 이 `state_snapshot`(`:1728`)에도 실려 Starlette `JSONResponse`(`allow_nan=False`)가 ValueError → `/api/state` 가 **500**.
- **근거**: `web/engine.py:648-654`(`:650-653` 의 try/except 는 `(TypeError, ValueError)` 만 잡음)·`:1013`·`:1728` · `web/server.py:309`(`float | None = None`, `allow_inf_nan`·`ge/le` 없음; `StrictBody` 는 `extra="forbid"` 만) · 저장소 전체 grep 결과 유한성 검증 지점 0
- **수정안**: 클램프 앞에 `if not math.isfinite(_ti): _ti = CFG.web_tick_interval_sec`. 또는 `StartBody.tick_interval_sec` 을 `Field(default=None, ge=0.05, le=60, allow_inf_nan=False)` 로 선언해 422 로 거르고 엔진 클램프는 이중 안전장치로 남긴다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 중간→낮음 하향: 자금·오정산·크래시가 아니고 402 Guard 결선은 그대로 작동한다. **UI 로는 트리거되지 않는다** — `app.js:1012` 가 `parseFloat` 값을 넣지만 `JSON.stringify` 가 NaN 을 `null` 로 바꿔 서버는 기본값을 받는다(사고성 유입 경로 아님, NaN 리터럴을 손으로 넣어야 함). 배포본은 `control_token` 이 설정돼 있어 `require_control` 뒤라 외부 공격자는 못 부른다(무인증은 CONTROL_TOKEN 미설정 로컬뿐). 복구도 `POST /api/engine/stop` 1회. 그래도 한 줄 수정이면 끝나고 데모데이 오조작 시 대시보드 먹통(+`/api/state` 500) 비용이 커 처리 가치는 있다.

### [BUG-19] 적립식 회당 금액이 NaN 이면 500 으로 새어나가고 Infinity 면 세션이 그대로 시작된다 — `web/engine.py:385`
- **심각도 / 렌즈**: 낮음 / 엣지케이스 (후보 신고 '중간' → 하향)
- **증상**: `Decimal(str(...))` 는 "NaN"·"Infinity" 를 예외 없이 만들어 내는데(`:378-386` 의 try 는 `(ValueError, InvalidOperation)` 만 잡음) 다음 줄 `if dca_amount <= 0:` 이 NaN 비교에서 `decimal.InvalidOperation` 을 던진다. `server.py:316-324` 는 EngineError 만 잡으므로 **500** 으로 나간다. Infinity 는 `<= 0` 을 False 로 통과해 세션이 실제로 시작되고, 멀티 종목이면 `engine.py:512` 의 `(dca_amount/n).quantize` 에서 곧바로 500. 형제 경로 `update_limits`(`:729-734`)는 이미 `is_finite()` 로 같은 함정을 막고 있어 검증 규칙이 한쪽에만 빠져 있다.
- **재현**: `start("dry", {"type":"dca","dca_unit":"ticks","dca_every_ticks":1,"dca_amount_usdc":"NaN"}, {"type":"mock"}, autostart=False)` → 기대 EngineError(400) / 실제 `decimal.InvalidOperation → HTTP 500`. `"Infinity"` → 예외 없이 세션 시작, `strategy_info.dca_amount_usdc='Infinity'`. 멀티(`symbols:["AAPL","TSLA"]`) + Infinity → 500. 음성 대조: `"-0"`·`"abc"` → 400, `"10"` → 정상.
- **근거**: `web/engine.py:378-386`·`:512`·`:729-734`(주석에 이 함정을 명시) · `web/server.py:283`(`dca_amount_usdc: str = "10"`)·`:316-324`
- **수정안**: `is_finite()` 선검사를 **파싱 직후(`engine.py:381` 바로 뒤)**에 둔다 — `if strat_type == "dca":` 블록 안에만 넣으면 `type="condition"` + `dca_amount_usdc="Infinity"` + 멀티 조합에서 `:512` 의 500 이 그대로 남는다. `update_limits` 의 검사와 같은 헬퍼(`_finite_decimal(name, raw)`)로 묶어 두 경로가 갈라지지 않게 할 것. 회귀는 `-0`·`abc`·`10` 음성 대조로 확인. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · **후보 서술 1건이 재현되지 않아 하향**: "Infinity 면 매 틱 견적 quantize 에서 InvalidOperation 이 나 ERROR 이벤트만 쏟아내는 좀비 세션"은 사실이 아니다 — 단일 종목 10틱 실행 결과 ERROR 0건이고 매 틱 `hold, reason="적립 보류 — 잔여 예산 100 < 정액 Infinity USDC"` 로 정상 보류한다(AP2 예산 비교가 먼저 걸러 매수 경로에 도달하지 않음). 결과는 오류 폭주가 아니라 "아무것도 사지 않는 무해한 세션". 자금·오정산·크래시 없고 `require_control` 게이트 뒤라 낮음이 정확하다. 실제 피해는 ①400 이어야 할 입력이 500 으로 나가는 오류 위생(라이브 URL 인상) ②Infinity 세션이 조용히 0건 체결해 사용자가 원인을 모르는 혼란.

### [BUG-20] 세션 중 한도 변경이 진행 중인 결제 예약을 버려 실패한 결제의 예산이 영구 소모된다 — `web/engine.py:786` **(기존 BUG-11 재발견)**
- **심각도 / 렌즈**: 낮음 / 정확성
- **증상**: `update_limits` 는 새 `PaymentAuthorizer` 를 만들어 `spent_usdc` 만 이월하고 `_reservations` 는 옮기지 않는다(`:786-790`, 추세 멀티는 `:770-784`). 반면 `_buy_cycle` 의 finally 는 `agent.auth.release(order_id)` 로 실패 결제를 원복하는데(`:1251`), 교체된 새 auth 에는 그 예약이 없어 release 가 `Decimal(0)` 을 반환하고 조용히 no-op 한다(`ap2_mandate.py:147-151`). 이월된 spent 에는 그 예약분이 이미 포함돼 있으므로 실패한 결제 금액만큼 예산이 영구 차감된 채 남는다. 방향은 보수적(과소 지출)이지만 잔여 예산 표시와 실제가 어긋난다.
- **재현**: `authorize("ord_inflight", 30)` → `spent=30 remaining=70 reservations={'ord_inflight':30}` → `pause("human")` → `update_limits(100, 50)` → `교체 후 reservations={}` → `release("ord_inflight")` → **반환액=0 · spent=30 remaining=70**(기대 반환 30·spent 0·remaining 100). 대조군(한도 변경 없이 release)은 **반환액=30 · spent=0 remaining=100** 정상 → 원인이 auth 교체임이 분리 확인. 추세 멀티 경로도 동일. 인터리빙 창: `update_limits` 즉시적용 분기가 `status=="running" and not trading_enabled`(`:750-752`)를 요구하는데 `pause()` 가 그 상태를 만들고, `_buy_cycle` 은 `await self._broker.settle(...)`(`:1209`)에서 루프를 양보한다.
- **근거**: `web/engine.py:753-791`·`:770-784`·`:1191`·`:1246-1251` · `payments/ap2_mandate.py:142-155`(열어 확인)
- **수정안**: `PaymentAuthorizer` 에 `carry_over_from(other)` 를 두고 `spent_usdc`·`_reservations` 를 함께 옮긴다(사유 필드 직접 대입보다 두 분기·향후 필드 추가에 안전). 또는 진행 중 예약이 남아 있으면 한도 변경을 409 로 거부. **추세 멀티 분기(`:770-784`)와 단일 분기(`:786-790`)를 함께 고칠 것**이며, 같은 노출이 `:1191`(기준선 조회 실패 → release)에도 있다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · **⚠ 중복** — `docs/reports/bug_latest.md:109-115` 의 BUG-11 과 동일 결함(앵커는 멀티 종목 리팩터로 `505`→`786`, `819`→`1251` 이동)이며 당시에도 reproduced=true·낮음으로 판정된 뒤 미수정 잔존(기존 리포트 원문 대조 완료). '이미 알려진 한계'로 반려할 성격이 아니라 '알려졌으나 고치지 않은 실제 결함'이다. 후보가 기존 리포트보다 나아진 점: 추세추종 멀티 분기도 같은 결함을 공유함을 짚었고 재현으로 확인됨. 트리거는 라이브 인플라이트+정지+한도 변경이 겹칠 때로 좁다.

### [BUG-21] `session_id` 가 초 단위라 같은 초에 시작한 두 세션이 Firestore 문서를 덮어쓴다 — `web/engine.py:625`
- **심각도 / 렌즈**: 낮음 / 엣지케이스
- **증상**: `session_id = strftime("%Y%m%d_%H%M%S") + f"_{mode}"` 이므로 1초 안에 두 세션을 시작하면 id 가 완전히 동일해진다. `store.save_session` 은 `document(session_id).set()`(`web/store.py:106`)이라 merge 없이 통째로 치환되고, `save_trade` 의 문서 id 는 `f"{session_id}_{order_id}"`(`:111`)에 `session_id` 필드까지 심어(`:113`) 두 세션의 체결이 한 세션에 섞인다. `/api/history/sessions` 로 보여줄 증빙이 조용히 사라진다.
- **재현**: 초 경계에 맞춰 실제 `start → _tick_once → _finalize` 경로로 두 세션 실행 → A id = B id = `20260727_132913_dry`, 세션 문서가 기대 2개가 아니라 **1개**. 내용 대조: A 저장 직후 `ticks=3, trade_count=3` 이던 문서가 B 종료 후 `ticks=1, trade_count=1` 로 바뀌어 **A 요약 완전 소실**. 거래 4건 전부가 같은 session_id 를 달아 조회 시 두 세션이 섞임(문서 id 는 order_id 가 uuid4 라 덮이지 않고 귀속만 뒤섞임).
- **근거**: `web/engine.py:625` · `web/store.py:105-113`(열어 확인) · `web/server.py:241-244` · session_id 전수 grep 결과 충돌 방지 로직 0
- **수정안**: `strftime("%Y%m%d_%H%M%S") + f"_{mode}_{uuid.uuid4().hex[:6]}"` 또는 마이크로초(`%f`) 포함. 표시용 라벨이 필요하면 별도 필드로 두고 문서 키만 고유하게 한다. 참고: `store.py:116` 의 `save_briefing` 도 같은 종류의 충돌 여지가 있다(별건). (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · ⚠ 인과 서술 정정 — 충돌 조건은 "A 종료 직후 재시작"이 아니라 **두 세션의 시작 시각이 같은 초**여야 하므로 세션 A 자체가 1초 안에 끝나야 한다. 정상 경로 재현(세션당 약 0.75초)은 초 경계를 넘겨 충돌하지 않았다. 재생 소진 자동종료는 수백 틱이라 그 시나리오로는 발화하지 않고, 봉이 매우 적은 피드·시작 직후 정지 후 재시작·스크립트 호출이 실제 발화 경로다(`engine.py:339` 가드가 동시 세션은 막으므로 순차 같은 초 한정). 로컬 기본은 NoopStore 라 무영향이고 Firestore 가 켜진 배포본에서만 증빙이 사라진다. 발화하면 예외 없이 조용히 사라지고 수정이 한 줄이라 '정보'로 낮추지도 않았다.

### [BUG-22] 라이브 모드에서 `start()` 동시 호출이 중복 세션을 만든다(전역 싱글턴 엔진) — `web/engine.py:593`
- **심각도 / 렌즈**: 낮음 / 엣지케이스
- **증상**: `start()` 는 `status != "idle"` 검사를 맨 앞에서 하지만(`:338-339`), 라이브 경로는 status 를 "running" 으로 세우기(`:593`) 전에 `await x.get_client(...)`(`:581`)·`await snapshot_balances(...)`(`:582`)로 실제 RPC I/O 에서 이벤트 루프를 양보한다. 그 사이 두 번째 `POST /api/engine/start` 도 idle 검사를 통과해 두 개의 `_run_loop` 태스크가 같은 전역 상태(`_client`·`agents`·`trades`)를 공유한다. 드라이 경로는 status 설정 전에 await 이 없어 무영향(`:338~593` 구간의 await 은 581·582 둘뿐).
- **재현**: 네트워크 I/O 경계(get_client·snapshot_balances)와 지갑 키만 주입하고 start() 본문은 원본 그대로 실행 → `asyncio.gather` 로 `start("live")` 2회 → **성공 2건·거부 0건**(기대 1/1), snapshot_balances 2회 호출. 대조군 `start("dry")` 2회는 성공 1·거부 1(`EngineError: 엔진이 이미 실행 중입니다`)로 정상 → 하니스 충실성 확인 + 드라이 무영향 확인. `autostart=True` 로는 `_run_loop` 태스크 2개 생존, `self._task` 는 나중 것만 가리킴.
- **근거**: `web/engine.py:338-339·579-593·687-697`(열어 확인) · `web/server.py:316-321`(async 엔드포인트라 동시 요청이 같은 루프에서 교차) · `config.py:83`(ALLOW_LIVE_FROM_WEB 기본 0) · `web/static/js/app.js:1008`(fetch 앞에 동기적으로 버튼 비활성 → 한 탭 더블클릭은 막힘)
- **수정안**: **⚠ 후보의 1안(맨 앞에서 `status="starting"` 점유)은 위험하다** — `:338~593` 사이 raise 경로가 매우 많아(341·345·353·357·361·368·376·402·423·442·465·474·514·587 등) 하나라도 복원을 빠뜨리면 엔진이 "starting" 에 영구히 갇혀 원래 버그보다 나빠진다. `self._start_lock = asyncio.Lock()` 으로 start/stop 을 감싸는 2안을 권한다(1안을 쓸 거면 try/finally 로 전 경로 감쌀 것). (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true(라이브 하니스 — 네트워크 경계만 대체) · 후보 설명 2건 정정 — ①"stop() 이 앞 루프를 정리하지 못한다"는 부정확하다. `_run_loop` 는 매 회차 `self._stop_event` 를 다시 읽으므로(`:1007,1013`) 두 번째 start 가 `:668` 에서 갈아끼운 새 Event 를 두 루프가 함께 보게 되어 stop() 으로 둘 다 빠져나온다. 실제로 깨지는 것은 stop() 이 뒤 태스크만 await 하는 사이 `_finalize`(`:1444`)가 두 번 도는 것(아카이브 이중 기록·같은 session_id 로 save_session 중복·ENGINE_STOPPED 중복·`_client.close()` 경합). ②후보가 놓친 더 큰 영향: 실행 중 두 루프가 같은 `agents`·feed 를 틱해 재생 피드 2배 소비, 같은 TradingAgent 에서 `_buy_cycle` 동시 실행(라이브면 실제 온체인 매수 중복). **가드는 뚫리지 않는다** — 모든 결제가 여전히 AP2·Guard 를 통과한다. 도달 조건이 좁다(ALLOW_LIVE_FROM_WEB=1 + require_control 통과 + 탭·클라이언트 2개 또는 직접 API 호출).

### [BUG-23] SSE 구독자 큐가 차면 이벤트를 조용히 버리고 클라이언트는 유실 사실을 얻지 못한다 — `web/events.py:76`
- **심각도 / 렌즈**: 낮음 / 에러처리
- **증상**: `emit` 은 `put_nowait` 의 QueueFull 을 `pass` 로 삼키며 "재접속 시 since() 히스토리로 복구"라고 주석했지만, 연결이 끊기지 않은 클라이언트는 재접속할 이유가 없다. 네트워크가 잠깐 정체돼 큐(maxsize=500)가 차면 그 구간 이벤트는 영구히 사라지고 이후 이벤트는 다시 정상 도착해 화면이 '멀쩡해 보이는 채로' 어긋난다.
- **재현**: `bus=EventBus(); q=bus.subscribe(); for i in range(520): bus.emit('trade',{'n':i})` → 수신 500건(n=0~499), n=500~519 유실, **유실 신호 이벤트 0건**, 히스토리에는 520건 잔존. 큐를 비운 뒤 emit 한 신규 3건은 정상 도착(조용한 갭 후 정상 재개). 1600건 emit 시 히스토리 최소 id 가 601 로 밀려 id 600 이전은 새로고침해도 복구 불가.
- **근거**: `web/events.py:60,73-78,81`(열어 확인) · `web/server.py:392-404`(유실 감지·통지 코드 없음) · `web/static/js/app.js:927`(`lastEventId = evt.id` 로 비교 없이 대입만 — 갭 검사 없음, 주기 폴링도 없음)
- **수정안**: 유실을 이벤트로 만든다 — 구독자별 `dropped_from_id` 를 기록해 다음 전송에 함께 흘리는 쪽을 권한다(`get_nowait()` 로 가장 오래된 항목을 버리는 방식은 유실 지점을 앞으로 옮길 뿐이다). 프론트는 그 신호를 받으면 `?since=<마지막 id>` 로 재연결하면 된다. 참고로 `app.js:927` 에서 `evt.id !== lastEventId + 1` 만 검사해도 서버 수정 없이 감지는 가능하다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · ⚠ 후보 과장 정정 — "심사 증빙이 사라진다"는 절반만 맞다. 가드 KPI 카드는 이벤트가 아니라 `GET /api/state.guard` 에서 렌더되고(`app.js:377-383`), `guard_blocked` 핸들러가 `fetchState()` 를 호출하므로(`app.js:841` 외 다수) **살아남은 다음 이벤트 하나가 KPI 를 서버 값으로 되맞춘다**. 영구 유실되는 것은 증분 append 인 활동 로그 줄·거래 테이블 행(`app.js:646`)·브라우저 알림이다(데모상 아픈 지점은 "402 Guard 차단 … 유출 0" 빨간 로그가 서사에서 빠지는 것). 발생 조건이 비정상 경로다 — 500건이 밀리려면 **끊기지 않은 채 수 초간 전송 정체**가 필요하고, 새로고침하면 최근 1000건이 재전송돼 복구된다. 큐 상한 자체는 의도된 트레이드오프이며 진짜 결함은 **주석이 근거로 든 복구 경로가 성립하지 않는데도 그 전제 위에서 조용히 버린다**는 점. 8/3 마감 대비 우선순위는 낮다.

### [BUG-24] 숫자형 환경변수를 빈값으로 주입하면 임포트 단계에서 앱 전체가 죽는다 — `config.py:55`
- **심각도 / 렌즈**: 낮음 / 에러처리
- **증상**: `Config` 의 필드 기본값은 클래스 정의 시점에 평가되고 숫자 필드는 `Decimal(_get(...))`·`int(_get(...))` 로 곧바로 변환한다. `_get`(`:35-36`)이 `os.environ.get(key, default)` 라 '키는 있고 값은 빈 문자열'이면 기본값이 아니라 `""` 를 돌려주므로 변환이 터지고, config 를 임포트하는 모든 진입점(web.server·run_demo·scripts/*)이 부팅 전에 죽는다.
- **재현**: `BUDGET_USDC= python -c "import config"` → `decimal.InvalidOperation: [ConversionSyntax]`(config.py:55) · `MAX_HOLD_BARS=` → `ValueError: invalid literal for int() with base 10: ''`(:60) · `PORT=` → 동일(`web/server.py:416`). `MAX_BUDGET_USDC=` 빈값으로 `import web.server`·`import run_demo` 둘 다 `config.py:79` 에서 사망. 트리거 현실성: python-dotenv 가 `.env` 의 `BUDGET_USDC=` 를 `''` 로 실제 주입함을 확인했고, `.env.example` 은 10개 키를 `KEY=` 형태로 쓰고 있어(현재는 전부 문자열 필드라 안 터질 뿐) 이 저장소 자체의 관례다. 배포는 `docs/deploy_cloud_run.md:147` 이 `--set-env-vars` 안에 숫자형 `MAX_BUDGET_USDC=1000` 을 콤마 구분으로 넣는다.
- **근거**: `config.py:35-36·55-60·79` · `web/server.py:416`(열어 확인) · `.env.example:14,28,29,31,36,44,48,53,54,55` · `docs/deploy_cloud_run.md:147`
- **수정안**: **⚠ 후보의 한 줄 안 `return os.environ.get(key) or default` 는 이 저장소에서 보안상 역효과가 있다** — 배포가 `MAX_BUDGET_USDC=1000` 으로 낮춘 서버측 예산 상한이 빈값일 때 조용히 기본값 10000 으로 되돌아간다(런북 170행이 설명한 통제가 10배 헐거워진 채 무경고 기동). 이 필드에 한해서는 현재의 fail-fast 가 오히려 안전하다. 따라서 후자 안만 채택할 것 — `_dec(key, default)`·`_int(key, default)` 헬퍼를 만들어 변환 실패 시 `raise SystemExit(f"환경변수 {key} 값이 잘못됐습니다: {raw!r}")` 로 변수명을 밝힌다. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true(3건) · dataclass 필드 기본값이 클래스 정의 시점에 평가된다는 전제도 트레이스백 프레임으로 확인. 낮음 유지 — 운영자 오타가 있어야 발동하고, 거래 중이 아니라 부팅 시점에 즉시·요란하게 실패하며(자금·오정산 없음), Cloud Run 은 새 리비전 기동 실패 시 직전 정상 리비전을 계속 서빙한다. ⚠ 후보 증상 중 "어느 환경변수가 문제인지 화면에 안 나온다"는 과장 — 트레이스백 마지막 프레임이 소스 라인을 그대로 출력해 변수명이 보인다(예외 타입이 불친절할 뿐 진단 불가는 아님). 문자열 필드는 빈값이 일관되게 '미설정=기본값'으로 취급되는데 숫자 필드만 크래시하는 불일치가 본질이다.

### [BUG-25] `PaymentRequirements.to_dict/from_dict` 가 x402 와이어 포맷과 어긋난 채 사문화 — `shared/a2a_messages.py:31`
- **심각도 / 렌즈**: 정보 / 커버리지
- **증상**: 모듈 독스트링(`:8-9`)은 "각 메시지는 to_dict/from_dict 를 제공한다 … 추후 A2A/HTTP 전송에 그대로 사용"이라고 선언한다. 그런데 실제 HTTP 레그는 이것들을 쓰지 못하고 `payments/x402_http.py` 가 별도 직렬화기를 정의했다 — 금액 필드 이름이 다르기 때문이다(a2a 는 `amount`(`:37`), x402 스펙·파서는 `maxAmountRequired`). 현재 `PaymentRequirements.to_dict`(`:31`)/`from_dict`(`:44`) 는 저장소 어디에서도 호출되지 않는 사문화 코드이고(`PaymentPayload` 쪽만 `x402_http.py:130·147` 에서 쓰인다), 독스트링만 보고 나중에 이걸로 402 본문을 만들면 상대 구현이 스펙 필드를 못 찾는다.
- **재현**: `PaymentRequirements(...).to_dict()` 키 = `['amount','asset','decimals','network','payTo','resource','scheme','x402Version']` → 같은 저장소 파서 `parse_payment_required({'x402Version':1,'accepts':[r.to_dict()]})` 가 `X402ProtocolError: accepts[0] 형식 오류: KeyError: 'maxAmountRequired'` 로 거부. 기대: 같은 저장소의 두 직렬화기가 호환.
- **근거**: `shared/a2a_messages.py:8-9·31-49`(열어 확인 — 후보가 붙였던 앵커 38행은 실제로 `"payTo": self.pay_to` 라 블록만 맞고 행이 어긋나 있었다. 이 리포트는 `def to_dict` 실제 행인 **31**로 정정했다) · `payments/x402_http.py:45-72,56,170` · 앱 코드(agents·payments·market·web·shared·scripts·config.py·run_demo.py) 전수 grep 결과 `PaymentRequirements` 의 두 메서드 호출처 **0건**(x402_http.py:130·147 은 `PaymentPayload`, web/store.py 는 Firestore 스냅샷의 동명 메서드로 무관)
- **수정안**: 단일 소스를 명확히 한다 — (a) `PaymentRequirements` 의 to_dict/from_dict 를 삭제하고 독스트링 `:8-9` 를 "와이어 직렬화는 `payments/x402_http.py` 가 단일 소스"로 정정(현 구조와 일치하므로 권장). (b) 유지한다면 키를 `maxAmountRequired` 로 맞추고 `x402_http.accepts_entry` 가 이 메서드를 호출하도록 통합. (제안 — 자동 적용 안 함)
- **적대 검증**: reproduced=true · 런타임 영향 0(호출처 없음)이라 '정보'가 정확하다. **후보가 오히려 과소 보고한 부분**: 독스트링은 "각 메시지"라 했으나 봉투 데이터클래스 3종(`PaymentRequired`·`PaymentSubmitted`·`PaymentCompleted`, `:80-107`)은 두 메서드를 아예 갖고 있지 않다 — 문서와 실제의 괴리는 보고된 것보다 넓다. **표현 정정**: `to_dict` 는 항목 안에 `x402Version` 을 담고 있는데 accepts[] 항목은 그 키를 갖지 않으므로(`x402_http.py:79-83`), 정확한 명제는 "스펙 필드명을 안 쓴다"보다 **"어떤 와이어 포맷에도 대응하지 않는 미사용 직렬화기"**다. ⚠ fix_proposal 말미의 "제출물에서 x402 스펙 준수를 말하는 만큼 리뷰 리스크"는 검증 가능한 사실이 아니라 심사 관점 의견이므로 결함 근거로 인용하지 말 것.

## 반려된 후보 (오탐 방지 기록)

- **컨펌 결과를 파싱하지 못하면 결제를 '성공'으로 반환한다(`ok` 기본값 True + except pass)** — `payments/x402_solana.py:347` — 반려 사유: 라인 자체는 주장대로이나(`ok = True` 시작 + `except Exception: pass`) 서술된 실패 모드가 실제 의존성에서 **도달 불가**다. `.venv/Lib/site-packages/solana/rpc/async_api.py:1216-1230` 을 열어 확인한 결과, `confirm_transaction(sig, commitment=Confirmed)` 는 `last_valid_block_height` 없이 호출되어 else 분기를 타는데 이 루프는 `resp.value[0] is not None` **이면서** `confirmation_status >= Confirmed` 일 때만 반환하고, 90초 안에 못 읽으면 `UnconfirmedTxError` 를 raise 한다(`time` 을 앞당겨 실증). 즉 **반환되면 반드시 판독 가능**하고 **못 읽으면 예외**다. `value=[]` 도 라이브러리가 `resp.value[0]` 를 먼저 만져 IndexError 를 내고, 이 예외들은 `_is_transient`(`:247`)에 걸리지 않아 `rpc_retry` 가 즉시 전파한다 → `ok=True` 반환이 아니라 예외로 빠진다. 스텁 클라이언트를 주입하는 실행 경로도 없다(라이브는 전부 진짜 `AsyncClient`, 드라이런은 `client is None` 이라 호출 자체가 안 됨). **잠재적 견고성 문제(뒤집힌 기본값)이지 현재 결함이 아니다.** 후보의 "확실" 신뢰도는 라이브러리 계약 미확인의 결과. (real=false, 조정 심각도=정보) — 다만 `ok=False` 로 뒤집는 1줄은 무해하고 solana-py 업그레이드 대비 가치가 있어 반대하지 않는다.
- **`PaymentCompleted.status` 기본값이 'settled' — 실패가 성공으로 읽히는 방향의 기본값** — `shared/a2a_messages.py:105` — 반려 사유: 사실관계는 전부 정확하고 재현도 성공했으나(status 생략 시 'settled' 출력) **기본값이 실행되는 경로가 하나도 없다**. 전 저장소 grep 결과 `PaymentCompleted(` 생성 지점은 `broker_agent.py:145·159·199·223·237·264` + `x402_http.py:109` + `test_guard.py:287·306` 뿐이고 전부 status 를 명시한다(145/159/223/237 은 "failed" 하드코딩, 264 는 조건식, 199 는 3분기 계산값). 후보 자신도 "지금 실제로 깨지는 곳은 없다"고 인정한 대로 결함이 아니라 **방어적 기본값 개선 제안**이다. 라이브 매수 레그는 `engine.py:1223` 의 `check_delivery` 가 2차 그물로도 있다. (real=false, 조정 심각도=정보) — `status: str = "failed"` 로 뒤집는 변경은 **동작 변화가 수학적으로 0**이고 dataclass 필드 순서 제약도 건드리지 않으므로, 다른 파일을 열 일이 있을 때 곁다리로 처리하면 충분하다(마감 전 우선순위 낮음).

## 스캔 한계·미검증

- **네트워크·온체인 미사용**: 이번 라운드는 전부 정적 분석 + 인프로세스 재현이다. localnet/devnet RPC 왕복, 실제 SPL 전송, Firestore 실서버 왕복은 한 건도 실행하지 않았다. 따라서 라이브 전용 경로(BUG-01·BUG-05·BUG-11·BUG-22)의 영향 평가는 **코드 판독 + 가짜 클라이언트 재현에 기반한 추론**이며, "실제 온체인에서 이렇게 된다"로 단정하지 말 것. 특히 BUG-02 의 "SPL TransferChecked 가 decimals 불일치를 거부한다"는 SPL Token 프로그램 사양에 근거한 판단이고 실제 devnet 트랜잭션으로는 확인하지 않았다.
- **미커버 모듈 `web.briefing`**: 스냅샷 coverage_map 기준 전용 테스트가 없는 유일한 모듈이며, 이번 스캔에서도 확정 버그가 0건이다. 이는 '결함이 없다'가 아니라 **렌즈가 닿지 않았다**로 읽어야 한다. BUG-09(`_finalize` 경쟁)가 브리핑 스킵을 유발하는 것으로 보아 브리핑 경로는 세션 종료 타이밍에 종속적이므로, 다음 라운드에서 별도 렌즈를 배정할 가치가 있다.
- **전용 테스트 공백 4곳(이번 재현으로 드러남)**: `payments.ap2_mandate`(release↔credit_sale 상호작용 — BUG-03) · `market.price_feed` 의 start/end 슬라이싱과 오류 경로(BUG-16·BUG-17) · `IntradayReplayFeed` 의 집계 high/low 대조(BUG-10) · `payments.guard` 의 `decimals` 축(BUG-02). 수정 시 회귀 테스트를 함께 추가하지 않으면 같은 구멍이 재발한다.
- **중복 집계 주의**: BUG-12·BUG-20 은 `docs/reports/bug_latest.md` 의 기존 BUG-08·BUG-11 과 동일 결함이다. "새로 13건 찾았다" 같은 누적 집계에 그대로 더하면 이중 계상이 된다.
- **심각도는 재현된 영향 기준이며 수정 우선순위와 다르다**: BUG-11·BUG-12 는 금액 기준 '낮음'이지만 402 Guard 가 검증하지 않은 것을 "확인했다"고 기록하는 자리라, 심사 신뢰도 관점에서는 일부 '중간' 건보다 먼저 처리할 값어치가 있다. 반대로 BUG-23·BUG-25 는 마감 이후로 미뤄도 무방하다.
- **작업트리 상태**: 검증 전 과정을 스크래치패드에서만 수행했고 저장소에는 파일을 남기지 않았다. 리포트 기록 직전 `git status --short` 로 재확인한 결과 **작업트리는 깨끗했다**(추적 파일 변경 0건) — 즉 이 스캔은 앱 코드·테스트·데이터를 한 줄도 바꾸지 않았다. 검증 중 일부 검증관이 보고했던 선재 변경(`M docs/artifacts/*.html`)은 최종 확인 시점에 존재하지 않았으므로, 그 사이 사용자·병렬 작업이 정리한 것으로 보고 별도 조치는 필요 없다.

## 참고 통계(파이프라인 산출)
{
  "checked": 27,
  "confirmed": 25,
  "dropped": 2,
  "by_severity": {
    "높음": 1,
    "중간": 9,
    "낮음": 14,
    "정보": 1
  }
}

## 증거 스냅샷
docs/reports/_bugscan_20260727_131137.json
