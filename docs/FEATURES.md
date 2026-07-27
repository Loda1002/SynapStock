# 기능 명세 — 주요 기능 · 변경 이력 · 현재/이전 대비

> **이 문서의 목적**: "지금 무엇이 되고, 무엇이 바뀌었는가"를 한눈에 본다.
> - [`docs/feature_spec.md`](feature_spec.md) 는 **계획**(무엇을 만들 것인가), 이 문서는 **현황**(무엇이 되어 있는가).
> - [`docs/progress_log.md`](progress_log.md) 는 **날짜별 로그**, 이 문서는 **기능별 요약**.
> - 큰 기능이 완료/변경되면 이 문서의 해당 행을 갱신한다.
>
> 마지막 갱신: 2026-07-24 · 대상 커밋: `a955ccb` · 코드 규모 약 5,000줄(agents 917 / payments 383 /
> market 610 / web 1,693 / shared 159 / scripts 1,256)

---

## 0. 한 줄 정의 (2026-07-24 재포지셔닝)

**이전**: "사용자가 정한 한도 안에서 AI 에이전트가 스스로 토큰화 주식을 사고팔고 USDC로 정산하는
멀티에이전트 시스템" (= AutoTrader Agent)

**현재(제출용)**: **"402 Guard — 에이전트 지출 승인 게이트."**
*x402 스펙은 파는 쪽을 보호한다. 402 Guard는 사는 쪽(사람 없이 결제하는 AI 에이전트)을 보호하는,
돈이 나가기 직전의 마지막 검증 게이트다.* 토큰화 주식 자동매매는 이 게이트를 증명하는 레퍼런스 시나리오.

**왜 바꿨나**: 직전 Solana x402 해커톤 수상작 16건 중 "AI 트레이딩 봇" 0건. "AI가 알아서 매매한다"는
흔한 봇으로 분류돼 탈락한다. 근거·전환 상세는 [`docs/differentiation.md`](differentiation.md).

---

## 1. 주요 기능 (현재 구동되는 것 — 전부 실검증 완료)

### 1-1. 결제·정산 코어 (프로젝트의 심장)

| 기능 | 설명 | 위치 | 상태 |
|---|---|---|---|
| **402 Guard 지출 승인 게이트** | 구매 에이전트가 서명 직전 통과: check_demand(금액·수취인·자산·주문번호 4항목, 차단코드 6종) + check_delivery(정산 후 온체인 재조회) | `payments/guard.py` | ✅ 단위 13종·localnet |
| **A2A 에이전트 협상** | 구매 에이전트 ↔ 브로커 에이전트가 메시지로 견적 요청·응답 | `shared/a2a_messages.py`, `agents/` | ✅ localnet 검증 |
| **AP2 mandate 한도** | 사용자가 서명한 한도(총예산=순투입 상한·건별 한도·허용 종목)를 초과하면 기계적 거부 | `payments/ap2_mandate.py` | ✅ 거부 4종 데모 |
| **x402 3단계 정산** | payment-required → submitted → completed. 브로커가 온체인 검증 후 자산 전달 | `payments/x402_solana.py` | ✅ 매수·매도 양방향 |
| **HTTP 402 레그 (G5)** | 브로커를 진짜 HTTP 자원 서버로 노출 — `POST /broker/orders` 가 결제 없으면 **402 Payment Required + accepts[]**, `X-PAYMENT` 헤더가 붙으면 200 + `X-PAYMENT-RESPONSE`. `GET /.well-known/x402` 디스커버리(미구현 항목 선공개). 1회용 청구서로 리플레이 차단, 온체인 정산 기본 잠김. 매수 레그 전용(매도는 방향이 반대라 A2A 인프로세스) | `web/broker_service.py`, `payments/x402_http.py` | ✅ test_http402 53건 · 실제 TCP 데모 `artifacts/x402_http/` |
| **매수·매도 풀사이클** | USDC⇄주식토큰 양방향 온체인 정산, 전후 잔액 RPC 교차검증 | `agents/broker_agent.py` | ✅ 순변화 PASS |
| **거부 4종 데모** | 건별한도 초과·mandate 위변조·금액 부족·미허용 종목 | `scripts/demo_rejections.py` | ✅ |
| **증빙 아카이브** | tx 해시·전후 잔액·교차검증을 JSON으로 저장 | `artifacts/tx/` | ✅ 7건(전부 localnet) |

