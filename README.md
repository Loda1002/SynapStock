# 402 Guard — 손을 떼려면, 한도가 있어야 합니다

구글 클라우드 X 솔라나 AI Agentic 해커톤 · **Track C: Multi-Agent Commerce**

**402 Guard 는 AI 에이전트가 결제에 서명하기 직전, 이 청구서가 합의한 그 청구서인지
대조합니다.** 그래서 사람이 화면을 지켜보지 않아도 됩니다.

증명 방식은 하나입니다 — 두 에이전트(구매·판매)가 A2A 로 협의하고 사용자가 서명한
한도(AP2) 안에서 **Solana USDC 로 토큰화 주식을 실제로 사고팔게** 한 뒤, 그 결제 경로에
악성 청구서를 던져 봅니다.

> ⚠️ 데모는 전부 **devnet/localnet(테스트 토큰)** + **읽기전용 시세**로 동작합니다. 실제
> 증권거래·실제 자금 이동은 없습니다. 매매 조건은 사용자가 정의하는 규칙이며 투자 조언이 아닙니다.

## 이 계층이 왜 비어 있었나

x402 는 구매자의 **키**를 지킵니다. 하지만 구매자가 **무엇에 서명하는지**는 검증하지
않습니다 — 수취인이 합의한 상대인지, 청구액이 합의 견적과 같은지를 묻는 주체가 스펙에
없습니다. 그리고 `exact` 스킴은 실행되면 되돌릴 수 없는 push 결제입니다.

AP2 는 수취인 제약(`allowed payees`)을 **정의하고**, 그 집행을 Credential Provider ·
Network · Merchant Payment Processor 에 배정합니다. 그런데 **self-custody 온체인
플로우에는 그 세 주체가 없습니다.** 집행 지점이 공석입니다.

402 Guard 는 그 검증 책임을 구매 에이전트 로컬로 옮겨 **서명 직전에** 실행합니다.

---

## 지금 동작하는 것

### 402 Guard — 서명 직전 게이트 (`payments/guard.py`)

- **하드 검사 6종**(결정론·오차 0): 주문번호 형식 · 결제 자산 · 종목(허용 목록 **및**
  지금 주문한 그 종목) · 수취인 allowlist · 금액(합의 견적과 base units 정합) ·
  **의도 지출 상한**(브로커 견적과 독립적인, 구매 에이전트 자신이 정한 상한) · 건별 한도
- **의미 대조 1종**(LLM): 청구서의 사람이 읽는 설명이 우리 주문 의도와 **같은 물건**을
  가리키는지. 금액·수취인·자산이 전부 정상인데 물건만 다른 청구서는 규칙으로 표현할 수
  없습니다 — 상세 [`docs/axis2_ai_narrative.md`](docs/axis2_ai_narrative.md)
- **매수·매도 양 레그 대칭**: 자산을 내보내는 방향에서도 자산·수취인·수량·종목을 대조
- **정산 후 배송 검증**(`check_delivery`): 온체인 잔액 재조회로 청구서대로 도착했는지 확인.
  미확인은 차단이 아니라 **보류 + 세션 정지**입니다(회수 경로는 없습니다 — §정직한 범위)
- 위반이면 **서명 자체가 만들어지지 않습니다.** 이 계층의 유출이 0 인 것은 측정 결과가
  아니라 구조적 사실입니다.

### 자율 결제 스택

- **Trading Agent(구매)** ↔ **Broker Agent(판매)** 의 A2A + x402 3단계 결제 흐름
  (`payment-required` → `payment-submitted` → `payment-completed`). 단계 명칭은 공식
  `google-agentic-commerce/a2a-x402` 확장과 일치합니다.
- **AP2 mandate**: 사용자가 예산·건별 한도·허용 종목·허용 자산에 ed25519 로 서명 →
  에이전트가 그 한도 안에서만 결제. `authorize()` 가 매 건 검사하고 잔여 예산을 **실제로
  차감**하며, 정산 실패 시 예약을 원복합니다.
  ※ 표기: **"AP2 의 mandate 위임 모델을 솔라나 ed25519 서명으로 재구성"**(SD-JWT/VC 미채택).
