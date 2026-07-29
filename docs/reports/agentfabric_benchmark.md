# AgentFabric 벤치마크 — 402 Guard 차별점·갭·개선안 (2026-07-27)

> **대상**: AgentFabric — Cronos x402 PayTech Hackathon 2026-02 **1위 $24,000**(191팀).
> 본 조사가 다룬 대회 중 **수상 근거가 원문으로 명시된 유일한 사례**다
> (`"By solving the critical security trade-off for agentic autonomy"`).
> **방법**: 워크플로우 5에이전트(조사 2·대조 1·적대검증 2), 오류 0.
> **★ 아래 §1 의 결정적 사실 2건은 내가 raw 소스를 직접 열어 재확인했다**(에이전트 보고 그대로 쓰지 않음).
> 저장소: <https://github.com/nschwermann/agent_fabric> · 사이트: agentfabric.tools

---

## 1. 결정적 발견 — 1위의 '한도'는 강제되지 않는다 ★직접 확인

`hardhat/contracts/AgentDelegator.sol` 의 `Session` 구조체 **전체 필드**:

```solidity
address sessionKey; address[] allowedTargets; bytes4[] allowedSelectors;
uint48 validAfter; uint48 validUntil; bool active;
```

**금액 필드 없음. 수취인 필드 없음.** 온체인에서 강제되는 것은 **호출 대상 주소 · 함수 셀렉터 · 시간창** 3가지뿐이다.
커스텀 에러 9종(`TargetNotAllowed`·`SelectorNotAllowed`·`ContractNotApproved`·`SessionExpired`…)에도 payee 계열이 없다.

그리고 팀이 자기 소스에 직접 적어 둔 주석(`apps/web/lib/sessionKeys/types.ts`):

> `Advisory limits (NOT enforced on-chain, just for UI display)`
> `Budgets CANNOT be enforced on-chain (view function)`

README·사이트는 `Max spend: 5 CRO`·`maximum value`·`how much value they are allowed to use` 를 내세우지만,
**x402 결제 스코프(`x402:payments`)는 `budgetEnforceable: false`** 이고 금액은 UI 표시용 참고치다.

> **함의**: 우리의 AP2 mandate 는 `authorize()` 에서 건별 한도·잔여 예산을 **실제로 검사하고 차감한다**
> (`payments/ap2_mandate.py:126-133`). 1위 수상작이 서사로만 가진 것을 우리는 코드로 갖고 있다.
> ⚠ **단, 톤 주의.** "저쪽은 거짓말한다"로 말하면 안 된다. 그들의 `budgetEnforceable` 플래그는
> '강제 가능한가'를 정직하게 표시한 것이고, execute 스코프는 `true` 다. 정확한 표현은
> **"온체인 세션키는 '무엇을 건드릴 수 있는가'까지 강제한다. '얼마를·누구에게'는 아직 그 레이어에 없다."**

---

## 2. 겹치는 것 — 차별점으로 내세우면 즉사한다

| 겹침 | 그쪽 근거 | 우리 |
|---|---|---|
| 에이전트가 사용자 개인키에 접근하지 않는다 | README 최상단 `Agents never access a user's primary private key.` | `engine.py:489,495` 위임자/실행자 키 분리 |
| 사용자 1회 서명 + 이후 자율 실행 | `useGrantSession.ts` grantSession 1회 | `engine.py:494` mandate 1회 서명 |
| 스코프 기반 위임(허용 목록) | allowedTargets/allowedSelectors | `ap2_mandate.py:33-36` allowed_asset/symbols |
| x402 결제 레일 + **테스트넷** 실배포 | Cronos 테스트넷(338) 1건, 메인넷은 `not yet deployed` | Solana devnet 1건 — **동급** |
| 차단 코드 열거식 서사 | 커스텀 에러 9종 | `GUARD_*` 7종 |
| 랜딩+대시보드+영상+라이브 URL 제출물 구성 | 동일 | 동일 |

**"테스트넷이라 약하다"는 우리만의 약점이 아니다** — 1위도 테스트넷이 유일한 배포다. 방어적으로 굴 필요 없다.

⚠ 수취인 allowlist 는 AgentFabric 엔 없지만 **Circle Agent Wallets·Kyvern(Solana devnet)** 등에는 있다.
"세계 최초"류 표현 금지. 부재 증명에는 반드시 **"우리가 확인한 범위에서"** 를 붙인다.

---

## 3. 우리 고유 — 코드 근거가 있고 심사장에서 버티는 것 4가지