### 1-2. AI 판단 (Gemini)

| 기능 | 설명 | 위치 | 상태 |
|---|---|---|---|
| **Gemini 매매 판단** | 무료 티어(`gemini-flash-lite-latest`). 시세·MA·변동성·평단 손익률·직전 회고를 보고 매수/매도/보류 + 한국어 이유 | `agents/gemini_decider.py` | ✅ 실패 시 규칙 폴백 |
| **판단 모드 토글** | 엄격(규칙 그대로) / 추세(보류 재량) | 세션 설정 | ✅ 백테스트 실측 |
| **규칙 게이트** | "규칙 신호 없는 개시 금지"를 프롬프트가 아니라 **코드로 강제** — AI 가 매수기준 미충족 매수·익절기준 미충족 매도를 내면 `hold` 로 강등하고 출처를 `rule-gate` 로 기록. 보류(멈추는 방향)는 항상 허용 | `agents/trading_agent.py` `_rule_gate` | ✅ test_rule_gate 28건 |
| **판단 출처 계측** | 세션의 `gemini`/`rule`/`rule-fallback`/`rule-gate` 집계 + 체결별 판단 출처를 tx 아카이브·세션 요약(Firestore)·`/api/state.ai` 에 기록 — 온체인 증빙에서 AI 관여분을 확인 가능 | `web/engine.py` `_ai_stats` | ✅ test_ai_stats 27건 |
| **TA 판단 보강** | MA 1~200일·크로스·기울기·지지/저항·차트/캔들 패턴을 판단 근거로 주입 | `market/indicators.py` | ✅ 기본 OFF, 단위테스트 44건 |
| **JSON 파서 견고화** | 코드펜스·잘못된 이스케이프·제어문자 정화 후 재파싱, 실패 시 1회 재요청 | `parse_decision_json` | ✅ 테스트 11건 |

### 1-3. 웹 대시보드 (FastAPI + SSE)

| 기능 | 설명 | 위치 | 상태 |
|---|---|---|---|
| **실시간 대시보드** | SSE 스트림, 새로고침 시 Last-Event-ID로 복원 | `web/server.py`, `web/events.py` | ✅ |
| **카드 모듈 11개** | price·session·position·budget·pnl·valuation·mandate·decisions·log·briefing·trades. 제목 드래그 재배치 + localStorage + 배치 초기화 | `web/static/` | ✅ 배치는 `DEFAULT_LAYOUT` 배열 |
| **캔들차트** | SVG 직접 렌더(외부 CDN 0), 양봉·음봉·MA선·현재가선·범례 | `app.js` | ✅ |
| **긴급정지/재개** | 신규 판단·결제 즉시 중단(진행 정산 1건은 마무리), 정지 주체 기록 | A2 | ✅ 세션 경계 처리 |
| **한도 설정(재서명)** | 예산/건별 한도 변경 → 새 mandate 서명 → 적용, 변경 이력 로그 | A3 | ✅ |
| **판단 타임라인** | [gemini]/[rule]/[rule-fallback] 배지, 세션 경계 구분선·출처 범례 | A6 | ✅ |
| **손익·평가손익·총자산** | 실현손익·수익률·미실현 평가손익·총자산(라이브는 온체인 잔액 병기) | A7 | ✅ |
| **수수료 투명화** | 견적에 [단가×수량 + 수수료 = 총액] 분리, 누적 수수료 = 브로커 수익 증명 | A8 | ✅ 0.3%(bps 조정) |
| **거래 알림** | 브라우저 알림(권한 상태 표시·차단 해제 안내) | A4 | ✅ |
| **데일리 브리핑** | 세션 데이터를 Gemini 한국어 리포트로(+장 마감 자동 1회), 실패 시 템플릿 폴백 | `web/briefing.py` | ✅ |