- **x402 온체인 결제 코어**: USDC(SPL) 전송 트랜잭션 생성·서명·구조 검증 +
  **Memo 대사 키**(`AT1:{order_id}:{mandateSig8}`)로 주문↔트랜잭션 바인딩·리플레이 방어
- **지갑 연결 = 로그인**(`web/auth.py`): 비밀번호를 만들지 않습니다. Phantom 등 Solana
  지갑이 서버가 발급한 SIWS 서식 메시지에 서명하면 그게 소유 증명이자 로그인입니다.
  검증은 원문 완전 일치 + ed25519 (의존성 추가 0).
- **Gemini 판단**(무료 티어): 시세·지표를 보고 매수/매도/보류 판단. **단, 규칙 신호 없는
  개시는 코드가 막습니다**(`_rule_gate`) — AI 재량은 '멈추는 방향'으로만 열려 있습니다.
  호출 실패 시 규칙 기반 자동 폴백.
- **매도 사이클**: 주식토큰 전송 → 브로커 온체인 검증 → USDC 지급, 매도 대금은
  AP2 예산에 환입(예산 = 순투입 한도)
- **거부 케이스 4종**(`scripts/demo_rejections.py`): 건별 한도 초과 · mandate 위변조 ·
  결제 금액 부족 · 미허용 종목 — 전부 돈이 나가기 전에 차단
- 실브로드캐스트 경로 포함(`--live`, localnet/devnet 공용)
- **웹 대시보드**(`python -m web.server`): FastAPI + SSE 실시간 — 시세/포지션/AP2 예산
  게이지/실현손익, AI 판단 타임라인([gemini]/[rule-fallback] 배지), A2A·x402 협상 로그,
  거래 내역 테이블(explorer 링크), **긴급정지/재개**(정지 주체 기록). 세션 종료 시
  잔액 교차검증 + `artifacts/tx/` 아카이브 자동
- **실데이터 시세 재생(ReplayPriceFeed)**: 실제 미국 주식 일봉 CSV를 1틱=1봉으로 재생
  (워밍업 20봉 → MA5·MA20 즉시 성립, 재생 소진 시 세션 자동 종료). 매매 규칙은
  **지표 기준** — 매수: MA5 대비 −2% / 매도: 평단 대비 +3% 익절 (% 조정 가능)
- **Gemini 판단 모드 토글**: 엄격(규칙 그대로) / **추세(보류 재량)** — 규칙 신호가 떠도
  추세가 나쁘면 AI 가 근거를 들어 보류. 한도 차단은 어느 모드든 AP2 가 기계적으로 수행
- **백테스트 러너**(`scripts/backtest.py`): 같은 구간에서 규칙 vs Gemini 엄격 vs 추세를
  비교 — 총손익·수익률·승률·MDD·AP2 거부 집계 → `artifacts/backtests/`

- **TA 판단 보강**(`market/indicators.py`): MA 1~200일 배열·크로스·기울기, 지지/저항,
  차트·캔들 패턴을 판단 근거로 주입 (세션 토글, 기본 OFF — 상세 `docs/ta_upgrade.md`)
- **적립식(DCA) 전략**: 틱/분/매일 시각 기준 정액 매수 · **데일리 브리핑**(Gemini 한국어
  리포트, 실패 시 템플릿 폴백) · **수수료 투명화**(체결마다 브로커 수수료 = 수익모델 증빙)
- **Firestore 영속화 + Cloud Run 배포 구성**: 세션·체결·브리핑·한도가 재시작 너머로 남고
  (`/api/history`), Dockerfile + Secret Manager 런북 완비 (`docs/deploy_cloud_run.md`)

## 정직한 범위 — 402 Guard 가 하지 않는 일

먼저 밝히는 편이 지적당하는 것보다 낫습니다.

1. **막는 것은 '위조'이지 '신뢰 실패'가 아닙니다.** 수취인 검사는 *목록에 있는가*만 봅니다.
   allowlist 안의 상대가 배신하면 막지 못합니다.
2. **브로커 불이행(먹튀)은 되돌리지 못합니다.** 결제와 배송이 별개 트랜잭션이라, 배송이
   확인되지 않으면 **탐지·세션 정지**까지가 전부입니다. 저장소에 환불·에스크로·분쟁 구현은
   0건입니다. 대신 (a)allowlist 로 상대를 한정하고 (b)발생 즉시 멈춰 손실에 1건 상한을
   씌우고 (c)`payment_tx` 서명과 빈 `delivery_tx` 를 증빙으로 남깁니다.
