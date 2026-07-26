# 경쟁 조사 — 해커톤 수상작·실패작 (2026-07-27)

> **목적**: 떨어진 프로젝트들의 실패 원인을 알아내 402 Guard 의 약점을 선제적으로 메운다.
> **방법**: 워크플로우 병렬 분산 — 6개 조사 각도 × (웹 검색 → 원문 정독 → 건별 적대적 검증) → 종합 → 완전성 비평.
> 에이전트 14개, 웹 도구 호출 469회, 오류 0, 소요 48분, 서브에이전트 토큰 160만.
> **원본**: 세션 워크플로우 `wf_c7cf2251-244`. 이 문서는 그 산출물 전문을 보존한 것이다(요약본 아님).

---

## 0. 신뢰도 경고 — 이 문서를 인용하기 전에 반드시 읽을 것

1. **이 해커톤([Google Cloud X Solana] AI 에이전틱 해커톤)의 수상작·탈락작·심사평은 존재하지 않는다(확인).**
   대회가 2026-07-17 공고되어 최종 제출 2026-08-03, 데모데이 8/21 이므로 **결과 자체가 아직 없다.** 지금이 1회차다.
   따라서 본 조사는 전부 유사 해커톤으로 대체했다.

2. **조사한 12개 해커톤 전부에서 프로젝트별 심사평이 비공개다(확인).**
   Colosseum 4개 발표문, theblockbeats, Algorand 리캡, SKALE 리캡, Cronos 기사, Devpost 수상 페이지 어디에도 "이 팀이 왜 이겼는가"가 없다.
   **심사 근거로 확정 인용 가능한 것은 단 4건뿐이다:**
   - ① Cronos 1위 AgentFabric 의 `"By solving the critical security trade-off for agentic autonomy"`
   - ② Colosseum Frontier Top 25 공통 기준 문장 `"execution speed, insight, founder-market fit, prioritization, and overall talent"`
   - ③ SKALE SF 해커톤 심사 4축 (partner integration / agentic depth / commerce realism / polish·ship-ability)
   - ④ 제미나이 3 서울 해커톤에서 심사위원이 1위 참가자에게 "외부 API 를 섞어 썼나"를 **두 번** 물었다는 blog.google 기록
   
   **그 외 모든 "왜 이겼다/졌다"는 결과(순위·상금·카테고리 배정)에서 역산한 추정이며, 본 문서에서 `(추정)` 으로 표기했다. 소개서·발표에 사실로 인용하지 말 것.**

3. **명시적 '탈락작' 데이터는 구조적으로 존재하지 않는다.** 해커톤은 낙선 사유를 공개하지 않는다. Devpost 갤러리에서 '수상 배지 없음'으로 역추적하는 것이 유일한 방법이다. 유일하게 낙선 사유가 1인칭으로 기록된 것은 Substack 회고 1건인데, 심사위원 발언이 아니라 낙선자의 사후 자기진단이다.

4. **이 대회에 대한 정보는 전부 2차 매체다.** 공식 페이지·디스코드 원문 확인이 반드시 필요하다.

5. **§9 완전성 비평에서 URL 없이 단언된 제품명 다수가 적출됐다**(CardZero, PlaiPin, World of Geneva, Volt402, x402Resolve, TruthKeeper, GreenOps, CompilanceOS, Edu.AI, DashX, CargoBill). 인용 전 개별 확인 필요.

### 조사 대상 12개 해커톤

| 계열 | 대회 |
|---|---|
| Solana / Colosseum | Frontier 2026, Cypherpunk 2025, Breakout 2025, Radar 2024 |
| x402 특화 | Solana x402 2025, EF x402 2025-12~2026-01, Cronos x402 PayTech 2026-02, SF Agentic Commerce x402 2026-02, Agentic Commerce x402 Berlin 2026-06 |
| Google Cloud / Gemini | ADK Hackathon 2025, Rapid Agent 2026, GKE Turns 10 2025, 제미나이 3 서울 2026, Gemini API Developer Competition 2024 |
| 시장 조사 | 에이전트 지출통제 상용 제품군 |

---

## 1. 이 대회 자체에 대해 새로 확인한 사실 ★ 최우선

전부 2차 매체(Solana Compass, Crypto Briefing, 케이에스넷 보도) 기반. **공식 재확인 필수.**

| 항목 | 확인 내용 | 출처 |
|---|---|---|
| **정식 주제** | "Build the Future of Agentic Commerce" = AI 에이전트가 **클라우드 API 를 발견·인증·결제**한다. 공식 예시 유스케이스: *"에이전트가 Gemini 추론 호출 시 HTTP 402 응답 감지 → 가격 파싱 → USDC 차감 → 서명 → API 접근"* | solanacompass |
| **핵심 과제 원문** | `"building agents that can discover, authenticate, and pay for cloud API services via the x402 protocol, without any human involvement in the payment step."` | solanacompass |
| **결제 스택** | **pay.sh 명시.** 매체 서술: `"Developers must build AI agents using Pay.sh (the payment proxy), x402 protocol, and USDC on Solana."` 기사들은 pay.sh 를 "the payment stack for the exercise" 로 서술 | solanacompass, cryptobriefing |
| **API 카탈로그** | Gemini · BigQuery · Cloud Run · Dune · Nansen · Helius · Alchemy · Quicknode · AgentMail | solanacompass |
| **상금** | 총 $135,000, 트랙당 최대 $20,000 | solanacompass |
| **공동주최** | **Superteam Korea** (`"co-organized with Solana Super Team Korea"`) | solanacompass |
| **기관 파트너** | **케이에스넷 (국내 PG사)** | 스포츠피플타임즈 |
| **일정 3단계** | 8/3 = **본선 진출 스크리닝** → **8/10~20 집중 멘토링** → **8/21 데모데이(본선)**, 장소 구글 스타트업 캠퍼스 | 스포츠피플타임즈 |
| **심사 서술** | "기술력과 사업성을 모두 고려" | 스포츠피플타임즈 |
| **대회 성격 서술** | "사용자가 일일이 승인할 필요 없이 AI 에이전트가 **정해진 한도 내의 결제**를 알아서 처리하는" 에이전틱 커머스. 기술 스택은 **Gemini + Solana Pay** 로 언급(이 기사에는 pay.sh 언급 없음) | 스포츠피플타임즈 |
| **선행 회차** | 동일 주최진이 **2025-04 서울에서 Seoulana Hacker House 개최** (seoulana-hacker-house.devfolio.co). 수상작 명단 확보 실패 | Devfolio |
| **공고일** | 2026-07-17 | solanacompass |

### 이 사실들의 함의

- **(가) 일정 오독 수정**: 8/3 은 최종 마감이 아니라 **본선 진출 스크리닝**이고 그 뒤에 11일짜리 멘토링이 붙는다. → 8/3 까지는 "스크리닝을 통과시키는 최소 완성"에 자원을 몰고, **프론트 시안 적용(공수 L)은 멘토링 기간으로 미루는 것이 합리적**이다. 기존 계획(프론트 최우선)은 "7일 뒤가 끝"이라는 잘못된 전제에서 나온 배치다.
- **(나) 주제 적합성은 우려보다 좋다, 그러나**: "정해진 한도 내의 결제"가 **대회 자체의 주제 서술**이다. 402 Guard 의 주제 적합성은 좋다. **문제는 적합성이 아니라, 그게 대회의 기본 전제라서 차별점이 되지 못한다는 것이다.**
- **(다) 심사진 구성**: Superteam Korea 공동주최 + PG사 파트너 + "기술력과 사업성" → (i) Colosseum 식 창업 관점(founder-market fit·지속 가능성) (ii) **결제 실무 관점(정산·수수료·규제)** 이 동시에 들어온다. **규제 질문(무등록 투자일임, 자금 분리보관)이 나올 확률이 매우 높다.**
- **(라) pay.sh 리스크 재계상**: CLAUDE.md 는 "공식 심사 기준이 pay.sh 를 병렬 예시로 나열하므로 필수 아님"으로 정리했고 그 판단은 여전히 유효하나, **홍보 문구는 "must" 다.** 리스크 강도를 "축③ 실점"에서 **"스크리닝 탈락 가능성"** 으로 한 단계 올려야 한다.

---

## 2. 수상작 (12건)

### 2-1. Sudont — Solana Frontier Hackathon 2026 (Colosseum) Top 25 / 2,857개 제출
<https://blog.colosseum.com/announcing-the-winners-of-the-solana-frontier-hackathon/>

(확인) 공식 설명 원문: `"An agentic crypto security platform providing bare-metal execution firewall and local RPC on Solana."` 제품 사이트 확인: 드롭인 RPC 프록시가 모든 트랜잭션을 체인에 닿기 전 로컬 샌드박스(revm/LiteSVM)에서 실행해 State-Diff 를 계산. `"Simulation runs inline with your RPC call, not a round-trip to a SaaS cluster, so it completes inside the agent's decision window"`.
(추정) 개별 심사평 없음 — 에이전트 자율 지출 시대의 사고 방지라는 시의성 + 레이턴시 난제 정면 돌파.

> **시사점**: 402 Guard 의 문제 정의는 이미 최상위에서 검증됐다 = 안전하되 **'세계 최초' 주장은 즉시 반박당한다.** 차별점은 층위로 말해야 한다 — Sudont = 트랜잭션 시뮬레이션(체인·프로토콜 무관), 402 Guard = **x402 결제 의미론 검증**(수취인 allowlist · 청구서 대 합의견적 대조 · AP2 한도 · 의도 상한).

### 2-2. AgentFabric — Cronos x402 PayTech Hackathon 2026-02-28, **1위 $24,000 CRO + Builder Residency** (191팀)
<https://www.mexc.co/en-PH/news/815826>

(확인) **수상 근거가 이례적으로 명시된 유일한 사례**: `"By solving the critical security trade-off for agentic autonomy"`. 제품은 '스코프가 지정된 프로그래머블 권한(scoped, programmable permissions)'으로 자율 에이전트가 자본을 **마찰 없이(zero friction)** 이동시키게 하는 레이어. 즉 안전을 제약이 아니라 **자율성을 가능케 하는 조건**으로 프레이밍.

> **시사점**: 본 조사 전체에서 402 Guard 카피에 **가장 직접적인 처방.** '막는다·차단한다'가 아니라 **'한도가 있어서 비로소 사람 없이 맡길 수 있다'** 로 재서술할 것. 1위 문구를 그대로 벤치마킹 가능.

### 2-3. AutoSRE — Google Cloud Rapid Agent Hackathon 2026 (Dynatrace 트랙) Winner
<https://rapid-agent.devpost.com/project-gallery>

(확인) 태그라인 원문: `"AutoSRE is an autonomous on-call agent that diagnoses Dynatrace incidents in seconds and queues up the fix, but cannot touch production without your one-tap approval."` 능력 선언 뒤에 '넘을 수 없는 선'을 **같은 문장에** 붙였다. (추정) 이것이 수상 사유라는 인과는 미공개.

> **시사점**: 자율 에이전트에게 통제 장치는 감점이 아니라 **셀링 포인트로 실제 작동한다.** 402 Guard 첫 문장을 2단 구조로: *"에이전트가 사람 없이 24시간 매매·정산한다. 단 사용자가 정한 한도와 수취인 밖으로는 1원도 나갈 수 없다."*

### 2-4. Cassandra — Google Cloud Rapid Agent Hackathon 2026, Arize 트랙 1위
<https://devpost.com/software/cassandra-jilmgy>