### 1-4. 데이터·전략

| 기능 | 설명 | 위치 | 상태 |
|---|---|---|---|
| **실데이터 재생** | 실제 미국 주식 일봉 CSV를 1틱=1봉으로 재생(워밍업 20봉, 소진 시 자동 종료) | `market/price_feed.py` (ReplayPriceFeed) | ✅ AAPL·TSLA·NVDA |
| **적립식(DCA)** | 조건형 / 적립형(틱·분·매일 시각 정액 매수), mandate 한도 동일 적용 | 전략 선택 | ✅ 테스트 7케이스 |
| **추세추종** | 상승세 전량 보유·하락세 전량 매도(자본 보존)·재상승 재매수. 올인/올아웃·복리·결정론 규칙 신호. **신호 4종**: 가격≥MA20 / 골든크로스5/20 / 1/5(가격≥MA5, 빠름) / 5/20+1/5 결합(빠른 손절). **멀티 종목**은 종목별 예산/N 슬라이스로 독립 격리 | 전략 선택 `Strategy.mode="trend"`·`trend_signal` | ✅ 재현검증 4신호×3종목 정확일치·웹 3종목 +77.55%. ⚠1/5·인트라바는 휩쏘로 수익↓ |
| **멀티 종목(동시 매수)** | 하나의 402 Guard 아래 N종목 독립 포지션. 조건형/적립형은 공유예산(1회=총 spend/N), **추세추종은 종목별 예산/N 슬라이스로 독립 올인·복리·완전 격리**(한 종목 손실이 남을 잠식 못함). 대시보드 종목 선택·포커스·종목별 요약, 백테스트 포트폴리오. 드라이 전용(라이브·목시세 멀티 거부), N=1 하위호환 | `web/engine.py`·`scripts/backtest.py --symbols` | ✅ test_multistock 37건·웹 3종목(추세 +77.55%·유출0) |
| **재생 속도·봉 간격** | 세션 시작 시 재생 속도 선택(0.15~8초/틱) + **봉 간격**(일봉 / 하루 N개 합성 인트라바 — 실 일봉 OHLC 경로, 마지막 종가=실 일봉 종가) | `web/engine.py`·`market/price_feed.IntradayReplayFeed` | ✅ test_intraday(481일 0불일치)·웹 e2e |
| **백테스트 러너** | 규칙 vs Gemini(엄격/추세) vs 추세추종 비교 + **매수후보유 벤치마크**·시장노출 (`--strategy trend --trend-signal --suffix _bear`) · `--symbols` 멀티 포트폴리오 · `--sub-bars` 인트라바 | `scripts/backtest.py` | ✅ 3종목 실측 |
| **데이터 수집** | Alpha Vantage 일봉(무료 25콜/일 보호) + 하락장 yfinance(`fetch_bear_data.py`) | `scripts/fetch_market_data.py` | ✅ |

### 1-5. 배포·영속화 (2026-07-23~24)

| 기능 | 설명 | 위치 | 상태 |
|---|---|---|---|
| **Firestore 영속화** | 세션·체결·브리핑·한도 기본값이 재시작 너머로 남음. 부팅 복원 | `web/store.py` | ✅ 로컬 기본 OFF, 테스트 18건 |
| **이력 조회 API** | `/api/history/sessions`(목록/상세)·`/trades`·`/briefings` | `web/server.py` | ✅ **⚠ 이걸 쓰는 화면이 아직 0개** |
| **Cloud Run 배포 구성** | Dockerfile(3.11-slim·TZ=KST) + Secret Manager 런북 | `Dockerfile`, [`docs/deploy_cloud_run.md`](deploy_cloud_run.md) | ✅ 실행만 사용자 대기 |
| **공개 배포 보안** | CONTROL_TOKEN 게이트(POST만)·한도 상한·경로주입 차단·라이브 잠금 | `web/server.py` | ✅ 공격 12종 차단 |