| # | 차별점 | 코드 근거 | 왜 버티나 |
|---|---|---|---|
| **A** | **의도 지출 상한**(`GUARD_INTENT_EXCEEDED`) — 판매자가 만든 견적과 **독립적으로**, 구매 에이전트 자신이 정한 금액을 상한으로 걸고 청구액과 대조 | `guard.py:159-165` + `trading_agent.py:485-489`(AP2 앞) + `engine.py:1116` | 악성 브로커가 건별 한도 45 안쪽 **44.94** 를 자기정합으로 청구하는 공격은 mandate·x402·세션키 **어느 것도** 못 막고 이 검사만 막는다. *"한도를 지켰는데 전액을 잃는다"* 한 문장으로 설명된다 |
| **B** | **매수·매도 양 레그 대칭**(`check_stock_transfer`) — 자산을 내보내는 방향에서도 서명 직전 자산·수취인·수량 대조 | `guard.py:187-238` + `engine.py:1239-1250` | 조사 범위 전체(AgentFabric·Circle·Kyvern·MetaMask·Coinbase)에서 **'들어오는 방향의 청구서도 검증한다'는 대칭 설계가 하나도 없었다.** 전부 '나가는 돈'만 본다 |
| **C** | **AI 판단에 대한 코드 강제 게이트**(`_rule_gate`) — 규칙 신호 없는 개시를 프롬프트가 아니라 코드로 차단 | `trading_agent.py:427-460` + `engine.py` `_ai_stats` 의 `gemini_gated` | AgentFabric 은 '무엇을 할 수 있는가'만 제약하고 **'판단이 옳은가'는 안 본다** — 세션키 범위 안이면 어떤 매매든 통과. 우리는 실측이 있다: TSLA 481봉에서 Gemini 가 *사실과 다르게* "조건 충족"을 단언한 매수 2건을 코드가 차단 |
| **D** | **못 막은 것도 숫자로 표시한다** — 유출 KPI 가 실측 가산이고, 레드팀이 방어를 3계층으로 분리(구매자 서명 전 차단 / 판매자측 방어 / 사후 탐지·회수 불가) | `engine.py:_record_leak`(한계 6건 자체 기재) + `red_team.py` 3계층 | 경쟁군은 전부 단일 숫자를 판다(Kyvern `$0 drained`, AgentFabric `zero friction`). **먼저 말한 약점은 공격당하지 않는다** |

**A·B 가 핵심이다.** 한 문장으로: *"모두가 '얼마까지'를 말할 때, 우리는 '누구에게·무엇에 대해'를 서명 직전에 묻는다."*

---

## 4. 그쪽에 있고 우리에 없는 것 (정직한 갭)

1. **온체인 스마트컨트랙트 집행** — `AgentDelegator.sol` 509줄이 체인 위에서 검증. 우리 Guard 는 파이썬 프로세스 안 평범한 객체이고 검사가 조건부다(`trading_agent.py:487,530`).
   (2026-07-29 갱신 — 금액 축 한정으로 SPL Token 위임 레일을 실증했다: `docs/design/onchain_budget_design.md` · `scripts/demo_delegation.py`. **갭은 닫히지 않았다** — 배선 전이고, 위임은 수취인·대상을 강제하지 않는다.)
2. **MCP 서버** — ChatGPT·Claude 가 바로 발견·호출. 심사위원이 '내 Claude 에 붙여봤다'가 되면 축④의 의미가 달라진다.
3. **수익 레이어 결합** — 방어를 단독으로 팔지 않고 x402 API 프록시 + 워크플로 마켓플레이스와 묶었다.
4. **위임 만료의 온체인 강제** — `validAfter/validUntil`. 우리 `OpenPaymentMandate` 엔 **만료 필드 자체가 없다**.
5. **진짜 제3자 상대** — 유료 x402 API + WolfSwap DEX. 우리 브로커는 우리 프로세스이고 devnet USDC 조차 자체발행이었다.
6. **자연어 명령 한 줄 데모** — `"Find the top trending token on Cronos today and buy 5 CRO worth of it."` → 발견 → 추론 → 실행 → 정산. 우리 데모는 설정 고르고 버튼 누르는 형태라 **'에이전트가 스스로 한다'는 인상이 약하다**.
7. **팀 신뢰 자산** — 1인이지만 Cronos 최대 NFT 마켓플레이스 창업자. 상금도 일시금이 아니라 `$6,000 + 9개월 × $2,000` 인큐베이션이었다 = **심사진이 '계속 만들 팀'을 뽑았다는 신호**.

---

## 5. ★ 1위가 이긴 진짜 이유 — 프레이밍 문법

같은 대회에서 **같은 문제**를 다룬 셋의 결과가 갈렸다.

| 순위 | 팀 | 프레이밍 | 상금 |
|---|---|---|---|
| 1위 | AgentFabric | 안전 = **자율성이 성립하기 위한 조건** (`Agents with limits.` / `autonomous execution without autonomous risk`) | **$24,000** |
| 2위 | Cronos Shield | 안전 = **보호막·리스크 엔진** | $5,000 |
| Dev Tooling | x402 Intent Firewall | 안전 = **sanity check 도구** | $3,000 |

문법은 이것이다 — **주어에서 '막는다'를 빼고, 결과절을 긍정형으로 닫는다.**