3. **막는 것은 '정직한 구매 에이전트가 악성 청구서에 속는 것'입니다.** 악성·버그 있는 엔진이
   사용자를 배신하는 것은 현재 코드로 막지 못하며, 온체인 프로그램 이관이 로드맵입니다.
4. **"유출 0.00"은 서명 전 차단 계층의 값입니다.** 정산 후 탐지 계층에서 확인된 미배송 금액은
   회수하지 못하며, `scripts/red_team.py` 리포트가 두 계층을 **분리해서** 출력합니다.
5. 구매 대상은 **자체 발행 토큰**입니다 — devnet 에 실물 토큰화 주식이 존재하지 않기
   때문입니다. 민트 상수와 스왑 경로 2곳 교체로 실물 전환됩니다.
6. **결제 통화도 마찬가지입니다.** 현재 저장소의 devnet 증빙
   (`artifacts/tx/20260724_1643_solana-devnet_live_buy.json`)에서 오간 'USDC' 는 민트
   `8L9feSSChJHXEF58etFL1zsTzWiggqRdWFwqLM6vgH4u` 이고, **그 발행 권한은 구매자 지갑
   자신**입니다(당시 `setup_devnet` 이 테스트 민트를 만들어 `.env` 를 덮어썼습니다).
   즉 그 트랜잭션은 "자기가 찍은 돈으로 지불"한 것이고, 저희가 먼저 밝힙니다.
   현재 코드의 기본값은 **Circle 공식 devnet USDC**(`4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`,
   `config.py:44`)이며 `setup_devnet` 은 자체발행 민트를 감지하면 중단합니다. 배포본이
   402 로 광고하는 asset 도 이 공식 민트입니다. **공식 민트로 정산한 온체인 증빙은
   재실증 대기 중**이며, 그 전까지 축③ 문구는 "USDC 인터페이스(SPL 6 decimals) 호환"까지가
   정확한 표현입니다.

## 아직 안 붙은 것 (다음 단계)

- 사용자별 세션 분리 — 현재 엔진은 전역 싱글턴 1개(지갑 1개 연결 범위)
- 사용자 지갑이 AP2 위임장에 직접 서명(현재는 지갑 연결 = 로그인까지)
- 온체인 프로그램으로 예산·수취인 강제 이관
- 멀티턴 협상, 에이전트 대화형 제어

---

## 빠른 실행 — 드라이런 (네트워크·GCP 불필요)

요구사항: Python 3.10+ (3.11+ 권장)

```bash
pip install -r requirements.txt
```

```bash
python run_demo.py
```

`.env` 도 지갑도 없는 상태에서 그대로 동작한다(방금 clone 한 상태 그대로). 기본 12틱 안에서
**매수 2회 → 익절 매도 1회**가 일어나고, 두 에이전트의 협의(A2A), AP2 한도 승인, 402 Guard 의
청구서 대조, 서명된 결제 트랜잭션 생성·검증까지 모두 실제로 수행한다. 온체인 전송만 생략한다
(`--ticks N` 으로 틱 수 조절 — 목 시세는 매수·매도 1사이클에 최소 9틱이 필요하다).

### 30초 안에 방어를 직접 확인하기 — 레드팀

```bash
python -m scripts.red_team --report
```

공격 7종을 **실제 결제 경로에 그대로 태워** 차단 매트릭스를 출력한다. 자작 목업이 아니라
`TradingAgent.build_payment` → `Guard` → `BrokerAgent.settle` 을 지나간다. 리포트는
계층을 합치지 않는다 — **구매자 서명 전 차단 / 판매자측 방어 / 정산 후 탐지**를 나눠 세고,
같은 실행 안에서 정상 거래 14건의 오탐도 함께 산출한다(과거 아티팩트를 끌어오지 않는다).

심사위원이 **직접 악성 수취인 주소를 넣어볼 수 있다**:

```bash
python -m scripts.red_team --report --attacker <당신의_pubkey>
```

## 웹 대시보드

