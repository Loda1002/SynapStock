# 시스템 아키텍처 설계 (v2 — 주식 자동매매)
### 구글 클라우드 X 솔라나 AI Agentic 해커톤 · Track B: Autonomous On-chain Settlement

> **[0722 갱신 노트 — 0721 킥오프 Q&A 반영]** Gemini는 API 호출 수준이면 충분(ADK/Vertex 구조적 활용은 필수도 가산점도 아님). 아래 표의 "Gemini(Vertex AI) via ADK"는 **Gemini API(무료 티어) 직접 호출**로 하향 적용한다. $300 크레딧으로는 Gemini API 결제 불가. 심사 평가 네트워크는 Devnet/Localnet으로 확정.

> 컨셉: **사용자가 정한 조건·예산 한도 안에서, AI 에이전트가 스스로 주식을 사고팔고 USDC로 정산한다.** 사람은 규칙과 한도만 설정, 매수/매도 버튼은 누르지 않음.

> ⚠️ 데모 전제: 전부 **Solana devnet(테스트 토큰)** + **읽기전용 시세 데이터**. 실제 증권거래·실제 자금 이동 없음. 매매 조건은 사용자가 정의하는 규칙(투자 조언 아님).

---

## 1. 제품 컨셉

**SynapStock** — 조건 기반 자율 주식매매, USDC/Solana 정산.

- 전통 방식: AI가 주식 매매 → 브로커 API 호출 + 사람이 계좌 입금/주문 승인
- 이 프로젝트: 에이전트가 조건 충족 시 **사람 개입 없이 한도 내에서** 주식(토큰화 주식)을 USDC로 자율 매매
- 실세계 앵커: 솔라나 토큰화 주식(**xStocks**, 2026 Q2 거래량 $5.77B, 솔라나가 토큰화 주식의 97%) — "USDC로 온체인 주식 매매"는 이미 실재

### 데모 서사(심사 어필)
"사용자는 'AAPL이 조건 X면 $100까지 매수' 규칙만 설정. 이후 두 에이전트가 알아서 시세를 보고, 협의하고, 진짜 devnet USDC 트랜잭션으로 주식 토큰을 사고판다."

---

## 2. 멀티에이전트 구조

- **Trading Agent (Buyer)** — 사용자 대리. 시세·조건을 Gemini로 판단, 매수/매도 결정, AP2 예산 한도 보유, USDC 서명.
- **Market/Broker Agent (Seller)** — 토큰화 주식 시세 제시(payment-required), 온체인 결제 검증, 주식 토큰 전달 / 매도 시 USDC 반환.
- (스트레치) **Signal Agent / Risk Agent** — 리서치·리스크 한도 전담으로 분리(멀티에이전트 심화).

```
   사용자 ──(규칙 + 예산 한도: Open Payment Mandate)──► Trading Agent
                                                          │
   시세 데이터(읽기전용) ──► [Gemini 판단: 지금 살까?] ◄──┘
                                                          │ A2A 협의(견적·수량)
                                       Trading Agent ◄────┴────► Broker Agent
                                          │ x402: payment-submitted   │ x402: payment-required
                                          │ (USDC 전송 서명)           │ /payment-completed
                                          ▼                            ▼
                             ┌──────────── SOLANA devnet ────────────┐
                             │  USDC(SPL) ⇄ 토큰화 주식(tAAPL 등)     │
                             │  온체인 스왑 · 트랜잭션 해시            │
                             └────────────────────────────────────────┘
   [GCP] Gemini API(무료 티어) · Cloud Run · Secret Manager · Firestore(포지션/영수증) · (BigQuery/PubSub=스트레치)
```

---

## 3. 엔드투엔드 플로우 (매수 예시)

1. **트리거**: Trading Agent가 시세 데이터 + 사용자 규칙을 Gemini로 평가 → "tAAPL $50 매수" 결정
2. **A2A 견적요청**: Trading → Broker "tAAPL을 $50 예산으로 견적 줘"
3. **payment-required**: Broker "0.28 tAAPL = 50 USDC" (실시간 시세 기반)
4. **한도 체크(AP2)**: Trading이 예산·규칙 내인지 검증 → USDC 전송 서명 → **payment-submitted**
5. **온체인 정산**: USDC → Broker, Broker → tAAPL 토큰을 Trading 지갑으로 (가능하면 원자적 스왑)
6. **payment-completed**: Broker가 온체인 검증 후 포지션 확정 전달
7. **기록**: Firestore에 포지션·영수증 저장 (스트레치: BigQuery 거래로그)

**매도**는 역방향: 조건 충족 → tAAPL 반환 → USDC 수령.

---

## 4. 프로토콜 결정 & 근거

| 레이어 | 역할 | 기술 | 근거 |
|---|---|---|---|
| 통신 | 에이전트 협의 | **A2A** | 견적 합의 레일 |
| 권한 | 예산·자율한도 | **AP2** Open/Closed Payment Mandate | "한도 내 자율 결제" 요구 직결 |
| 실행 | 온체인 정산 | **x402 on Solana (USDC ⇄ 주식토큰)** | 심사 필수 devnet 라이브 tx |
| 두뇌 | 시세판단·협의 | **Gemini API(무료 티어)** | 심사 AI 활용도 — 호출 수준이면 충분(0721 Q&A) |
| 시세 | 매매 판단 입력 | 시장데이터 API(읽기전용) | 현실적 의사결정 |

