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

## 아직 안 붙은 것 (다음 단계)

- 웹 서비스화(FastAPI + 대시보드) → Cloud Run 배포, Secret Manager, Firestore
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
├── scripts/setup_devnet.py# devnet 준비(민트·에어드랍·지급)
├── requirements.txt · .env.example · .gitignore
```

## 검증 완료

- 엔드투엔드 데모 정상 동작(협의→AP2 승인→결제 서명→검증→포지션 반영)
- AP2 한도 초과/미허용 종목/총예산 초과 → 결제 차단
- x402 검증: 수취인 위변조·금액 부족 → 거부, 정상 결제 → 통과
- **localnet 라이브 검증 통과(2026-07-22)**: 매수 3건 = 온체인 tx 6건(USDC 결제 + 주식 전달)
  확정, 전후 잔액 RPC 교차검증 PASS, 증빙 `artifacts/tx/` 아카이브 — devnet은 SOL 확보 후 동일 절차