(확인) 4가지 설계가 원문에서 확인됨:
1. **은유 네이밍** — `"the Greek prophet cursed to always see the truth, while nobody believed her"`
2. **문제 규정** — `"AI agents fail silently, confidently wrong, and nobody notices"`
3. **hand-labeled 'trap library'** 로 자기 진단 정확도를 자가 채점
4. **시간 약속** — `"The whole loop runs live, on camera, in under a minute."`

스택: Gemini 2.5 Flash Lite + ADK + Cloud Run + Firestore.

> **시사점**: 402 Guard 의 `scripts/red_team.py`(시도·차단·유출 0.00·오탐 0)가 **'trap library' 와 정확히 같은 역할**이다 = 첫 화면 전면 배치의 근거. 그리고 데모에 **'60초 안에 카메라 앞에서 전 루프가 돈다'** 는 시간 약속을 넣어라.

### 2-5. MCPay — Solana Cypherpunk Hackathon 2025, Stablecoin 트랙 1위 $25,000 USDC (1,576개 제출)
<https://blog.colosseum.com/announcing-the-winners-of-the-solana-cypherpunk-hackathon/>

(확인) 공식 설명: `"open payment infrastructure connecting MCP and x402"`. x402 를 새로 구현한 것이 아니라 **이미 쓰이는 툴 생태계(MCP)에 연결**했다. (추정) 심사평 없음.

> **시사점**: **'자체 x402 구현'을 성과로 내세우지 말 것.** 심사에서 값이 매겨진 것은 구현이 아니라 **연결**이다. 402 Guard 는 'A2A 협의 + AP2 위임장 + x402 정산을 잇는 사는 쪽 보호막'이라는 **결합 서사**로 말해야 한다.

### 2-6. Erster — Agentic Commerce x402 Hackathon Berlin 2026-06-06~07, Agentic Commerce(Existing) 1위 $3,000 + Folks Finance 보너스 $500 (42개 제출)
<https://algorand.co/blog/agentic-commerce-x402-hackathon-berlin-recap>

(확인) 태그라인: `"Pay-per-evaluation trust check for finance agents."` **검증 자체를 건당 과금 상품으로 판다.**
같은 페이지 조직위 총평(확인): `"Many teams focused on trust: a core challenge for any economy where agents pay other agents."` / `"Teams explored this through reputation systems, validation layers, trust routers, and audit trails."`

> **시사점**: 신뢰·검증이 2025-12~2026-06 x402 판의 **지배 주제**다 = 402 Guard 의 주제 선택은 옳다. **문제는 주제가 아니라 혼잡도.** 또한 '검증을 건당 과금으로 판다'는 x402 자체와 정합적인 수익모델이라 **소개서 수익모델 대안으로 검토 가치 있음.**

### 2-7. JailbreakMe — Solana AI Hackathon (SendAI) 2025, Main Track 종합 3위 $20,000 (400+ 제출)
<https://cryptotvplus.com/2025/01/sendai-announces-winners-of-solana-ai-hackathon/>

(확인, 2차 매체) AI 에이전트 보안 테스팅 플랫폼 — 사용자가 프롬프트 탈옥으로 에이전트 보안을 시험하는 공개 검증장. (추정) 심사평 없음.

> **시사점**: '에이전트는 실제로 뚫린다'를 정면 주제로 삼아 AI 해커톤 종합 3위 = **402 Guard 문제 정의의 외부 근거.** 인용 시 1차 출처가 아니라 매체임을 명시할 것.

### 2-8. x402r (BackTrackCo) — Ethereum Foundation x402 Hackathon (2025-12-08~2026-01-05), 수상작 3건 중 1건 (재정적 상금 없음)
<https://phemex.com/news/article/ethereum-foundation-reveals-x402-hackathon-winners-54373>

(확인) x402 로 결제했으나 데이터 서비스가 배송되지 않은 경우의 **환불 요청 처리 도구.** EF 는 '프로토콜 위에 인프라와 툴을 짓는 프로젝트'를 뽑았다고만 밝힘.

> **시사점**: EF 가 **'구매자 구제'를 수상작으로 뽑았다** = 사는 쪽 보호는 x402 커뮤니티가 공인한 공백이다. 402 Guard 의 `check_delivery` 와 같은 문제. 단 x402r·x402Resolve 는 **사후 환불**이므로 **"사후 분쟁으로는 늦다, 우리는 서명 전에 막는다"** 로 대비시켜야 포지션이 선다.

### 2-9. cart-to-kitchen AI assistant on GKE (Amie Wei) — GKE Turns 10 Hackathon 2025, **Grand Prize** (4,773명 등록·133개국·133개 제출)
<https://devpost.com/software/cart-to-kitchen-gke-ai-assistant>

(확인) 장바구니를 분석해 레시피를 추천하는 AI 쇼핑 도우미. Gemini + Imagen + GKE Autopilot + ADK + A2A, Google 의 online-boutique **레퍼런스 앱을 확장**해 Recipe Service 를 추가. 수상 후 KubeCon NA 2025 초청·라이트닝 토크·theCUBE 출연.
(추정) 심사 사유 미공개. 블로그 표현은 `"creativity, technical mastery, and deep understanding of GKE and AI"` 수준.

> **시사점**: **그랑프리가 '장바구니로 레시피 추천'이라는 한 문장 제품**이었다. 같은 대회에서 용어를 쌓아올린 GuardianOS 는 무수상 = **설명 난이도가 기술 깊이보다 강하게 작동한다.** 또한 '레퍼런스 앱을 확장'한 구조는 402 Guard 도 쓸 수 있다 — *"x402 표준 흐름을 그대로 두고 사는 쪽 보호만 얹는다"*.

### 2-10. GeminiSpace (장민수) — 제미나이 3 서울 해커톤 2026-02-28 (Google Korea) 1위 (1,515명 지원 → 219명 참가 → 111개 제출)
<https://blog.google/intl/ko-kr/company-news/inside-google/gemini-seoul-hackathon-first/>

(확인 — **본 조사에서 유일하게 심사 질문이 원문으로 남은 사례**) 기술 데스크 심사위원이 *"구현을 위해 혹시 다른 외부 API들도 섞어 썼나요?"* 라고 물었고, *"아닙니다. 백지상태에서 오직 제미나이 API 만 사용해서 만들었습니다"* 라는 답에 심사위원이 "크게 놀라"는 반응. **본 심사에서도 "정확히 똑같은 질문"** 을 받았다. 7시간 만에 GCP 상 구동 형태로 완성.

> **시사점**: 한국 Google 심사진의 실제 관심사가 기록된 유일한 표본이고, **같은 질문이 두 번 나왔다 = 우연이 아니라 기준이었다.** 402 Guard 는 **'왜 Gemini 여야 했는가'를 한 문장으로 답할 수 있어야 한다.**
> ⚠ 단, 그 대회는 Gemini 단일 대회였고 이 대회는 인프라 연동이 별도 축이므로 'Gemini 만 써야 한다'로 **과잉 일반화 금지.**

### 2-11. Agent Arc — Solana Breakout Hackathon 2025 (Colosseum), AI 트랙 3위
<https://blog.colosseum.com/announcing-the-winners-of-the-solana-breakout-hackathon/>

(확인) **논커스터디얼(non-custodial) AI 트레이딩 터미널** — 사용자 키를 위탁받지 않는다. (추정) 심사평 없음.

> **시사점**: **'트레이딩 봇은 무조건 탈락한다'는 전제의 반례.** 키 비위탁이라는 안전 서사가 붙으면 AI 트랙 3위까지 간다 = 402 Guard 의 self-custody·위임장 설계와 같은 축이므로 **매매 기능을 숨길 필요는 없다.**

### 2-12. Peaks / Clawpump — Solana Frontier Hackathon 2026, 둘 다 Top 25
<https://blog.colosseum.com/announcing-the-winners-of-the-solana-frontier-hackathon/>

(확인) Peaks 원문: `"A consumer app for investing in any idea. Using agents, investors can launch self-managing portfolios based on a sector, personality, or worldview."`
Clawpump 원문: `"An agentic finance platform ... skilled across swaps, sniping, lending, perp DEX arbitrage, prediction markets, and a long-tail of automations."`
**둘 다 본질은 자동 운용인데 수상했다.**

> **시사점**: 정확한 명제는 '자동매매라서 떨어진다'가 아니라 **'단일 전략 봇 언어로 말하면 떨어진다'** 이다. Peaks 는 **컨슈머 앱 언어**로, Clawpump 는 **실행 레이어 언어**로 말했다. 402 Guard 의 재포지셔닝(수익률 대신 통제 KPI 를 첫 화면에)은 유지하되 **매매를 숨기지는 말 것.**

---

## 3. 실패작 (14건)

### 3-1. ★ GuardianOS — ADK Hackathon with Google Cloud 2025, **제출·무수상** (476개 제출)
<https://devpost.com/software/guardianos>

(확인) 페이지에 **수상 배지가 없다**(수상작 페이지에는 'Winner …' 배지가 보이는 것과 대조).
갖춘 것: 블록체인 트랜잭션용 멀티에이전트 컴플라이언스 / 저위험 2초 자동승인 · 중위험 5개 에이전트 3-of-5 합의 · 고위험(7.5만 유로 초과) 10개 에이전트 4단계 'Tenth Opinion Protocol' / 선택적 신원 공개 / **Sepolia 스마트컨트랙트 실배포** / **guardianos.vercel.app 라이브 URL** / ADK 멀티에이전트.
즉 **기술 깊이 · 온체인 실배포 · 라이브 URL 을 다 갖추고 무수상**이다.

(추정 — 심사평 미공개) 감점 후보:
① 전문용어 과적재(selective de-anonymization, Tenth Opinion Protocol, 3-of-5 합의)로 비전문 심사자가 3분 안에 가치를 못 잡음
② 타겟이 블록체인 개발자·컴플라이언스 담당·핀테크로 분산
③ 복잡도 자랑이 '누가 왜 쓰는가'를 덮음

> **★ 본 조사 최대 경고 신호.** 402 Guard 와 문제 영역이 사실상 동일하다(온체인 거래에 승인 게이트를 씌운다).
> 교훈은 '기술을 더 쌓아라'가 아니라 **'용어를 줄이고 사용자를 하나로 좁혀라'.**
> x402 · AP2 · A2A · mandate · rule-gate 를 첫 화면에 동시에 늘어놓으면 같은 함정. **첫 화면은 한 문장 + 숫자 4개로 끝내라.**

### 3-2. TradeSage AI — ADK Hackathon 2025, **Honorable Mention** (최상위 실패)
<https://devpost.com/software/tradesage-ai>

(확인) 멀티에이전트 금융 분석 플랫폼. 6개 에이전트 협업, ADK + Vertex/Gemini, Cloud SQL(pgvector) RAG, React, Alpha Vantage·Yahoo Finance·FMP 실시세, Cloud Run 배포 — **기술 스택·배포·실데이터를 전부 갖췄는데 Honorable Mention.**
이 대회 배점(규정 원문 확인): **Technical Implementation 50% / Innovation 30% / Demo·Documentation 20%.**
(추정) 자율 매매 실행을 아예 빼고 '분석·평가'로 한정했는데도 상위권에 못 갔고, 반대로 리스크·규제·감사 추적 서술이 없어 '왜 이게 안전한가'를 설명하지 못했다.

> **시사점**: 트레이딩 도메인 + 멀티에이전트 + GCP 풀스택 + 실시세 + Cloud Run 을 다 갖춰도 **천장이 Honorable Mention.** 402 Guard 가 '수익률·전략 성능'으로 경쟁하면 같은 천장에 부딪힌다. 차별점은 이 팀이 비워둔 칸 — **안전·감사 추적** — 에 있다.