- 개발 언어: **Python** (a2a-x402 공식 라이브러리 `x402_a2a` 참고)
- 네트워크: **Solana devnet** (개발은 localnet)

---

## 5. "주식"을 데모에서 표현하는 방법 — A안 확정

| 옵션 | 방식 | 장점 | 단점 |
|---|---|---|---|
| **A. 온체인 토큰화 주식(확정)** | devnet에 우리가 발행한 주식토큰(tAAPL 등)을 USDC로 스왑 | 매수=온체인 tx 자체, 정산 서사 가장 강함, xStocks 앵커 | 시세는 별도 데이터 피드로 주입 |
| B. 페이퍼 브로커 API(Alpaca 등) | 실제 시세·페이퍼 체결 + USDC는 정산/접근 결제 | 시세·체결 현실적 | 주식 체결이 오프체인, 서사 분리 |
| C. 목(mock) 시장 | 우리가 만든 가상 시장 | 가장 단순·통제 쉬움 | "실제성" 약함 |

> 확정: **A + 실시간 시세는 시장데이터 API로 주입**. 온체인 정산(심사 핵심) + 현실적 판단 둘 다 확보.

---

## 6. 레포 / 폴더 구조 (실제 — 0722 첫 세션에서 대조·확정)

```
solana-agent/
├── run_demo.py             # 엔드투엔드 데모 오케스트레이터 (드라이런/--live)
├── config.py               # .env 로드, 단위 변환
├── agents/
│   ├── trading_agent.py    # 구매: 판단(Gemini+규칙 폴백)→AP2 승인→x402 서명
│   ├── gemini_decider.py   # Gemini 판단 모듈 (무료 티어 API, 0722 적용)
│   └── broker_agent.py     # 판매: 견적→payment-required→검증(verify는 payments/)→정산·전달
├── market/
│   └── price_feed.py       # 읽기전용 시세 데이터 (목 → 실피드 교체 지점)
├── payments/
│   ├── x402_solana.py      # x402 payload 생성/검증/브로드캐스트 + 지갑 유틸
│   └── ap2_mandate.py      # Open/Closed Payment Mandate(예산 한도)
├── shared/
│   ├── a2a_messages.py     # payment-required/submitted/completed
│   └── models.py           # Quote/Position/Receipt
├── scripts/
│   └── setup_devnet.py     # 지갑·민트·에어드랍·잔액 준비 (RPC만 바꾸면 localnet 겸용)
├── secrets/                # 지갑 키페어 (커밋 금지, .gitignore 차단)
├── docs/                   # 아키텍처·체크리스트·진행로그
├── artifacts/tx/           # [예정] 트랜잭션 해시·실행 로그 아카이브 (커밋 대상)
├── infra/                  # [예정] Dockerfile, cloudrun.yaml
├── .env.example · requirements.txt · README.md · CLAUDE.md
```

원안과의 차이: `agents/`를 패키지(agent/strategy/wallet 분리)가 아닌 단일 모듈로 구현
(현 규모에선 충분, Gemini 판단·매도 로직이 커지면 분리). 지갑 유틸은 `payments/x402_solana.py`,
결제 검증(verify)도 같은 파일에 있음. 키 저장 위치는 `.wallets/` → `secrets/` 로 통일(0722).

---

## 7. 범위: MVP vs 확장 (마감 8/3)

### 반드시 데모에 나와야 하는 MVP
- Trading·Broker 두 에이전트, 시세+규칙으로 **Gemini가 매수 판단**
- A2A 견적 → **예산 한도 체크** → **devnet USDC로 주식토큰 실제 매수(라이브 tx)**
- Broker 온체인 검증 → 포지션 전달, **트랜잭션 해시** 확보
- 매도 1회까지 시연

### 스트레치
- AP2 정식 서명 mandate, Cloud Run 배포 + 공개 엔드포인트
- Firestore 포지션/영수증 + BigQuery 거래로그, Pub/Sub 비동기 정산
- 프론트 대시보드(시세·에이전트 대화·거래 실시간)
- Signal/Risk 에이전트 분리, 멀티종목·멀티턴

### 일정
- **~7/24** 셋업(devnet 지갑·USDC·주식토큰 발행), Gemini 호출 확인 — *MVP 코어 코드는 이미 완료, 라이브 tx 확보가 관문*
- **~7/28** A2A + x402 매수 성공 = **devnet 라이브 tx** ← 최우선 관문
- **~7/31** 매도·AP2 한도·Cloud Run 배포·Firestore
- **~8/2** 프론트/데모영상/README/제출물
- **8/3 23:59** 제출(버퍼 포함)

---

## 8. 심사기준 매핑
1. **혁신성·UX** → 조건만 설정하면 에이전트가 자율 매매, 사람 개입 0
2. **AI 활용도** → Gemini 시세판단·협의, 멀티에이전트, A2A
3. **기술완성도·블록체인** → A2A+AP2+x402, Solana USDC⇄주식토큰 온체인 정산, Cloud Run+Secret Manager
4. **실제 구동** → devnet 라이브 매매 트랜잭션 해시·로그 시연

---

## 9. 역할 분담
- **Claude(Claude Code)**: 에이전트·결제·시세·인프라 코드 전부, 아키텍처, README
- **회원님**: GCP 콘솔 API 켜기, gcloud 인증, 실행/배포, 방향 결정, Devnet SOL 디스코드 요청
- **Gemini**: 프로덕트 런타임 AI(판단·대화) + 디자인(Stitch)·데모 자료
