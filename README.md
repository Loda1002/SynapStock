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
- **Gemini 매매 판단**(무료 티어, `gemini-flash-latest`): 시세 흐름·규칙·예산을 보고
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

## 아직 안 붙은 것 (다음 단계)

- Cloud Run 배포, Secret Manager, Firestore (P2: 수수료 투명화·한도 설정 화면·알림·DCA·브리핑)
- 디자인 스킨 적용(시안 수령 후 — `web/static/css/theme.css` 변수 교체)
- 멀티종목·멀티턴 협상

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

## localnet 라이브 실행 (개발 기본 경로 — 검증 완료)

`solana-test-validator`가 필요하다 (Windows는 WSL Ubuntu 권장:
`curl -sSfL https://release.anza.xyz/stable/install | sh`).

```bash
solana-test-validator --reset          # 터미널 1 (WSL)
# 터미널 2 (.env: SOLANA_RPC_URL=http://127.0.0.1:8899 / SOLANA_NETWORK=solana-localnet)
python scripts/setup_devnet.py         # 지갑(secrets/) + 민트 + 잔액 준비 + .env 기록
python run_demo.py --live              # 실제 매수 브로드캐스트 + 잔액 교차검증 + 아카이브
python run_demo.py --live --ticks 8    # 매수→매도 풀사이클 (틱 7에서 매도 트리거)
python scripts/demo_rejections.py      # 거부 4종 데모 (네트워크 불필요)
```

실행이 끝나면 `artifacts/tx/`에 트랜잭션 해시·전후 잔액·교차검증 결과가 JSON으로 남는다.

## devnet 라이브 실행 (Cloud Shell 권장)

CLI 설치 없이 브라우저에서: GCP 콘솔 우측 상단 터미널(`>_`) → Cloud Shell.

```bash
pip install -r requirements.txt
cp .env.example .env
python scripts/setup_devnet.py   # 지갑 생성 + SOL 에어드랍 + 테스트 USDC/주식 민트 + 잔액 지급 + .env 기록
python run_demo.py --live        # devnet 에 실제 매수 트랜잭션 브로드캐스트
```

에어드랍이 파우셋 한도로 실패하면 https://faucet.solana.com 에서 지갑 주소로 수동 충전 후
재실행하세요. 트랜잭션은 `https://explorer.solana.com/tx/<sig>?cluster=devnet` 에서 확인.

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
  확정, 전후 잔액 RPC 교차검증 PASS, 증빙 `artifacts/tx/` 아카이브 — devnet은 SOL 확보 후 동일 절차
- **웹 대시보드 라이브 검증 통과(2026-07-23)**: 브라우저에서 라이브 세션 — 매수 4건 + 매도 1건
  전부 온체인 확정(실현손익 +7 USDC 온체인 반영), 긴급정지/재개 동작, 교차검증 PASS,
  `artifacts/tx/20260723_1220_solana-localnet_web_session.json` 아카이브