**현재 우리 카피는 전부 2위 자리의 문법이다**: `landing.html:113` "사는 쪽 에이전트를 **지키는** 지출 승인 게이트", `:132` "서명 직전에 **지킵니다**", `:148` "한도 **초과 불가**".

### 교체 후보 (권장)

> **헤드라인**: 손을 떼려면, 한도가 있어야 합니다.
> **서브**: 402 Guard 는 AI 에이전트가 서명하기 직전, 이 청구서가 합의한 그 청구서인지 대조합니다.
> 그래서 사람이 화면을 지켜보지 않아도 됩니다.
> **결과절**: 감시 없는 자율. 승인 없는 결제.

'한도'를 제약이 아니라 **손을 떼기 위한 전제**로 뒤집는다. KPI 도 같은 문법으로 바꾼다 —
'유출 0.00' 단독이 아니라 **"이 세션이 사람 없이 집행한 금액 N USDC · 그중 게이트가 되돌린 M건"**.

### 대안 (스펙 원문으로 전부 방어되는 버전)

> **검증되는 자율.** x402 는 구매자의 키를 지킵니다. AP2 는 한도를 정합니다.
> 그런데 **'누구에게, 얼마를, 무엇에 대해' 내는지를 구매자 편에서 묻는 자리**는 비어 있었습니다.
> 402 Guard 가 그 자리에 섭니다.

---

## 6. 개선안 — 8/3 전 / 이후

### 8/3 제출 전 (합계 약 8~11h)

| 순 | 항목 | 공수 | 비고 |
|---|---|---|---|
| 1 | **카피를 자율성 인에이블러 문법으로 교체** | 2.5h | §5. `landing.html:113,132,196`·`index.html` 헤더·`differentiation.md`·`submission.md`. **문서 작업 0시간짜리가 아니라 카피 재설계다** |
| 2 | **devnet Circle USDC 실증** | 3h | 코드 기본값은 이미 고쳐졌다(`setup_devnet.py`). 파우셋 → setup 재실행 → `run_demo --live` → 새 아카이브. 불가능하면 축③ 문구를 'USDC 인터페이스 호환'으로 낮춘다 |
| 3 | **mandate 에 `allowed_payees` + `expires_at`** | 3h | 같은 파일·같은 서명 페이로드라 **묶으면 추가 공수 거의 없음**. 그러면 *"위임장 서명에 봉인된 수취인 제약 — 한 글자만 바꿔도 서명이 깨진다"* 를 화면에서 시연 가능. 만료는 AgentFabric 이 온체인으로 가진 것을 우리도 갖게 된다 |
| 4 | **경쟁 지도 1장** | 1.5h | 우리가 먼저 말한다: *"수취인을 검사하는 팀은 우리 말고도 있습니다(Circle·Kyvern). 다만 **청구서가 합의된 견적과 일치하는지를 서명 직전에 대조**하는 곳은, 우리가 확인한 범위에서 없었습니다."* — 한정어 필수 |
| 5 | **경계선을 우리가 먼저 밝힌다** | 1h | 코드는 안 건드린다. README·소개서에 한 문단: *"402 Guard 가 막는 것은 정직한 에이전트가 악성 청구서에 속는 것입니다. 악성·버그 있는 엔진이 사용자를 배신하는 것은 현재 코드로 막지 못하며, 온체인 이관이 로드맵입니다."* |
| 6 | **반증된 주장 삭제** | 1h | `differentiation.md:3` "직전 x402 수상작 16건 중 트레이딩 봇 0건" — 출처 미확인 + 반례. `:18` "검사 3개뿐"(지금 4개), `:69·126·164·238` "유출 0.00"에 '서명 전 차단 계층' 한정어 |

### 8/3 이후 (파이널 통과 시)

- **MCP 서버 노출**(10h) — 축④를 '심사위원이 직접 붙여봤다'로 확장. 1위가 실제로 쓴 수법.
- **온체인 프로그램 이관**(40h) — 여기서 **1위를 넘어설 여지가 실재한다**: AgentFabric 조차 금액 상한이 온체인 미강제다. Solana 프로그램으로 예산·수취인을 강제하면 그들이 못 한 것을 하는 셈.
- 제3자 판매자 결제 증빙(6h) · 유출 계측 미해결 6건(8h) · 매수/매도 실패 처리 비대칭(1h).

---

## 7. 인용 주의

- AgentFabric 관련 사실은 **저장소 raw 소스로 확인된 것만** 위에 실었다. 수상 근거 인용문은 2차 매체(mexc.co)다.
- **"저쪽은 한도가 없다"고 공격하지 마라.** `budgetEnforceable` 는 그들이 정직하게 단 플래그다.
  우리 서사는 *"세션키 레이어는 '무엇을'까지 강제한다. '얼마를·누구에게'는 아직 그 레이어에 없고, 우리가 그 자리에 있다"* 다.
- 부재 증명에는 항상 **"우리가 확인한 범위에서"**.