```bash
python -m web.server     # http://localhost:8000 (포트는 .env WEB_PORT)
```

- `/` 소개(랜딩) · `/app` 대시보드 · `/connect` **지갑 연결(= 로그인)**
- 첫 화면 KPI 는 수익률이 아니라 **지출 시도 · 가드 차단 · 한도 거부 · 유출 0.00 USDC**
- 그 바로 아래 **AI 카드** — 두 레이어(판단 / 청구서 심사)가 이번 세션에서 실제로 몇 번
  일했는지. Gemini 판단 비율 · 규칙 게이트가 되돌린 AI 개시 · 의미 대조 건수 · **실제 LLM
  호출 수와 캐시 적중**(호출 예산이 어떻게 관리되는지 화면에 드러난다)
- 브라우저에서 모드(드라이런/라이브)를 골라 **세션 시작** — 시세 틱마다 판단·협상·정산이
  실시간(SSE)으로 흐른다. 🛑 **긴급정지**는 신규 판단·결제만 즉시 멈추고(진행 중 정산 1건은
  마무리) 시세는 계속 흐른다.
- 라이브 세션을 종료하면 전후 잔액 교차검증 후 `artifacts/tx/` 에 증빙 JSON 이 남는다
  (판단 출처 집계 `ai` 블록 포함). 새로고침해도 피드가 복원된다(SSE 재전송).

**지갑 연결**은 Phantom 등 Solana 지갑이 서버가 발급한 1회용 메시지에 서명하는 방식이다.
서명은 **지갑 소유 증명일 뿐이고 자금이 이동하지 않는다**(트랜잭션이 아니다). 데모는
연결 없이도 전부 둘러볼 수 있다 — 심사위원이 지갑 없이 관전할 수 있어야 하기 때문이다.

```bash
python -m scripts.test_wallet_auth   # 서명 위조·메시지 변조·리플레이·만료 차단 40건
```

## 브로커 HTTP 402 — x402 자원 서버 (G5)

판매자(브로커)는 **진짜 HTTP 자원 서버**다. 결제 없이 주문하면 표준 `402 Payment Required`
와 `accepts[]` 를 돌려주고, `X-PAYMENT` 헤더를 붙여 같은 요청을 재시도하면 200 으로 정산된다.

```bash
python -m web.broker_service --port 8402          # 별도 프로세스·별도 포트·별도 키페어

curl -i -X POST http://127.0.0.1:8402/broker/orders \
     -H "Content-Type: application/json" \
     -d '{"symbol":"AAPL","spend_usdc":"10","price_usdc":"200"}'
# → HTTP/1.1 402 Payment Required
#    {"x402Version":1,"error":"payment required","accepts":[{"scheme":"exact","payTo":…}]}

curl -s http://127.0.0.1:8402/.well-known/x402    # 디스커버리(미구현 항목까지 공개)

python -m scripts.demo_http402                    # 전 과정 실왕복 + artifacts/x402_http/ 증빙
python -m scripts.test_http402                    # 53건 검증
```

엔진의 매수 레그를 이 HTTP 경로로 보내려면 `.env` 에 `BROKER_HTTP_URL=http://127.0.0.1:8402`
를 주면 된다(빈값이면 기존 인프로세스 A2A). 배포본은 컨테이너당 포트가 하나뿐이라 같은
라우터가 메인 앱에도 마운트돼 있어 `curl -i https://<배포URL>/broker/orders` 로도 확인된다.

> **정직한 범위**: HTTP 402 실왕복은 **매수(자산 구매) 레그**다. 매도(환매)는 브로커가
> 구매자에게 돈을 보내는 방향이라 402 challenge 모델과 맞지 않아 A2A 인프로세스로 남긴다.
> facilitator 없이 판매자가 직접 검증·정산한다. 두 가지 모두 디스커버리 응답의
> `notImplemented` 에 그대로 적혀 있다.

## 실데이터 시세 (리플레이) · 백테스트