---

## 2. 변경 이력 (마일스톤별 — 이전 → 현재)

| 시기 | 마일스톤 | 이전 상태 | 현재 상태 |
|---|---|---|---|
| 07-21 | 킥오프 분석 | — | 해커톤 제약·심사 4축·검증 루틴 확정 |
| 07-22 | **MVP 코어** | 설계뿐 | A2A+x402 매수 전 과정 **localnet 온체인 확정**, 교차검증 PASS |
| 07-22 | 매도 + 거부 | 매수만 | **매도 사이클**(주식→USDC 환입) + 거부 4종. 예산=순투입 한도 해석 |
| 07-22 | Gemini 교체 | 규칙만 | Gemini API 판단 + 규칙 폴백 |
| 07-23 | **P1 웹 서비스화** | CLI(run_demo)뿐 | FastAPI+SSE 대시보드, 긴급정지·한도·타임라인·거래내역 |
| 07-23 | 버그 수정 라운드 | 4건 오류 | 긴급정지 세션경계·Gemini 파서·알림 권한·세션 구분 수정 |
| 07-23 | P2 엔진 확장 | 뼈대만 | 수수료 투명화·평가손익·캔들차트·DCA·브리핑 |
| 07-23 | **실시세 전환** | 목 시세(8스텝 반복) | ReplayPriceFeed(실제 일봉), 지표 규칙, 백테스트 러너 |
| 07-23 | TA 보강 | MA5/MA20만 | 이동평균 1~200·크로스·지지/저항·차트/캔들 패턴, 세션 토글 |
| 07-23 | 대시보드 모듈화 | 고정 레이아웃 | 카드 11개 드래그 재배치 + DEFAULT_LAYOUT 배열 |
| 07-23 | **Cloud Run 준비** | 인메모리·로컬 전용 | Firestore 영속화 + Dockerfile + 배포 런북 |
| **07-24** | **종합 검토** | 자평 | 조사 7축·감사 64건·검증 32건 → [`docs/preflight_review.md`](preflight_review.md) |
| **07-24** | 재현 차단 수정 | README 첫 명령 크래시 | cp949 안전화 + 백테스트 날짜 수정 |
| **07-24** | 벤치마크 추가 | 벤치마크 없음 | 매수후보유 대비 초과수익·시장노출 지표(3종목 실측) |
| **07-24** | **보안 5건** | 무인증 공개 API | CONTROL_TOKEN 게이트·한도 상한·경로주입 차단 등 |
| **07-24** | **재포지셔닝** | AutoTrader(트레이딩 봇) | **402 Guard(지출 승인 게이트)** — [`docs/differentiation.md`](differentiation.md) |

---

## 3. 앞으로 바뀔 것 (402 Guard 재포지셔닝, 미구현)

> 상세·공수·순서는 [`docs/differentiation.md`](differentiation.md) §2, [`docs/handoff.md`](handoff.md) §5.