### 3-3. Vigil AI (Ayan Liger) — GKE Turns 10 Hackathon 2025, **Honorable Mention**
<https://cloud.google.com/blog/topics/developers-practitioners/winners-and-highlights-from-gke-hackathon>

(확인) Bank of Anthos 대상 계층형 멀티에이전트 사기 탐지 — 4개 전문 에이전트(TransactionMonitor, Orchestrator, Investigation Agent, Actuator)가 의심 활동을 표시하고 Gemini 로 조사한 뒤 필요 시 계정을 잠근다. **기존 애플리케이션 코드를 수정하지 않고** 동작.
같은 대회 그랑프리는 '장바구니로 레시피 추천'이었다. (추정) 심사평 미공개.

> **시사점**: **금융 거래 감시 + 자동 차단이라는 402 Guard 와 가장 가까운 프레이밍이 Honorable Mention** 이었고, 훨씬 단순한 소비자 제품이 그랑프리였다. '사기 탐지·감시' 프레이밍만으로는 최상위가 어렵다.
> 다만 **'기존 흐름을 바꾸지 않고 감시층만 얹는다'는 구조 서술은 402 Guard 가 그대로 차용할 만하다.**

### 3-4. ★ Latinum — Solana Breakout 2025 AI 트랙 **1위 $25,000** → Solana Frontier 2026 **명예언급(Top 25 진입 실패)**
<https://blog.colosseum.com/announcing-the-winners-of-the-solana-frontier-hackathon/>

(확인, 양쪽 공식 페이지 대조) 'MCP 빌더를 위한 결제 미들웨어'로 2025년 AI 트랙 1위였으나, 2026 Frontier 에서는 Top 25 에 없고 Honorable Mentions 16개에만 등재.
같은 기간 제출작은 **1,412건 → 2,857건으로 두 배**(양쪽 확인). (추정) 순위 하락 원인은 어디에도 공개되지 않았다.

> **★ 범주 소진의 실증.** **'에이전트 결제를 가능하게 한다'는 범주는 이미 소비됐다.**
> 402 Guard 가 '에이전트가 자율 결제하게 해준다'로 읽히는 순간 1년 전 1위가 명예언급으로 내려앉은 그 자리에 들어간다. **'지출을 막는다'는 반대 방향을 전면에 세워야 한다.**

### 3-5. ★ x402 Intent Firewall — Cronos x402 PayTech 2026-02, 메인 트랙이 아닌 **'Best Dev Tooling Layer' $3,000** (1위 AgentFabric 은 $24,000)
<https://www.mexc.co/en-PH/news/815826>

(확인) 설명 원문: `"a critical sanity check for programmatic payments"` — 결제 의도를 필터링해 악의적 실행을 차단.
즉 **402 Guard 의 핵심 기능과 개념이 거의 동일한데 메인 트랙이 아니라 개발자 도구 카테고리로 분류돼 1위 상금의 8분의 1을 받았다.**
(추정) 카테고리 배정이 심사위원 판단인지 팀의 자기 신청인지는 확인되지 않는다.

> **★ 402 Guard 의 최대 형태 리스크를 그대로 예시한다.** '게이트·방화벽·sanity check'만 내세우면 **제품이 아니라 라이브러리로 분류**된다.
> 같은 대회 1위는 같은 안전 문제를 다루면서도 '자본을 마찰 없이 움직이게 하는 레이어'로 말했다. **대시보드·실 체결·수익까지 붙은 완결 제품으로 보여야 한다.**

### 3-6. Cronos Shield — Cronos x402 PayTech 2026-02, **2위 $5,000** (1위의 5분의 1)
<https://www.mexc.co/en-PH/news/815826>

(확인) `"a sensitive layer of safeguard for agent-driven transactions"` + 자동 리스크 관리 엔진. **순수 방어 도구로서 이 대회 최고 성적이 2위·$5,000.** (추정) 심사평 미공개.
⚠ 원 수집본에 있던 '자율성이 자산 안전을 대가로 치르지 않게 한다'는 인용 문구는 원문 대조에서 확인되지 않아 **폐기했다.**

> **시사점**: **'리스크 엔진 단독 포지셔닝'의 실측 천장이 2위.** 1위와의 차이는 기능이 아니라 프레이밍이었다(1위 = 안전을 자율성의 인에이블러로, 2위 = 안전을 보호막으로).
> 402 Guard 는 여기에 **실제 온체인 정산·체결 실물**을 더해야 2위 천장을 넘는다.

### 3-7. ★ (이름 비공개) Isolation Forest + FastAPI 네트워크 이상탐지 — 이름 없는 24시간 해커톤, 저자 자기보고 "we lost"
<https://shrit.substack.com/p/lost-so-bad-got-humble-again> (게시일 2026-03-16, 대회명·주최 원문에 없음)

(확인 — 단, 심사위원이 아니라 **낙선자의 사후 자기진단**) 원문 인용:
> `"We coded almost everything using Claude...I didn't really understand them myself."`
> `"When the judges asked questions I couldn't answer properly."`
> `"The real mistake was simple. I trusted the AI generated code without fully reading or understanding it."`

프로젝트와 발표 자체는 견실했고 `"the win felt very close"` 였으나 **질의응답에서 무너졌다.**

> **★ 402 Guard 에 직접 해당.** 코드 대부분이 Claude 생성이다. 심사 질의(가드 4항목 검증 순서, mandate 와 가드의 관계, 402 레그, rule-gate 가 Gemini 판단을 강등하는 지점)를 **사용자가 문서 없이 직접 설명**할 수 있어야 한다. **데모데이 8/21 라이브 Q&A 가 실제 위험 구간.**

### 3-8. Team 112 — LeRobot Worldwide Hackathon (Hugging Face) 서울 로컬 회장 SK mySUNI, 2025-06-14~15, 최종 수상 여부 확인 불가
<https://sudormrf.run/2025/06/24/huggingface-lerobot-hackathon-review/>

(확인) 오후 1시 글로벌 온라인 발표에서 완벽 수행한 다단계 로봇 조작 태스크가, **같은 환경·같은 모델로 진행한 오후 5시 서울 현장 데모에서 실패.**
원문: *"오후 1시 글로벌 발표에서는 잘 되었던 모든 것이 오후 5시 서울만의 현장 데모에서는 잘 안되었습니다."* 원인 후보는 스크류 이완·구버전 메모리 누수·모터 과열.
(추정) 이 대회는 영상 제출 방식이었으므로 현장 실패가 수상에 영향을 줬는지는 원문에 없다.

> **시사점**: 라이브 시연 리스크의 **한국 현장 실증.** 402 Guard 는 devnet 공용 RPC · Gemini 무료 티어 쿨다운 · Cloud Run 콜드스타트라는 **3중 외부 의존**이 있다. **데모데이 8/21 폴백(녹화 영상 + 로컬넷 재시연 경로)은 선택이 아니라 필수.**

### 3-9. 미완성·플레이스홀더 제출물 (Code Analyzer / arkhan / XML FIle reader / Project) — ADK Hackathon 2025 갤러리 (476개 중), 무수상
<https://googlecloudmultiagents.devpost.com/project-gallery?page=19>

(확인) 태그라인이 자리표시자 그대로 굳었다 — Code Analyzer: `"Shot your link or github here"`, arkhan: `"the evolution of ai"`, XML FIle reader: `"Using this tool, you can read the xml file."`, Project: `"Elevator pitch"`.
대회 규정 원문 확인: Stage One 은 pass/fail 스크리닝(`"determine via pass/fail whether the Submission meets a baseline level of viability"`)이고 **필수 제출물 5종**은 호스팅 URL · 텍스트 설명 · **공개 코드 저장소 URL** · **아키텍처 다이어그램** · YouTube/Vimeo 데모 영상.

> **시사점**: 바닥선 확인용. 402 Guard 는 이 선을 넘지만, **아키텍처 다이어그램이 명시적 필수 제출물**이라는 점은 실무 체크리스트로 유효하다(이 대회도 소개서에 아키텍처를 필수로 요구). **476개 중 상당수가 이 선에서 걸러진다 = 완주 자체가 상위권 진입 조건.**

### 3-10. 일반형 클론 제출물 (AI 튜터·플래너·범용 비서 계열) — ADK Hackathon 2025 갤러리, 무수상
<https://googlecloudmultiagents.devpost.com/project-gallery?page=10>
직접 표본: EduGenie, LearnBridge, cahaya_ai, AI Personal Planner, AI-enabled Life Tracker, JarvisFlow, SmallBizPal, Tvara, AgentPM

(확인) 서로 구분이 어려운 '멀티에이전트 튜터/플래너/범용 비서'가 반복 등장. 중립 표본(page 10) 실측 **24개 중 6개(25%)**.
⚠ **정정**: 원 수집본의 '절반 가까이'는 과장이며 실측은 25%다.
(추정) 혁신·창의 30% 축에서 '같은 프로젝트에 라벨만 다른 것'으로 분류되면 점수를 받기 어렵다 — **배점표에 근거한 추정이지 심사 근거가 아니다.**

> **시사점**: 카테고리 진부함이 감점이라는 방향성은 유효하나 **근거가 가장 약한 항목이므로 소개서·발표에 수치나 인용으로 쓰지 말 것.** 대신 근거가 단단한 GuardianOS(무수상)·TradeSage(HM)·Vigil AI(HM) 를 쓸 것.

### 3-11. SingIt — Agentic Commerce x402 Hackathon Berlin 2026-06, 메인 트랙 아님, Quantoz 스폰서 보너스 $500 USDC 만
<https://algorand.co/blog/agentic-commerce-x402-hackathon-berlin-recap>

(확인) 태그라인: `"Spending by voice command, with a physical confirmation before any money moves."` 지출 통제를 다뤘으나 메인 트랙 수상은 아니었다.
⚠ 원 수집본의 '지출 통제를 명시적으로 다룬 유일한 수상작'은 **거짓으로 반증됨** — 같은 대회 Agentic Commerce(Existing) **2위 Lockpay($2,000)** 가 `"Milestone escrow that releases payment the moment a creator delivers."` 로 조건부 지급 통제를 다뤘고 이는 메인 트랙 수상이다.
(추정) SingIt 이 보너스에 그친 이유는 페이지에 없다.

> **시사점 2가지**: ① **'지출 통제 서사는 보너스에 그친다'는 명제는 반증됐다** — 402 Guard 에 유리한 반증. ② SingIt 처럼 **'사람의 물리적 확인'을 넣는 방식은 402 Guard 의 무인 자율 결제 전제와 정면 충돌**하므로 벤치마크로 삼지 말 것.

### 3-12. Particle Physics Agent — ADK Hackathon 2025, Honorable Mention
<https://googlecloudmultiagents.devpost.com/updates/35783-and-the-winners-are>

(확인) `"Physics AI agent that converts natural language into validated Feynman diagrams"`. 도메인 전문성은 압도적이나 Honorable Mention. (추정) 사용자층이 극히 좁아 임팩트 축에서 상위권에 못 간 것으로 보인다 — 심사평 미공개.

> **시사점**: 기술 난도가 높아도 사용자층이 좁으면 천장이 있다. **다만 402 Guard 는 반대 위험(너무 넓게 말해 흐릿해짐)이 더 크므로 이 교훈을 과잉 적용하지 말 것.**

### 3-13. ChaosPilot (Philip Mutua) — ADK Hackathon 2025, 회고 글에 수상 서술 없음(무수상 추정)
<https://medium.com/@philip.mutua/my-journey-in-the-google-agent-development-kit-hackathon-building-chaospilot-ce96ce72efc3>

