# AutoTrader Agent — 조건 기반 자율 주식매매 (USDC/Solana 정산)

구글 클라우드 X 솔라나 AI Agentic 해커톤 · **Track C: Multi-Agent Commerce**

두 AI 에이전트(구매·판매)가 A2A로 협의하고, 사용자가 정한 **예산 한도(AP2) 안에서**
사람 승인 없이 **Solana devnet USDC로 토큰화 주식을 자동 매매**한다.

> ⚠️ 데모는 전부 **devnet(테스트 토큰)** + **읽기전용 시세**로 동작합니다. 실제 증권거래·실제
> 자금 이동은 없습니다. 매매 조건은 사용자가 정의하는 규칙이며 투자 조언이 아닙니다.

---

## 지금 동작하는 것 (MVP 코어)

- **Trading Agent(구매)** ↔ **Broker Agent(판매)** 의 A2A + x402 3단계 결제 흐름
  (`payment-required` → `payment-submitted` → `payment-completed`)
- **AP2 Payment Mandate**: 사용자가 예산·건별 한도·허용 종목을 서명(ed25519)으로 설정 →
  에이전트가 한도 내에서만 자율 결제 (초과 시 결제 자체 차단)
- **x402 온체인 결제 코어**: USDC(SPL) 전송 트랜잭션 생성·서명·**구조 검증**
  (수취인/금액/민트/서명 확인)
- **Gemini 매매 판단**(무료 티어, `gemini-flash-lite-latest`): 시세 흐름·규칙·예산을 보고
  매수/매도/보류를 결정하고 이유를 생성 — API 장애 시 규칙 기반 **자동 폴백**
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

## 아직 안 붙은 것 (다음 단계)

- 로그인·회원가입(Firebase Auth)과 사용자별 세션 분리 — 현재 엔진은 전역 싱글턴 1개
- 디자인 스킨 적용(시안 수령 후 — `web/static/css/theme.css` 변수 교체)
- 멀티종목·멀티턴 협상, 에이전트 대화형 제어

---

## 빠른 실행 — 드라이런 (네트워크·GCP 불필요)

요구사항: Python 3.10+ (3.11+ 권장)

```bash
pip install -r requirements.txt
python run_demo.py            # 실제 서명 트랜잭션 생성 + x402 검증까지 (브로드캐스트만 생략)
python run_demo.py --ticks 6  # 시세 틱 수 조절
```

드라이런은 두 에이전트의 협의, AP2 한도 승인, 서명된 결제 트랜잭션 생성과 검증을 모두
실제로 수행한다. 온체인 전송만 생략한다.

## 웹 대시보드 (P1 — 검증 완료)

```bash
python -m web.server     # http://localhost:8000 (포트는 .env WEB_PORT)
```

브라우저에서 모드(드라이런/라이브)를 골라 **세션 시작** — 시세 틱마다 판단·협상·정산이
실시간(SSE)으로 흐른다. 🛑 **긴급정지**는 신규 판단·결제만 즉시 멈추고(진행 중 정산 1건은
마무리) 시세는 계속 흐른다. 라이브 세션을 종료하면 전후 잔액 교차검증 후 `artifacts/tx/`에
증빙 JSON 이 남는다. 새로고침해도 피드가 복원된다(SSE 재전송).
디자인은 의도적으로 무디자인 — 시안 수령 시 `web/static/css/theme.css` 변수만 교체.

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
python run_demo.py --live               # 목 시세(구조 데모) — 현 MA5 규칙에선 거래 미발생
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
│   ├── x402_solana.py     # 결제 트랜잭션 생성/검증/브로드캐스트
│   └── ap2_mandate.py     # Open/Closed Payment Mandate (한도·서명)
├── market/price_feed.py   # 읽기전용 시세(목) — 실피드로 교체 지점
├── shared/
│   ├── a2a_messages.py    # x402 3단계 메시지
│   └── models.py          # Quote/Position/Receipt
├── web/                   # 웹 대시보드 (P1)
│   ├── server.py          # FastAPI: 상태 API + SSE + 컨트롤
│   ├── engine.py          # TradingEngine (run_demo 사이클의 서비스화)
│   ├── events.py          # EventBus (인메모리 히스토리 + 구독)
│   └── static/            # index.html + skeleton.css(구조) + theme.css(스킨) + app.js
├── scripts/setup_devnet.py# devnet 준비(민트·에어드랍·지급)
├── requirements.txt · .env.example · .gitignore
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