| # | 변경 | 왜 | 현재 결함 |
|---|---|---|---|
| **G0** ✅ | 사용자 키 분리(`secrets/user.json`) — **완료 (커밋 329885f, 2026-07-24)** | 에이전트가 자기 허가서를 자기 키로 서명하던 문제 해결 — 위임 서사가 코드에서 사실이 됨 | 해결됨 (검증: 드라이런·엔진 스모크·테스트 4종) |
| **G1** ✅ | `payments/guard.py` — check_demand 4항목 + check_delivery 온체인 재조회 (커밋 89cde31) | 결함 B·C 를 이 계층에서 닫음 | 해결 (단위 13종) |
| **G2** ✅ | 결제 경로 결선 + `allowed_asset` 살리기 + release/settle 한도 원복 (커밋 9701d90) | authorize 앞 guard, 실패 시 예약 원복(H), partial 배송 처리(I) | 해결 (스모크 예약회계 일치) |
| **G3** ✅ | `scripts/red_team.py --report` — 공격 3종 + 매트릭스 + 오탐 0 (커밋 7e2f3b8) | 실측 시도18·차단4·유출0.00·오탐0 | 해결 |
| **G4** ✅ | Memo 바인딩(AT1) + `exact` 정합(`!=`) + 서명 dedup + expires_at (커밋 9523d19) | 대사 키·리플레이·초과지불 방어(D·E) | 해결 (localnet 풀사이클 PASS) |
| G5 | 브로커 HTTP 402 분리(매수 경로) | HTTP 402가 코드에 0줄 | `engine.py:750` 인프로세스 |
| G6 | pay.sh/유료 데이터 402 엔드포인트 | 심사 3축 가점(필수 아님 — 공식 기준상 병렬 예시) | — |
| — | devnet 실증 + explorer 증빙 | 증빙 7건 전부 localhost | `artifacts/tx/` |

---

## 4. 잘라낸 기능 (범위 제외 — 되돌린 이유 명시)

| 기능 | 이유 |
|---|---|
| 전략 고도화(손절·트레일링·Gemini 사이징) | 수익률로는 어차피 벤치마크에 지므로 승부 축을 바꾸는 게 우선(07-24 결정) |
| Firebase Auth + 사용자별 세션 분리 | CONTROL_TOKEN 게이트로 데모 범위는 충족. 엔진이 전역 싱글턴이라 별도 라운드 필요 |
| ~~멀티 종목 전면 확장~~ → **드라이+대시보드+백테스트 범위로 구현 완료(2026-07-25)** | 라이브 온체인 멀티만 제외(종목별 민트 발행 무거움). "다중 지출을 하나의 402 Guard 로 통제" 서사·분산 하방 축소로 채택 |
| P3 에이전트 챗(B6), 설정 페이지, 리플레이 전용 페이지 | 중복이거나 마감 리스크 |
| 프롬프트 인젝션 데모 | 브로커 텍스트가 Gemini 프롬프트에 안 들어감 — 표면을 새로 만들어 막는 자작극 |
| 가드 UI 카드(G8) | P2로 하향 — 터미널 + 기존 대시보드로 촬영 가능 |

---

## 5. 심사 4축 ↔ 기능 매핑 (공식 기준, 2026-07-24 확보)

| 축 | 공식 문구 | 우리 증거 | 강도 |
|---|---|---|---|
| ① 혁신성·UX | 직관적·새로운 UX, 기존 문제 해결 | 402 Guard 재포지셔닝, 공격 콘솔, 첫 화면 KPI(수익률 아님) | 재포지셔닝으로 상승 |
| ② AI 활용도 | Gemini/Google Cloud AI 스택(에이전트 프레임워크 포함) | Gemini 실호출 3지점(판단·**청구서 의미 대조**·브리핑). AI 재량은 두 레이어 모두 **차단만** 가능. 481봉 대표본 실측 + 규칙 게이트 발동 로그 2건 | **강**(2026-07-27 심사 부서) |
| ③ 기술·인프라 연동 | USDC·Solana Pay·pay.sh 등, AP2·A2A·x402 등 | 자체 x402 + AP2 + A2A(**병렬 예시라 정합**). devnet tx 를 공용 RPC 로 재조회해 Memo·금액 일치 확인. 라이브 URL 이 실제 402+accepts[] 응답 | **강** — 단 증빙의 결제 통화가 테스트 민트(Circle 공식 민트 재실증 필요) |
| ④ 실제 구동 | 로컬넷/테스트넷/데브넷 라이브 트랜잭션 | localnet 풀사이클 + **devnet 온체인 tx 10건**(`artifacts/tx/20260724_1643_*`, err=null·교차검증 일치) · 테스트 20종 통과 | **강** — 단 devnet 증빙 1건뿐이고 배포 URL 발생 온체인 tx 0건 |