(확인) 저자 본인이 밝힌 어려움: ADK 학습곡선, 백엔드/프론트 동시 개발 부담, BigQuery 로그 싱크 구성, 에이전트 응답 렌더링, `"many skipped crucial deployment steps"`. 성과로 내세운 것이 **'UI 렌더링 성공·인증 구현' 수준의 구현 체크리스트**였다. Gemini 와 Azure OpenAI 를 병용.
(추정) 문제 정의·임팩트 서사 부재가 감점 요인 — 해석이며 심사 근거 아님.

> **시사점**: **'무엇을 만들었나' 나열은 점수가 안 된다.** 402 Guard 의 README·소개서를 기능 체크리스트로 쓰면 같은 함정.
> ⚠ 근거가 참가자 본인 블로그뿐이라 신뢰도가 한 단계 낮다 — **내부 참고용.**

### 3-14. Mercantill — Solana Cypherpunk Hackathon 2025, Stablecoin 트랙 4위
<https://blog.colosseum.com/announcing-the-winners-of-the-solana-cypherpunk-hackathon/>

(확인) `"enterprise banking infrastructure for AI agents"` — 기업이 에이전트에 자금 권한을 줄 때 필요한 계좌·통제 레이어. **402 Guard 와 문제 정의가 사실상 동일한데 4위.** (추정) 이유는 비공개.
⚠ 원 수집본의 '$25,000' 은 **오류** — Colosseum 공식 공지가 트랙 상금을 `"Six prizes ranging from $2,500 to $25,000 USDC in each of the tracks"` 로 명시하므로 $25,000 은 1위 전용이다.

> **시사점**: **'에이전트에게 돈을 맡길 때의 통제 레이어'는 이미 나왔고 4위였다.** 402 Guard 는 B2B 뱅킹 계층이 아니라 **결제 레일 자체(x402 청구서 검증·수취인 allowlist)에서 막는다**는 층위 차이를 명시해야 한다.

---

## 4. 수상 패턴 (11건)