시세는 **실제 미국 주식 일봉**을 내려받아 재생한다(결정적 재현 — 심사·데모에서 같은 구간이
같게 흐름, 오프라인 시연 가능). 데이터 출처: [Alpha Vantage](https://www.alphavantage.co)
무료 API (TIME_SERIES_DAILY). `data/market/*.csv` 는 재현성을 위해 저장소에 포함하며,
재배포가 아닌 데모·평가 목적으로만 사용한다.

```bash
# 1회 수집 (무료 키: https://www.alphavantage.co/support/#api-key → .env ALPHAVANTAGE_API_KEY)
python scripts/fetch_market_data.py                  # AAPL·TSLA·NVDA 일봉 2024~ → data/market/
# 대시보드에서 시세 피드 "실데이터 재생 (일봉)" 선택 → 세션 시작 (CSV 없으면 잠김)
# 재생 구간 고정(선택): .env REPLAY_START / REPLAY_END — 비우면 CSV 전체 구간

# 백테스트 — 같은 구간 3종 비교 (Gemini 는 무료 티어 보호: 4초 간격·기본 60봉 제한)
# 구간 인자를 빼면 커밋된 CSV 전체를 쓴다. 특정 구간은 --from/--to 로 지정.
python scripts/backtest.py --brain rule
python scripts/backtest.py --brain gemini --mode strict
python scripts/backtest.py --brain gemini --mode trend
# 종목 교체·구간 지정 예: python scripts/backtest.py --symbol TSLA --from 2026-06-01 --to 2026-07-22
```

## localnet 라이브 실행 (개발 기본 경로 — 검증 완료)

`solana-test-validator`가 필요하다 (Windows는 WSL Ubuntu 권장:
`curl -sSfL https://release.anza.xyz/stable/install | sh`).

```bash
solana-test-validator --reset          # 터미널 1 (WSL)
# 터미널 2 (.env: SOLANA_RPC_URL=http://127.0.0.1:8899 / SOLANA_NETWORK=solana-localnet)
python scripts/setup_devnet.py         # 지갑(secrets/) + 민트 + 잔액 준비 + .env 기록
python run_demo.py --live --replay AAPL --from 2026-06-01 --to 2026-07-22
       # 실데이터 재생으로 매수→매도 풀사이클 브로드캐스트 + 잔액 교차검증 + 아카이브
python run_demo.py --live               # 목 시세(구조 데모) — 기본 12틱에서 매수 2·매도 1
python scripts/demo_rejections.py      # 거부 4종 데모 (네트워크 불필요)
```

실행이 끝나면 `artifacts/tx/`에 트랜잭션 해시·전후 잔액·교차검증 결과가 JSON으로 남는다.

## devnet 라이브 실행 (Cloud Shell 권장)

CLI 설치 없이 브라우저에서: GCP 콘솔 우측 상단 터미널(`>_`) → Cloud Shell.

```bash
pip install -r requirements.txt
cp .env.example .env
python scripts/setup_devnet.py   # 지갑 + SOL(파우셋 실패 시 여유 지갑에서 자동 충당) + 테스트 민트 + 잔액 + .env
python run_demo.py --live --replay AAPL --from 2026-06-01 --to 2026-07-22
       # devnet 에 실제 매수·매도 브로드캐스트 (공용 RPC 429 는 자동 재시도)
```

파우셋(faucet.solana.com)은 GitHub 공개 repo 조건 등으로 막힐 수 있다. `setup_devnet.py` 는
한쪽 지갑에 SOL 여유가 있으면 부족한 지갑으로 자동 이체하고, 둘 다 부족하면 충전할 주소를
안내한다. 트랜잭션은 `https://explorer.solana.com/tx/<sig>?cluster=devnet` 에서 확인.

---

## 폴더 구조

```
solana-agent/
├── run_demo.py            # 엔드투엔드 데모 오케스트레이터
├── config.py             # .env 로드, 단위 변환
├── agents/
│   ├── trading_agent.py   # 구매: 판단→AP2 승인→x402 서명
│   └── broker_agent.py    # 판매: 견적→검증→정산→전달
├── payments/
│   ├── guard.py            # ★ 402 Guard — 서명 직전 게이트 (하드 검사 6종 + 배송 검증)
│   ├── invoice_semantics.py# ★ 청구서 의미 대조 (LLM — 차단만 가능, 통과 권한 없음)
│   ├── x402_solana.py      # 결제 트랜잭션 생성/검증/브로드캐스트 + Memo 대사
│   ├── x402_http.py        # HTTP 402 와이어 포맷 + 클라이언트
│   └── ap2_mandate.py      # Open/Closed Payment Mandate (한도·서명)
├── market/
│   ├── price_feed.py       # 실데이터 일봉 재생 / 목 시세 / 인트라바 합성
│   └── indicators.py       # MA·크로스·지지저항·차트/캔들 패턴
├── shared/
│   ├── a2a_messages.py     # x402 3단계 메시지
│   └── models.py           # Quote/Position/Receipt
├── web/
│   ├── server.py           # FastAPI: 상태 API + SSE + 컨트롤 + 지갑 인증
│   ├── auth.py             # ★ 지갑 연결 = 로그인 (SIWS 서식 + ed25519 검증)
│   ├── engine.py           # TradingEngine (run_demo 사이클의 서비스화)
│   ├── broker_service.py   # x402 자원 서버 (402 + accepts[] + 디스커버리)
│   ├── events.py           # EventBus (인메모리 히스토리 + 구독)
│   ├── store.py            # Firestore 영속화 (기본 OFF)
│   └── static/             # landing/index/connect.html + css(skeleton·theme) + js(app·wallet)
├── scripts/
│   ├── red_team.py         # ★ 공격/차단 매트릭스 (실제 결제 경로를 그대로 태움)
│   ├── setup_devnet.py     # devnet 준비(민트·에어드랍·지급)
│   ├── backtest.py         # 규칙 vs Gemini 비교 + 매수후보유 벤치마크
│   └── test_*.py           # 단위 테스트 20종 (자체 하네스, pytest 불필요)
├── requirements.txt · .env.example · .gitignore
```

## 테스트

전용 러너가 없다. 각 파일이 스스로 실행되고 실패 건수를 종료 코드로 돌려준다.

```bash
for f in scripts/test_*.py; do python -m "scripts.$(basename "$f" .py)" || echo "FAIL $f"; done
```

## 검증 완료

- 엔드투엔드 데모 정상 동작(협의→AP2 승인→결제 서명→검증→포지션 반영)
- AP2 한도 초과/미허용 종목/총예산 초과 → 결제 차단
- x402 검증: 수취인 위변조·금액 부족 → 거부, 정상 결제 → 통과
- **localnet 라이브 검증 통과(2026-07-22)**: 매수 3건 = 온체인 tx 6건(USDC 결제 + 주식 전달)
  확정, 전후 잔액 RPC 교차검증 PASS, 증빙 `artifacts/tx/` 아카이브
- **devnet 라이브 검증 통과(2026-07-24)**: 공용 RPC(api.devnet.solana.com)에서 실데이터 재생
  (AAPL) 라이브 — 매수 4 + 매도 1 = 온체인 tx 10건 전부 확정, explorer(cluster=devnet) 조회 가능,
  교차검증 PASS(실현 +4.9 USDC 온체인 반영), 증빙
  `artifacts/tx/20260724_1643_solana-devnet_live_buy.json`
- **웹 대시보드 라이브 검증 통과(2026-07-23)**: 브라우저에서 라이브 세션 — 매수 4건 + 매도 1건
  전부 온체인 확정(실현손익 +7 USDC 온체인 반영), 긴급정지/재개 동작, 교차검증 PASS,
  `artifacts/tx/20260723_1220_solana-localnet_web_session.json` 아카이브
- **레드팀(2026-07-27)**: 공격 7종 — 구매자 서명 전 차단 5 · 판매자측 방어 1 · 정산 후 탐지 1.
  서명 전 계층 유출 **0.00 USDC**, 같은 실행의 정상 거래 14건 **오탐 0**. 정산 후 탐지 1건의
  29.99 USDC 는 **회수하지 못한다**고 리포트가 명시한다(계층을 합치지 않는다).
- **의미 대조(2026-07-27)**: 값이 전부 정상이고 설명만 다른 청구서를 하드 검사 6종이 **전부
  통과**시킨 뒤 의미 대조가 차단하는 것을 같은 실행에서 확인. 실제 Gemini 판정 근거 문장까지
  리포트에 출력. 정상 거래 14건 오차단 0, 실제 LLM 호출 1회(서식 캐시 13).
- **테스트 20종 전부 통과** — `payments/`·`web/`·`market/` 단위 + 회귀