| # | 패턴 | 근거 |
|---|---|---|
| 1 | **안전을 '제약'이 아니라 '자율성을 가능케 하는 조건'으로 프레이밍한다** | (확인) Cronos 1위 AgentFabric `"By solving the critical security trade-off for agentic autonomy"` + 'zero friction'. 반면 순수 방어 Cronos Shield 2위 $5,000, x402 Intent Firewall 은 개발자 도구 $3,000 |
| 2 | **능력 선언 + '넘을 수 없는 선'을 한 문장에 (2단 구조 태그라인)** | (확인) AutoSRE `"…diagnoses Dynatrace incidents in seconds and queues up the fix, but cannot touch production without your one-tap approval."` |
| 3 | **자기 검증(self-grading)을 제품 안에 넣어 '그 가드가 맞다는 보장은?' 반문을 선제 차단** | (확인) Cassandra 의 hand-labeled 'trap library' 자가 채점 + 자기 추론 별도 트레이싱. 같은 대회 CompilanceOS 도 자기 트레이스 활용. **자기검증형 2건이 나란히 수상** |
| 4 | **데모에 시간 약속을 박는다 — '전 루프가 카메라 앞에서 1분 안에 돈다'** | (확인) Cassandra `"The whole loop runs live, on camera, in under a minute."` + 심사위원 조언 기사도 90초 데모를 우승 공통점으로 듦 ([jetbrains](https://blog.jetbrains.com/ai/2026/06/how-to-win-a-hackathon-notes-from-the-judging-table/)) |
| 5 | **태그라인 = 문제 선언 1문장 + 해결자 1문장, 또는 대구 구조** | (확인) TruthKeeper `"Your stack is lying to itself. TruthKeeper is the agent that listens."` / GreenOps(APAC 우승) `"Optimize Every Dollar, Reduce Every Emission"` / Cassandra `"AI agents fail silently, confidently wrong, and nobody notices"` |
| 6 | **프로토콜을 '새로 구현'하지 않고 '기존 생태계와 연결'한 것이 값이 매겨진다** | (확인) MCPay `"open payment infrastructure connecting MCP and x402"` — 1,576개 중 1위 |
| 7 | **기존 레퍼런스 앱·기존 흐름을 바꾸지 않고 위에 얹는 구조** | (확인) GKE 그랑프리 cart-to-kitchen 은 online-boutique 확장. Vigil AI 는 Bank of Anthos 를 코드 무수정으로 감시 |
| 8 | **한 문장으로 끝나는 제품이 용어를 쌓은 제품을 이긴다** | (확인) '장바구니로 레시피 추천'(그랑프리) vs GuardianOS 'Tenth Opinion Protocol'(무수상). 심사위원 조언: *"강한 프로젝트에 혼란스러운 데모는 단순하지만 심사위원이 이해하는 프로젝트에 진다"* |
| 9 | **타겟을 한 나라·한 제도·한 산업으로 좁힌다** | (확인) ADK 중남미 우승 Edu.AI = 브라질 공교육 한정 / DashX(Frontier Top 25) = `"aimed for emerging markets like India"` / CargoBill(Breakout Stablecoins 1위 $25,000) = 공급망 결제 버티컬 |
| 10 | **안전·신뢰가 2025-12~2026-06 x402 판의 지배 주제 — 주제 선택 자체는 옳다** | (확인) Berlin 조직위 총평 `"Many teams focused on trust: a core challenge for any economy where agents pay other agents."` |
| 11 | **검증·안전을 '내부 기능'이 아니라 '건당 과금 상품'으로 만들면 커머스 트랙 최고 상금** | (확인) Berlin 1위 Erster `"Pay-per-evaluation trust check for finance agents."` |

---

## 5. 실패 패턴 (15건) — 402 Guard 해당도 판정 포함

| # | 패턴 | 402 Guard 해당도 |
|---|---|---|
| **[1]** | **'게이트·방화벽·sanity check' 만 내세우면 제품이 아니라 개발자 도구/라이브러리로 분류돼 메인 트랙 밖으로 밀린다** | 🔴 **high_risk** |
| **[2]** | **용어 과적재 — 자체 프로토콜명·약어를 첫 화면에 쌓으면 기술을 다 갖추고도 무수상** | 🔴 **high_risk** |
| **[3]** | **차별점이 이미 상용화·표준화되어 '기능 목록'으로는 검색 한 번에 반박된다** | 🔴 **high_risk** |
| **[4]** | '에이전트 결제를 가능하게 한다'는 범주는 이미 소진됐다 — 같은 제품이 1년 만에 1위에서 명예언급으로 | 🟡 medium_risk |
| **[5]** | 트레이딩·금융 분석 도메인은 풀스택을 다 갖춰도 Honorable Mention 이 천장 | ✅ already_handled |
| **[6]** | **데모 Q&A 붕괴 — AI 생성 코드를 본인이 설명하지 못하면 발표가 좋아도 진다** | 🔴 **high_risk** |
| **[7]** | 현장 라이브 데모가 같은 환경에서도 깨진다 — 영상 없이 라이브만 준비하면 한 방에 끝난다 | 🟡 medium_risk |
| **[8]** | 기능을 여러 개 쌓다가 어느 것도 깔끔히 출하하지 못한다 | 🟡 medium_risk |
| **[9]** | 회고·README 를 '무엇을 만들었나' 체크리스트로 쓰면 임팩트 서사가 사라진다 | 🟡 medium_risk |
| **[10]** | 필수 제출물 누락(아키텍처 다이어그램·공개 저장소·데모 영상)으로 채점 전에 걸러진다 | 🟡 medium_risk |
| **[11]** | 카테고리 진부함 — 서로 구분 안 되는 클론군에 묶이면 혁신 축에서 0점 | 🟡 medium_risk |
| **[12]** | 무인 자율 결제를 표방하면서 사람 승인 단계를 넣으면 전제가 무너진다 | ✅ already_handled |
| **[13]** | ~~'인프라·미들웨어라서 상금이 낮다'~~ ⚠ **검증에서 반증됨** | ⚪ low_risk |
| **[14]** | **대회 정식 주제·권장 스택에서 벗어난 유스케이스 — 이 대회 고유 위험** | 🔴 **high_risk** |
| **[15]** | **AI 활용도(축②)에 정량 근거를 못 대면 'Gemini 를 장식으로 썼다'로 읽힌다** | 🔴 **high_risk** |

### 상세 근거

**[1]** (확인) Cronos x402 PayTech: x402 Intent Firewall(`"a critical sanity check for programmatic payments"`)은 메인 트랙이 아니라 'Best Dev Tooling Layer' $3,000. 같은 대회 1위는 같은 안전 문제를 '자본을 마찰 없이 움직이는 레이어'로 말해 $24,000. 순수 방어 Cronos Shield 는 2위 $5,000 이 천장.
*왜*: 심사위원은 '무엇을 막는가'보다 **'누가 이걸로 무엇을 하는가'** 를 본다. 방어 기능은 그 자체로 사용자 행동을 만들지 않는다.

**[2]** (확인) GuardianOS: 온체인 승인 게이트 + Sepolia 실배포 + 라이브 URL + ADK 멀티에이전트를 갖추고 수상 배지 없음. 화면 용어는 selective de-anonymization, Tenth Opinion Protocol, 3-of-5 합의. 같은 계열 그랑프리는 '장바구니로 레시피 추천'.
*왜*: **심사 시간은 3분이고 심사위원 전원이 x402·AP2 전문가가 아니다. 이해되지 않은 깊이는 0점과 같다.**

**[3]** (확인) [fystack 2026-06-24](https://fystack.io/blog/6-guardrails-to-limit-ai-agent-spending-on-payment-rails) 첫 문장: `"Six spend control types are now standard for fintech teams putting AI agents on payment rails"`(건별 한도·allowlist·승인 워크플로·정책엔진·온체인 집행·가상카드). Crossmint 는 `"per-transaction limits, rolling caps, and recipient allowlists directly in the contract"` 를 컨트랙트에 인코딩. awesome-x402 등재 CardZero 는 **`"Buyer-side x402 support"`** 라는 표현을 이미 사용. xpay.sh Smart Proxy 는 'Allowed tool/destination lists'·'Sub-200ms policy enforcement'. x402-secure(t54 Labs)의 방어 6종 중 counterfeit routes·spec-creep 은 **우리 `GUARD_PAYEE_UNKNOWN`·`GUARD_INTENT_EXCEEDED` 와 정확히 중복.**
*왜*: 우리 핵심 카피('x402 는 파는 쪽을 보호한다, 우리는 사는 쪽을 보호한다')가 **독점적 주장이 아니다.** 기능 나열로 차별화하면 심사위원이 반례를 즉시 든다.
⚠ **§9 비평 지적**: 이 문단의 제품명 6개 중 URL 이 붙은 것은 fystack 1개뿐. **인용 전 개별 확인 필요.**

**[4]** (확인) Latinum: Breakout 2025 AI 1위 $25,000 → Frontier 2026 Top 25 진입 실패. 제출은 1,412 → 2,857건.
*왜*: **신선도는 반감기가 1년 미만이다.**

**[5]** (확인) TradeSage AI(6에이전트+ADK+Vertex/Gemini+pgvector RAG+실시세 3종+Cloud Run) Honorable Mention. Vigil AI 도 HM.
*왜*: 수익률·전략 성능은 검증 불가하고 심사 축에도 없다. **우리 자체 실측도 '어떤 설정도 매수후보유를 평균적으로 못 이긴다'였다.**

**[6]** (확인, 낙선자 자기보고) `"We coded almost everything using Claude...I didn't really understand them myself."` / `"When the judges asked questions I couldn't answer properly."`
*왜*: 402 Guard 는 코드 대부분이 Claude 생성이고, 데모데이 8/21 는 현장 Q&A 가 있다.

**[7]** (확인) LeRobot 서울: 오후 1시 완벽 → 오후 5시 현장 실패.
*왜*: 402 Guard 는 devnet 공용 RPC(429·파우셋 차단 이력), Gemini 무료 티어 쿨다운(백테스트에서 폴백 29건 발생), Cloud Run 콜드스타트라는 **3중 외부 의존.**

**[8]** (확인) 심사위원 인터뷰: `"They try to build five features and end up shipping none of them cleanly"` / `"If it's too long, cut down on your features"` / 문제 정의를 `"Often people skip it"`.
*왜*: 402 Guard 는 이미 조건형·DCA·추세추종·멀티종목·인트라바·1/5크로스·brain 선택·TA 토글까지 옵션이 많다.

**[9]** (확인) ChaosPilot 회고는 성과를 'UI 렌더링 성공·인증 구현' 수준으로 나열했고 수상 서술이 없다.

**[10]** (확인) ADK 규정: Stage One 은 pass/fail 스크리닝. 필수 5종에 아키텍처 다이어그램·공개 코드 저장소 URL 포함.
*왜*: 이 대회도 소개서에 아키텍처를 필수로 요구하고, **저장소는 제출 전 public 전환이 필요하다(현재 private).**

**[11]** (확인, 단 근거 약함) ADK 갤러리 중립 표본 24개 중 6개(25%)가 구분 어려운 클론. ⚠ 원 수집본 '절반 가까이'는 실측으로 반증되어 정정.
*왜*: 402 Guard 가 '또 하나의 AI 트레이딩 봇'으로 분류되면 같은 함정. **다만 Peaks·Clawpump·Agent Arc 수상으로 보아 자동매매 자체가 아니라 '봇 언어'가 문제다.**

**[12]** (확인) SingIt(물리적 확인 요구)은 스폰서 보너스 $500. 반대로 Lockpay(조건부 자동 지급 에스크로)는 메인 트랙 2위 $2,000.
*왜*: 402 Guard 는 이미 '위임장 1회 서명 + 이후 무인'으로 설계돼 이 함정을 피했다. **단 프론트에 '승인 버튼'을 추가하려는 유혹을 경계할 것.**

**[13]** ⚠ **반증됨.** (확인) Berlin 상금표 실측: Infrastructure(New) 1위 AlgoEUPay $2,500 = Agentic Commerce(New) 1위 Juicebag Mail $2,500 로 **동일.** Liminal x402 의 $1,500 은 해당 부문 수상작이 1건뿐인 구조와 더 관련.
*왜*: **포지셔닝 결정의 근거로 인용하면 안 된다.** 진짜 위험은 '인프라'라는 라벨이 아니라 [1] 의 '도구 카테고리 분류'다.

**[14]** (확인) 이 해커톤의 홍보 주제는 "Build the Future of Agentic Commerce" = 에이전트가 클라우드 API 를 발견·인증·결제, 공식 예시는 'Gemini 호출 시 402 감지 → USDC 차감 → API 접근'이며 결제 스택으로 **pay.sh** 가 제시된다.
*왜*: 402 Guard 는 토큰화 주식 자율매매 + 자체 x402 구현이고 pay.sh·Solana Pay 를 쓰지 않는다. 공식 심사 기준상 필수는 아니지만, **심사위원 머릿속의 기본 예시와 다르다는 것은 축③에서 실점 요인.**

**[15]** (확인, 한국 Google 심사 사례) 제미나이 3 서울 해커톤 1위 심사에서 `"구현을 위해 혹시 다른 외부 API들도 섞어 썼나요?"` 를 **두 번** 질문.
*왜*: 402 Guard 자체 실측은 TSLA 481봉에서 **rule −37.56% vs gemini −39.57%** 로 AI 기여 중립~약간 열위였고, rule-gate 는 Gemini 의 사실과 다른 매수 개시를 2건 차단했다. **정직하면 'Gemini 는 성능을 못 올리고 오히려 통제 대상'이라는 서사가 된다** — 이를 강점(감사 가능한 AI 통제)으로 뒤집지 못하면 축② 실점.

---

## 6. 402 Guard 리스크 진단 (12건)

### 🔴 critical

**R1. 차별점이 이미 선점됐고, 심사위원이 검색 한 번으로 반박할 수 있다 — 핵심 카피가 독점적 주장이 아니다**
(확인) awesome-x402 등재 **CardZero** 가 이미 `"Buyer-side x402 support"` 라는 표현을 사용하고 owner-controlled spending rules(per-tx limit, daily cap, whitelist, freeze)를 온체인 집행. **x402-approval-guard** 는 '서명 직전에 게이트를 태우는' 패턴을 오픈소스로 공개. fystack 2026-06-24 업계 정리는 `"Six spend control types are now standard"` 로 시작.
상용: **x402-secure**(t54 Labs — 방어 6종 중 counterfeit routes·spec-creep 이 우리 `GUARD_PAYEE_UNKNOWN`·`GUARD_INTENT_EXCEEDED` 와 중복), **MerchantGuard/AgentGuard**(KYA→OFAC→LLM Firewall→GuardGate→GuardScore→Kill Switch 6단계), **xpay.sh Smart Proxy**, **Crossmint** 컨트랙트 인코딩 allowlist.
해커톤: **Sudont**(Frontier Top 25), **Mercantill**(Stablecoin 4위), **x402 Intent Firewall**(Cronos), **Cronos Shield**(2위).
⚠ §9 비평: 이 판정은 **주장(마케팅 문구)의 존재**에 근거한 것이지 **시장 점유의 증거가 아니다.** 근거 강도와 severity 등급이 불일치한다는 지적 있음.

**R2. '게이트/방화벽'이라는 형태 자체가 메인 트랙이 아니라 개발자 도구로 분류될 위험**
(확인) 개념이 거의 동일한 x402 Intent Firewall 은 'Best Dev Tooling Layer' $3,000, 1위($24,000)는 같은 안전 문제를 '자율성을 가능케 하는 권한 레이어'로 말한 AgentFabric. 순수 방어 프레이밍의 천장은 2위 $5,000.
**402 Guard 현재 첫 화면 KPI 는 '시도·차단·유출 0.00·오탐 0' 으로 전부 방어 지표다.**

**R3. 이 대회의 정식 주제·권장 스택과 유스케이스가 어긋난다 (축③ 직격)**
(확인) 주최측 홍보 주제는 에이전트가 **클라우드 API 를 발견·인증·결제**하는 것. 결제 스택으로 **pay.sh** 가 제시되고 API 카탈로그까지 붙어 있다. 402 Guard 는 토큰화 주식 매매 + 자체 x402 구현이며 pay.sh·Solana Pay 미사용.
→ **주최 두 곳이 직접 만든 레일을 쓰지 않으면서 결제 축을 주장하는 그림**이 된다.

**R4. 축② AI 활용도가 실측상 가장 약하고, 정직하게 말할수록 불리해진다**
(확인, 자체 실측) TSLA `_bear` 481봉: rule −37.56% vs gemini −39.57%(벤치 −20.45%) = **AI 기여 중립~열위.** rule-gate 는 Gemini 가 사실과 다르게 '조건 충족'을 단언한 매수 개시를 2건 차단. 즉 제품 구조상 **Gemini 는 성능 기여자가 아니라 통제 대상.**
⚠ **배포본에 `GEMINI_MODE=developer` 환경변수가 누락되어 라이브 URL 에서 Gemini 가 아예 미작동할 수 있는 잠복 결함이 남아 있다.** 이 상태로 심사자가 라이브 URL 을 열면 **축② 증빙이 0.**

### 🟠 high

**R5. 데모가 밋밋하다 — '아무 일도 일어나지 않는 것'이 성과인 제품의 구조적 약점**
(추정, 단 근거 있는 대비) 유출 0.00·오탐 0 은 화면상 **비사건(non-event)** 이다. 반면 수상작들은 **눈에 보이는 사건**을 팔았다: PlaiPin(ESP32 칩이 스스로 결제하는 하드웨어), World of Geneva(에이전트가 사는 MMORPG), Volt402(EV 에이전트가 실시간 태양광 구매), cart-to-kitchen(장바구니→레시피).
→ **red_team 공격 시연(수취인 위조 → GUARD_PAYEE_UNKNOWN 차단 → 유출 0.00)을 눈에 보이는 사건으로 만들지 않으면 볼 것이 없다.**
⚠ §9 비평: PlaiPin·World of Geneva·Volt402 는 **URL 미제시.** 확인 필요.

**R6. '트레이딩 봇' 오분류 위험은 여전히 살아 있으나, 원래 진단보다 정밀해져야 한다**
(확인) 반례 3건 — Peaks·Clawpump(Frontier Top 25), Agent Arc(Breakout AI 3위). **자동매매 자체는 감점이 아니다.**
그러나 TradeSage AI·Vigil AI 가 보여주듯 **금융 분석·감시 프레이밍의 천장은 명예언급.**
402 Guard 는 화면에 수익률·평가손익·차트·MA 카드가 다수 배치돼 있어 **첫인상이 트레이딩 대시보드로 읽힐 여지가 크다**(현재 프론트 시안 미적용).

**R7. 현장 Q&A 에서 무너질 위험 — 코드 대부분이 Claude 생성이고 데모데이는 8/21 라이브**
예상 질문이 구체적이다: 가드 4항목 검증 순서 / `authorize()` 앞에 `check_demand()` 를 두는 이유 / `GUARD_INTENT_EXCEEDED` 허용치가 왜 2센트인지 / rule-gate 가 Gemini 판단을 강등하는 지점 / 매도 레그 `check_delivery` 대칭성 / exact 비교를 `<` 에서 `!=` 로 바꾼 이유.

**R8. 제출 시점 완성도 리스크 — 프론트 시안 미적용·저장소 private·아키텍처 다이어그램 미확정**
(확인, 자체 상태) 프론트 시안 적용이 아직 최우선 미착수 항목이고, 제출물 3종의 스크린샷·영상이 전부 여기에 종속. 저장소 private. ADK 대회에서 아키텍처 다이어그램이 **명시적 필수 제출물**이었고 이 대회도 소개서에 아키텍처를 요구.

### 🟡 medium

**R9. 상업성·확장성(축①) 반문에 답이 없다 — 엔진이 전역 싱글턴이고 max-instances 1 이라 다중 사용자 불가**
심사위원이 '10만 명이 쓰면?'을 물으면 구조적 답이 없다. 참고로 Colosseum Top 25 공통 기준 문장에 `founder-market fit` 과 `potentially build an enduring crypto startup` 이 들어 있다.

**R10. 브랜드 불일치 — 라이브 URL 은 `synapstock`, 제품명은 `402 Guard`, 저장소명은 `SolanaAgent`**
심사위원이 세 이름을 각각 마주치면 같은 제품으로 인식하지 못할 수 있다. 수상작들은 이름 하나에 은유·기능을 압축했다(Cassandra, STYLOMETRY, TruthKeeper, Unruggable).

**R11. '사후 구제'가 아니라 '사전 차단'이라는 우리 강점이, 이미 사후 구제 수상작들이 있어 자동으로 우월해 보이지 않는다**
(확인) EF x402 수상작 x402r(미배송 환불), Solana x402 Collaboration 트랙 x402Resolve(PDA 에스크로 + Switchboard 오라클 품질점수 기반 0~100% 슬라이딩 환불, 2~48시간 해결 vs 기존 차지백 30~90일), Berlin 2위 Lockpay(마일스톤 에스크로).
→ 이들은 **'돈이 나간 뒤에도 되찾을 수 있다'는 반론**을 갖고 있다. **사전 차단의 우월성은 논증해야 하는 것이지 자명하지 않다.**

**R12. 옵션 과다** — 조건형/DCA/추세추종/멀티종목/인트라바/1·5 크로스/brain 선택/TA 토글이 한 화면에 노출되면 초점이 사라진다.

---

## 7. 액션 (15건)

> ⚠ **§9 비평 반영**: 일정이 3단계(8/3 스크리닝 → 8/10~20 멘토링 → 8/21 본선)이므로, **8/3 전에는 "스크리닝 통과 최소요건"에 자원을 몰고, 프론트 시안(공수 L)은 멘토링 기간으로 미루는 것이 합리적.**

### 8/3 스크리닝 전 (공수 S 위주)

| # | 액션 | 대응 리스크 | 공수 |
|---|---|---|---|
| A0 | **공식 페이지·디스코드에서 `pay.sh 필수 여부`를 오늘 확인.** 본 조사 최대 리스크의 강도를 좌우하며, 가장 값싼 리스크 해소 | R3 / [14] | S |
| A1 | 재배포 환경변수에 **`GEMINI_MODE=developer`** 추가 → 라이브 URL 에서 `brain=gemini` 세션 1회 → `/api/state.ai` 의 `gemini_calls`·`gemini_share_pct` 가 0 이 아님을 스크린샷. **이게 안 되면 심사자가 라이브 URL 을 열었을 때 축② 증빙이 통째로 0** | R4 / [15] | S |
| A2 | **대회 정식 주제에 붙는 API 결제 레그 1개 추가** — 에이전트가 시세·뉴스·Gemini 호출 같은 유료 API 를 x402 로 결제하고, 그 청구서를 402 Guard 가 동일한 4항목으로 검증. `web/broker_service.py`·`payments/x402_http.py` 로 402 왕복이 이미 구현돼 있어 **자원 서버 하나 추가 수준.** 소개서에 "주식 매매든 API 호출이든 에이전트가 돈을 쓰는 모든 경로에 같은 게이트"로 서술하면 유스케이스 불일치가 강점으로 뒤집힌다 | R3 / [14] / **§9 지적 4(축④)** | M |
| A3 | **카피 3건 정정** (§9 지적 2·3 참조) — x402/AP2 관련 과잉 주장 제거 | §9 지적 2·3 | S |
| A4 | 첫 문장(태그라인)을 **AutoSRE 형식 2단 구조**로 재작성. 예: *"에이전트가 사람 없이 24시간 매매하고 USDC 로 정산한다. 단 사용자가 서명한 한도와 수취인 밖으로는 1원도 나갈 수 없다."* 소개서 표지·README 첫 줄·화면 상단·영상 첫 5초에 **동일 문장** | [1][2] / 수상패턴 2 | S |
| A5 | **첫 화면에서 전문용어 제거.** x402·AP2·A2A·mandate·rule-gate 를 상단에서 내리고 **'한 문장 + 숫자 4개(시도·차단·유출 0.00 USDC·오탐 0)'만** 남긴다. 프로토콜 이름은 '어떻게 동작하나' 접힌 섹션과 아키텍처 다이어그램에서만 노출. **GuardianOS 가 정확히 이 지점에서 무수상** | [2] / R1 | S |
| A6 | **제출물 필수 요소 체크리스트 확정**: ①아키텍처 다이어그램(에이전트·가드·mandate·x402·Solana 4계층, 1장) ②저장소 public 전환 시점 예약 ③README 최상단에 devnet explorer 트랜잭션 해시 3~5건 직링크 ④재현 명령 1줄(cp949 크래시 재확인) ⑤데모 영상 링크. **ADK 대회는 이 5종을 pass/fail 스크리닝으로 걸렀다** | [10] / R8 | S |
| A7 | **pay.sh 입장을 1슬라이드로 명시 방어**: 자체 x402 구현을 자랑하지 말고 'A2A 협의 + AP2 위임장 + x402 정산을 잇는 **결합 지점**'으로 서술(MCPay 교훈), pay.sh 는 '동일 레일이며 payTo 검증 대상이 하나 더 늘 뿐 — 가드는 레일 무관'. **여유가 있으면 pay.sh 엔드포인트 1건을 실제로 태워 검증 로그를 남긴다** (§9 지적 1은 이걸 선택이 아닌 1순위로 격상 권고) | R3 / [14] / 수상패턴 6 | M |
| A8 | **축② 서사를 뒤집는다.** 'Gemini 가 수익을 올린다'가 아니라 **'Gemini 에게 판단을 맡기되, 규칙 게이트가 AI 의 잘못된 개시를 코드로 차단한 실측 2건이 있다'**(2022-10-24·2023-08-14, Gemini 가 사실과 다르게 'MA5 대비 3% 이상 낮다'고 단언한 매수 시도). `gemini_share_pct 90.2%`·`gemini_gated 2`·`rule_fallbacks 29` 수치를 그대로 노출. **Cassandra 의 'trap library 자가 채점'과 같은 논리** | R4 / [15] / 수상패턴 3 | S |
| A9 | **소개서에 경쟁 대비 1장.** 사후 구제(x402r·x402Resolve·Lockpay) vs 우리 **사전 차단** / 범용 트랜잭션 시뮬레이션(Sudont) vs 우리 **x402 결제 의미론 검증** / 컴플라이언스 축(MerchantGuard KYA·OFAC) vs 우리 **사용자 예산·의도 집행** / XRPL 우선(x402-secure) vs **Solana 온체인 정산 통합**. **'세계 최초'류 표현은 전부 삭제**하고 층위 차이로 말한다. §9 지적 3에 따라 **"프로토콜 대비" 행 추가 필수** | R1 / [3] | M |
| A10 | 타겟 고객을 **한 줄로 좁혀** 소개서 첫 장에 못박는다. '모든 AI 에이전트 결제'가 아니라 예: *"자산을 자율 운용하도록 에이전트에게 지갑을 맡겨야 하는 개인 투자자와, 그 에이전트를 배포하는 핀테크 팀"*. Edu.AI(브라질 공교육)·DashX(신흥시장)·CargoBill(공급망)처럼 좁힌 쪽이 이겼다 | [2][11] / 수상패턴 9 | S |
| A11 | 소개서 수익모델에 **Erster 형 대안 병기** — 거래 수수료 0.1~0.3% 외에 **'검증 건당 과금(pay-per-check)'**. x402 자체와 정합적이며 커머스 트랙 최고 상금을 받은 실제 모델. 확정하지 않고 '두 가지 경로'로 제시해도 축①에서 상업성 서술이 살아난다 | R9 / 수상패턴 11 | S |
| A12 | **브랜드 정렬.** 화면 타이틀·`<title>`·OG 태그·README 제목·소개서 표지를 전부 **'402 Guard'** 로 통일, `synapstock` URL 은 '접속 주소'로만 표기. 저장소 About 도 402 Guard 한 문장. **URL 변경은 하지 않는다**(비용 대비 효과 낮음) | R10 | S |

### 8/10~20 멘토링 기간 (폴리싱)

| # | 액션 | 대응 리스크 | 공수 |
|---|---|---|---|
| A13 | **데모 대본을 '수익률 시연' → '공격 차단 시연' 중심으로 재작성.** 60~90초 안에: ①에이전트가 자율 매수 결정 ②악성 브로커가 `payTo` 를 위조한 402 청구서 반환 ③`GUARD_PAYEE_UNKNOWN` 으로 차단, 화면에 유출 0.00 ④정상 청구서는 통과해 devnet 트랜잭션 해시 생성 ⑤explorer 링크. **Cassandra 처럼 '전 루프가 카메라 앞에서 1분 안에 돈다'를 대본에 명시** | R5 / [1] / 수상패턴 4 | S |
| A14 | **프론트 시안 적용** — 단, **가드 KPI 4개를 최상단 단독 행**으로 고정, 수익률·차트·MA 카드는 그 아래로. 전략 옵션(DCA·인트라바·1/5 크로스·TA 토글)은 **'고급 설정' 접힘**으로 숨겨 첫 화면 요소 수를 줄인다 | R6 / [8][11] | **L** |
| A15 | **심사 Q&A 대비 카드 10문항** — 사용자가 **코드를 보지 않고 답할 수 있을 때까지** 연습. 필수: 가드 4항목 검증 순서와 `authorize()` 앞 배치 이유 / `GUARD_INTENT_EXCEEDED` 허용치 2센트 근거 / rule-gate 가 Gemini 매수를 강등한 실제 2건 / 매도 레그 `check_delivery` 대칭 / exact 비교를 `!=` 로 바꾼 이유 / 왜 Gemini 여야 했는가 / 왜 카드망이 아니라 블록체인인가 / 다중 사용자 확장 계획 / 토큰화 주식이 devnet 에 없는데 왜 자체 발행인가 / 수익모델. **§9 지적 5에 따라 "무등록 투자일임 아닙니까"·"이용자 자금을 어떻게 분리 보관합니까" 반드시 추가** | [6] / R7 | S |
| A16 | **라이브 데모 폴백 2중**: ①녹화 영상(devnet 풀사이클 + 공격 차단) ②로컬넷 재시연 경로를 미리 띄워두고 전환 키 하나로 넘어가게. devnet 공용 RPC 429·파우셋 차단·Gemini 무료 티어 쿨다운·Cloud Run 콜드스타트가 **실제 발생 이력이 있는 실패 지점.** 8/21 전에 1회 리허설 | [7] / R5 | S |

---

## 8. 끝내 확인하지 못한 것 (7건)

1. **이 해커톤의 공식 심사 배점·트랙 구성·제출물 세부 규격.** 언론 보도로 총상금 $135,000·트랙 최대 $20,000·주제·pay.sh 스택까지는 확인했으나, 심사 4축의 **가중치**, 트랙 이름, 데모 영상 길이 상한, 마감 시각의 공식 표기는 공개 자료에 없다. **사용자가 디스코드 공지·신청 페이지에서 직접 재확인해야 한다.** 특히 **pay.sh 사용이 가점인지 단순 예시인지가 본 보고서 최대 리스크(R3·[14])의 강도를 좌우한다.**

2. **조사한 12개 해커톤 전부 프로젝트별 심사평 비공개(확인).** 심사 근거로 확정 인용 가능한 것은 §0-2 의 4건뿐. 나머지는 결과에서 역산한 추정. **"이래서 이겼다/졌다"를 소개서에 사실로 인용하지 말 것.**

3. **명시적 '탈락작' 데이터는 구조적으로 존재하지 않는다.** Devpost 갤러리에서 '수상 배지 없음'으로 역추적하는 것이 유일한 방법. `losers` 목록의 GuardianOS·ChaosPilot·일반형 클론은 **"무수상 사실은 확인, 사유는 추정"**. 유일하게 낙선 사유가 1인칭으로 기록된 Substack 회고 1건도 심사위원 발언이 아니고 대회명·주최·개최일이 원문에 없다.

4. **한국 해커톤 심사 문화 표본이 2건뿐** — 제미나이 3 서울(blog.google, 심사 질문 원문 확인)과 LeRobot 서울 로컬 후기. 둘 다 이 대회와 주최·성격이 달라 일반화 강도가 약하다. **이 대회의 심사위원 구성(Google Korea / Solana Foundation·Superteam Korea / 외부 VC)은 확인 못 했고, 그에 따라 축②(Gemini)와 축③(온체인)의 실질 가중치가 크게 달라진다.**

5. **직전 회차·유사 국내 대회의 실제 낙선작 사례 0건.** 한국어 검색으로는 대회 홍보 기사만 나오고 참가자 회고·낙선 후기는 아직 없다(대회 진행 중이므로 당연). **8/3 제출 이후 참가자 후기가 나오면 데모데이 전에 재조사 가치 있음.**

6. **경쟁 제품의 실사용 여부·가격·점유율은 확인 범위 밖.** x402-secure·MerchantGuard·xpay.sh Smart Proxy 는 제품 페이지의 **마케팅 문구만** 확인했고 실제 고객·거래량은 알 수 없다. 즉 **"이미 상용화됐다"는 리스크 판정은 주장의 존재에 근거한 것이지 시장 점유의 증거가 아니다.** 심사위원이 이들을 알고 있을 확률도 추정 불가.

7. **사실 불일치 3건 미해소** (원출처 403): CroIgnite 철자 불일치(MEXC 'Crolgnite' vs 스니펫 'CroIgnite') / Solana x402 해커톤 상금 상충(언론 $20,000 vs 공식 $10,000) / Coinbase Agents in Action 기간 자사 페이지 간 불일치. **인용 시 주의.**

---

## 9. 완전성 비평 전문 (별도 에이전트, 보충 조사 포함)

### (1) 빠진 각도

| # | 빠진 각도 | 왜 치명적인가 |
|---|---|---|
| A | **공동주최자가 Superteam Korea 라는 사실 자체가 누락** | solanacompass 원문 `"co-organized with Solana Super Team Korea"`. 공동주최가 확인되는 순간 **심사 문화의 1순위 참조는 Colosseum/Superteam 계열**(founder-market fit·enduring startup·ship-ability)로 좁혀진다 |
| B | **동일 주최진의 선행 회차(2025-04 Seoulana Hacker House, Seoul, Superteam Korea × Google Cloud) 미조사** | 조사한 12개 중 **주최·도시·언어가 전부 같은 유일한 선행 사례**인데 목록에 없다. Devfolio 페이지 실재(seoulana-hacker-house.devfolio.co). 수상작 명단 확보 실패했으나 **"가장 가까운 표본을 안 열어봤다"는 것 자체가 구멍** |
| C | **기관 파트너가 결제 PG사(케이에스넷)라는 점의 함의 미분석** | 심사·멘토링에 **결제 산업 실무자 관점**이 들어온다. 402 Guard 는 이 대회에서 유일하게 "증권 매매 + 자동 정산"을 다루는 축이라 **정산·수수료·무등록 투자일임·증권성 질문을 받을 확률이 다른 대회보다 훨씬 높다.** **guard_risks 12개 중 규제 리스크가 0건이다** |
| D | **pay.sh 의 실제 기능 명세 미조사** | "쓰지 않는다"는 리스크로만 다뤘고 **pay.sh 가 무엇을 이미 제공하는지**를 확인하지 않았다 → (3)-① |
| E | **AP2 공식 스펙 원문 대조 미실시** | AP2 를 쓴다고 주장하는데 **공식 스펙이 무엇을 요구하는지 한 번도 열어보지 않았다** → (3)-② **본 비평에서 가장 아픈 발견** |
| F | **x402 스펙 v2 원문 대조 미실시** | 핵심 카피가 **스펙 원문으로 반박 가능한지** 확인하지 않았다 → (3)-③ |
| G | **일정 구조 미확인** | 보고서 전체가 "마감 8/3, 데모데이 8/21" 2점 구조를 전제. 실제로는 **8/10~20 집중 멘토링 + 8/21 본선**이 있다. 즉 **8/3 은 본선 진출 스크리닝** |
| H | 한국어 커뮤니티 1차 자료 | 이 대회 관련 참가자 회고 **아직 0건**(진행 중이므로 당연). GSC 계열 행사 영상은 미탐색 |
| I | 참가 규모·본선 진출 팀 수·팀 vs 개인 규정 | Colosseum 2,857건·ADK 476건과 달리 **이 대회는 모수를 모른다.** 50팀이면 "완주만 해도 상위권", 500팀이면 스크리닝이 진짜 관문. **리스크 강도 전체가 여기에 종속** |

### (2) URL 없이 단언된 주장 (지목)

**심각 — 프로젝트명이 나오는데 근거 URL 이 없다:**

1. **R1 "차별점 선점"**: CardZero, x402-approval-guard, x402-secure(t54 Labs), MerchantGuard/AgentGuard, xpay.sh Smart Proxy, Crossmint **6개 제품이 구체적 기능 문구까지 인용되는데 붙은 URL 은 fystack 블로그 1개뿐.** 특히 CardZero 의 `"Buyer-side x402 support"` 인용은 **본 보고서에서 가장 무거운 한 문장인데 출처가 없다.**
2. **R5(데모 밋밋함)**: PlaiPin, World of Geneva, Volt402 — 어느 대회 수상작인지, URL 이 무엇인지 없다.
3. **수상패턴 5**: TruthKeeper, GreenOps(APAC 우승) — 갤러리 URL 하나로 뭉뚱그렸다. **갤러리 URL 은 개별 프로젝트의 근거가 아니다.**
4. **수상패턴 3**: CompilanceOS — 철자부터 오타로 보이며(Compliance?) URL 없음.
5. **수상패턴 9**: Edu.AI(브라질), DashX, CargoBill — cloud.google.com 블로그 1개로 3건 커버. DashX·CargoBill 은 Colosseum 건인데 해당 URL 없음.
6. **R11**: x402Resolve 의 "PDA 에스크로 + Switchboard 오라클 + 0~100% 슬라이딩 환불 + 2~48시간 vs 차지백 30~90일" — 매우 구체적 수치인데 URL 없음.

**논리적으로 근거가 약한 단언:**

7. §8-6 이 스스로 인정하듯 "이미 상용화됐다"는 판정은 **마케팅 문구의 존재**일 뿐인데 R1 의 severity 는 **critical**. **근거 강도와 심각도 등급이 불일치.**
8. "pay.sh 가 사실상 기본 경로로 제시되고 있다"는 서술은 `"the payment stack for the exercise"` 라는 **2차 매체 표현**에 의존. **주최측 공식 문서 원문이 아니다.**
9. CLAUDE.md 에서 넘어온 **"직전 x402 해커톤 수상작 16건 중 트레이딩 봇 0건"** — 본 보고서가 Peaks·Clawpump·Agent Arc 로 사실상 반증했는데, **그 전제가 어디서 왔는지 출처가 없다. 재포지셔닝 전체의 출발점인데 근거가 미확인 상태.**

### (3) 보충 조사로 새로 찾은 사실

#### ① pay.sh 결제 흐름 자체가 "지출 상한"을 포함한다 (확인)

pay.sh 는 **`"built on x402 and MPP"`** 이며, Google Cloud 앞단의 API 프록시로서 **`"applying appropriate rate limits, quotas, and access controls so enterprise security and compliance are never compromised"`** 라고 [공식 페이지](https://solana.com/news/solana-foundation-launches-pay-sh-in-collaboration-with-google-cloud)에 적혀 있다. 언론 요약에는 에이전트가 402 를 받으면 **`"deducts USDC from a pre-funded wallet up to a programmatic spending ceiling"`** 로 서술된다.

> **함의(아픔)**: 402 Guard 의 "건별 지출 상한"은 **주최 두 곳이 직접 만든 레일의 기본 동작으로 이미 홍보되고 있다.** 심사위원이 pay.sh 소개 문구를 아는 상태에서 "그건 pay.sh 가 이미 하는 것 아닌가"라고 물으면, fystack·CardZero 반박보다 **훨씬 가까운 거리에서** 차별점이 무너진다.
> **단, pay.sh 공식 페이지에서 확인되는 것은 rate limit/quota/access control 까지이고 "수취인 allowlist·청구서 대 합의견적 대조"는 확인되지 않았다 — 여기가 유일하게 살아 있는 틈이다.**

#### ② AP2 공식 스펙에 "허용 수취인(allowed payees)" 제약이 이미 정의돼 있다 (확인)

[ap2-protocol.org/ap2/payment_mandate](https://ap2-protocol.org/ap2/payment_mandate/) 원문 구조: Payment Mandate 는 **SD-JWT(Selective Disclosure JWT) 형식의 Verifiable Credential**(`vct: "mandate.payment.1"`)이며, optional constraints 로 **allowed payees, allowed payment instruments, amount ranges, budget caps, agent recurrence limits, execution date windows** 를 규정한다. 또 `"The payment_amount property of the Payment Mandate MUST be within the range defined by min and max."`

> **함의(가장 아픔)**: 프로젝트의 재포지셔닝 근거 1번은 *"AP2 `authorize()` 가 `pay_to` 를 받고도 미검사(`payments/ap2_mandate.py:112-129`)"* 라는 자체 결함 발견이었다. **그런데 그건 AP2 프로토콜의 공백이 아니라 프로젝트 자체 구현의 미완성이다.** 공식 AP2 는 allowed payees 를 스펙에 갖고 있다.
> **Google 이 만든 프로토콜을, Google 이 주최한 대회에서, 스펙에 이미 있는 제약을 "우리가 발견해 메운 구멍"으로 발표하면 축②·축③ 양쪽에서 역풍이다.**
> 게다가 프로젝트의 mandate 는 SD-JWT/VC 가 아니라 자체 서명 구조다 → **"AP2 를 썼다"는 주장 자체가 AP2 준수(conformance)가 아니라 AP2 영감(inspired-by)으로 정정돼야 한다.**

#### ③ x402 스펙에는 구매자 측 장치가 이미 존재한다 (확인)

[coinbase/x402 v2 스펙](https://github.com/coinbase/x402/blob/main/specs/x402-specification-v2.md)의 `exact` 스킴 필수 필드에 **`maxAmountRequired`** 가 있고, [docs.x402.org FAQ](https://docs.x402.org/faq) 는 `"The client authorizes a maximum amount but is only charged for actual usage"`, `"Buyers sign locally in their runtime... Sellers never hold the buyer's key"`, `"A facilitator that tampers with the transaction would fail signature checks"` 로 서술한다.

> **함의**: *"x402 스펙은 파는 쪽을 보호한다. 우리는 사는 쪽을 보호한다"* 는 핵심 카피가 **한 문장으로 반박 가능**하다("maxAmountRequired 랑 로컬 서명이 이미 구매자 보호 아닌가요?").
> **정확한 방어선**: *"x402 는 구매자가 **금액 상한**을 걸 수단은 주지만, **수취인이 합의한 상대인지·청구서가 합의 견적과 같은지**는 검증 지점을 정의하지 않는다."*
> **지금 카피는 과잉 주장이라 Q&A 에서 신뢰를 통째로 잃는다.**

#### ④ 이 대회에서 pay.sh 는 "예시"보다 강하게 서술된다 + 공동주최가 Superteam Korea (확인 — 2차 매체)

[Solana Compass](https://solanacompass.com/news/solana-foundation-and-google-cloud-bring-ai-agentic-commerce-hackathon-to-korea):
- **`"Developers must build AI agents using Pay.sh (the payment proxy), x402 protocol, and USDC on Solana."`**
- 핵심 과제: `"building agents that can discover, authenticate, and pay for cloud API services via the x402 protocol, without any human involvement in the payment step."`
- `"co-organized with Solana Super Team Korea"`
- 총상금 $135,000, 트랙당 최대 $20,000, 공고일 2026-07-17
- 동일 주최진이 **2025년 4월 서울에서 선행 해커톤 개최**

> 종합 결과는 pay.sh 를 "홍보 문구상 기본 경로"로 표현했지만, 2차 매체 서술은 **`"must"`** 다. (기자의 해석일 수 있으므로 **공식 페이지·디스코드 원문 확인 필수.**)
> 다만 리스크 강도는 "축③ 실점"에서 **"스크리닝 탈락 가능성"** 으로 한 단계 올려 계상해야 한다.

#### ⑤ 일정 구조가 3단계다 — 8/3 은 최종본 마감이 아니다 (확인)

[케이에스넷 기관 파트너 기사](http://www.sportpeopletimes.com/news/articleView.html?idxno=31762): **집중 멘토링 8월 10~20일 → 데모데이(본선) 8월 21일**, 장소 구글 스타트업 캠퍼스, 기관 파트너 **케이에스넷**(국내 PG사). 대회 성격은 **"사용자가 일일이 승인할 필요 없이 AI 에이전트가 정해진 한도 내의 결제를 알아서 처리하는"** 에이전틱 커머스로 서술되며, 기술 스택은 **Gemini + Solana Pay** 로 언급(이 기사에는 pay.sh 언급 없음). 심사는 **"기술력과 사업성을 모두 고려"**.

> **함의 2개**:
> **(가)** 8/3 은 **본선 진출 스크리닝**이고 그 뒤에 11일짜리 멘토링이 붙는다. → actions 우선순위를 **스크리닝 통과 최소요건 우선 → 폴리싱은 멘토링 기간**으로 재배치해야 한다. 종합 결과의 "프론트 시안 최우선(공수 L)"은 **"7일 뒤가 끝"이라는 잘못된 전제에서 나온 배치**다.
> **(나)** **"정해진 한도 내의 결제"가 대회 자체의 주제 서술**이다 — 402 Guard 의 주제 적합성은 우려한 것보다 **좋다.** 문제는 적합성이 아니라 **그게 대회의 기본 전제라서 차별점이 되지 못한다**는 것이다.

### (4) 종합 결과에 추가해야 할 냉정한 지적 5개

**지적 1 — "pay.sh 미사용"은 감점이 아니라 스크리닝 컷오프 리스크로 재분류하라**
종합 결과는 이를 R3(critical)·[14] 로 넣고, 대응을 **"1슬라이드로 명시 방어" + 여유 시 엔드포인트 1건**(공수 M)에 머물렀다. 새 사실 기준으로는 ①2차 매체가 "must" 로 서술 ②8/3 이 본선 진출 스크리닝 ③주최 두 곳이 직접 만든 레일 — **세 조건이 겹친다. 슬라이드 방어로는 부족하다.**
최소한 **pay.sh 엔드포인트 1건을 실제로 태우고 그 청구서를 402 Guard 로 검증한 로그**가 8/3 제출본에 있어야 한다. **공수 M 의 선택지가 아니라 actions 1순위다.**
동시에 공식 페이지·디스코드에서 **"pay.sh 필수 여부"를 오늘 확인**하는 것이 본 조사 전체에서 **가장 값싼 리스크 해소**다.

**지적 2 — 재포지셔닝의 근거 절반이 프로토콜의 공백이 아니라 자기 코드의 미완성이다**
`docs/differentiation.md` 의 핵심 논거 *"AP2 `authorize()` 가 `pay_to` 를 미검사한다"* 는, **공식 AP2 스펙에 `allowed payees` 제약이 이미 정의돼 있으므로** 프로토콜 결함이 아니라 자체 구현 결함이다. 마찬가지로 *"exact 인데 금액 비교가 `<`"* 도 **x402 스펙 위반을 스스로 고친 것**이지 스펙의 공백이 아니다.
**Google 주최 대회에서 Google 프로토콜 스펙을 잘못 구현했다가 고친 것을 혁신으로 발표하는 그림**이 될 수 있다.
→ 서사 정정: *"AP2 는 제약을 정의하지만 **집행 지점(enforcement point)을 규정하지 않는다.** 402 Guard 는 그 집행을 **x402 청구서 수신 시점, 서명 직전**이라는 구체적 지점에 못 박은 참조 구현이다."*
→ mandate 를 SD-JWT/VC 로 바꿀 수 없다면 **"AP2 준수"가 아니라 "AP2 개념 기반"으로 표기를 낮춰라** — **정직성 문제이자, 스펙을 아는 심사위원 앞에서의 생존 문제.**

**지적 3 — 핵심 카피가 x402 스펙 한 줄에 반박당한다**
*"x402 는 파는 쪽을 보호한다"* 는 `maxAmountRequired`·구매자 로컬 서명·facilitator 변조 시 서명 실패라는 스펙 사실과 충돌한다. **이 문장은 소개서·README·영상 첫 5초에 들어갈 예정**이므로 반박당하면 전체 신뢰가 무너진다.
→ 교체: *"x402 는 구매자에게 **금액 상한**을 주지만, **누구에게 보내는지·왜 그 금액인지**를 검증하는 지점은 정의하지 않는다."*
→ A9(경쟁 대비 1장)에 **"프로토콜 대비" 행을 추가해야 한다** — 경쟁 제품만 비교하고 **표준 자체와의 경계선을 안 그렸다.**

**지적 4 — 축④(실제 구동)에 대한 반문이 리스크 목록에 없다: "진짜 상품을 산 적이 없다"**
guard_risks 12개 중 축④ 관련 항목이 **0건**이다. 그런데 이 대회의 결제 대상은 **클라우드 API(Gemini·BigQuery·Cloud Run·Helius…)** 라는 **실재하는 상품**이고, 402 Guard 가 사고파는 것은 **devnet 에 존재하지 않아 자체 발행한 토큰화 주식**이다. 즉 매수·매도 양쪽이 자기가 만든 자산이고, 브로커도 자기 프로세스다. **온체인 트랜잭션은 진짜지만 거래 상대와 상품은 자기 자신.**
"목업은 심사 제외"라는 대회 원칙 앞에서 ***"실제로 무엇을 산 건가요?"*** 라는 질문은 나올 수밖에 없고, **현재 답이 없다.**
→ **지적 1의 API 결제 레그는 축③ 보완이 아니라 축④ 방어책이기도 하다** — 실제 Gemini 호출 1건을 x402 로 결제하는 순간 **"진짜 상품을 진짜 돈으로 샀다"가 성립한다.** 우선순위를 다시 올려야 하는 이유가 하나 더 늘었다.

**지적 5 — 일정 오독으로 actions 우선순위가 틀렸고, 규제·심사진 구성 리스크가 통째로 비어 있다**
**(가) 일정**: 8/3 최종 마감이 아니라 **8/3 스크리닝 → 8/10~20 멘토링 → 8/21 본선.** 8/3 까지는 **"스크리닝을 통과시키는 최소 완성"**(pay.sh 레그·아키텍처 다이어그램·public 저장소·영상·주제 정합성)에 자원을 몰고, **프론트 시안 적용(공수 L)은 멘토링 기간으로 미루는 것이 합리적.**
**(나) 심사진 구성**: 공동주최 **Superteam Korea**, 기관 파트너 **PG사 케이에스넷**, 심사 서술 **"기술력과 사업성"**. 이 조합은 (i) Colosseum 식 창업 관점(founder-market fit·지속 가능성) (ii) 결제 실무 관점(정산·수수료·규제)을 동시에 불러온다.
**그런데 guard_risks 에 규제 항목이 0건이다.** 토큰화 주식은 증권이고(SEC 2026-01-28), 한국 제도 시행은 2027-02-04 이며, 자동매매는 무등록 투자일임 리스크가 있다 — **이건 CLAUDE.md 가 이미 아는 사실인데 본 조사의 리스크 목록에는 반영되지 않았다.**
**PG사가 파트너인 대회에서 이 질문이 안 나올 확률은 낮다.** Q&A 카드에 **"이거 무등록 투자일임 아닙니까"** 와 **"이용자 자금을 어떻게 분리 보관합니까"** 를 반드시 추가하라. 답의 축은 이미 있다 — **self-custody + 위임장 1회 서명 + 즉시 정지 + 우리가 자금을 보관하지 않음.**

### 비평 단계 출처

- [Solana Foundation Launches Pay.sh in Collaboration with Google Cloud (공식)](https://solana.com/news/solana-foundation-launches-pay-sh-in-collaboration-with-google-cloud)
- [Solana Foundation & Google Cloud Korea AI Hackathon (Solana Compass)](https://solanacompass.com/news/solana-foundation-and-google-cloud-bring-ai-agentic-commerce-hackathon-to-korea)
- [AP2 Payment Mandate 공식 스펙](https://ap2-protocol.org/ap2/payment_mandate/)
- [Announcing Agent Payments Protocol (AP2) — Google Cloud Blog](https://cloud.google.com/blog/products/ai-machine-learning/announcing-agents-to-payments-ap2-protocol)
- [x402 Specification v2 (coinbase/x402)](https://github.com/coinbase/x402/blob/main/specs/x402-specification-v2.md)
- [x402 공식 FAQ](https://docs.x402.org/faq)
- [케이에스넷, AI 에이전틱 해커톤 기관 파트너 참여](http://www.sportpeopletimes.com/news/articleView.html?idxno=31762)
- [Solana Foundation and Google Cloud co-host AI hackathon in Korea (Crypto Briefing)](https://cryptobriefing.com/solana-google-cloud-ai-hackathon-korea/)
- [Seoulana Hackathon 2025 (Devfolio) — 선행 회차](https://seoulana-hacker-house.devfolio.co/)
- [Superteam Korea](https://www.superteamkr.com/)

---

## 10. 내일 재분석용 메모 — 먼저 결정해야 할 것

이 문서는 **조사 결과이지 결정이 아니다.** 사용자가 판단해야 할 것을 순서대로 정리한다.

1. **[가장 값싼 것부터] pay.sh 필수 여부를 공식 채널에서 확인.** 이 답 하나에 R3·[14]·§9 지적 1·A2·A7 의 강도가 전부 달라진다. 확인 전까지는 나머지 계획이 흔들린다.
2. **일정 3단계가 맞는지 확인.** 맞다면 **프론트 시안(공수 L)을 8/3 전에서 멘토링 기간으로 옮기는 것**이 합리적인데, 이건 기존 CLAUDE.md 의 "①프론트 시안 적용(최우선)" 방침을 뒤집는 결정이라 사용자 판단이 필요하다.
3. **카피 3건 정정 여부** (§9 지적 2·3). `docs/differentiation.md` 의 논거 일부를 다시 쓰는 일이다. **정직성 문제이므로 미루면 위험이 커진다.**
4. **API 결제 레그 추가 여부** (A2). 축③·축④·"뭘 샀나" 반문을 한 번에 해소하지만 새 기능이다. 공수 M 추정.
5. **규제 Q&A 준비** (§9 지적 5-나). PG사 파트너 대회라 확률이 높은데 현재 리스크 목록에 없었다.

### 반증되어 폐기해야 할 기존 전제 2건

- ❌ **"직전 x402 해커톤 수상작 16건 중 트레이딩 봇 0건"** (CLAUDE.md 재포지셔닝의 출발점) — **출처 미확인 + 반례 3건**(Peaks, Clawpump, Agent Arc). 정확한 명제는 *"단일 전략 봇 언어로 말하면 떨어진다"*.
- ❌ **"인프라·미들웨어라서 상금이 낮다"** — Berlin 상금표 실측으로 반증([13]).

### 유지되는 기존 판단

- ✅ 402 Guard 재포지셔닝 방향(수익률 대신 통제 KPI) — **유지.** 단 **매매 기능을 숨길 필요는 없다**(Peaks·Clawpump·Agent Arc).
- ✅ "공식 심사 기준상 pay.sh 는 병렬 예시라 필수 아님" — **판단 자체는 유효.** 단 홍보 문구가 "must" 라 리스크 강도는 올려야 한다.
- ✅ 시간청산·멀티종목·규칙 게이트 등 기존 구현 — **본 조사와 충돌 없음.**
