# 상세 설계 — 온체인 예산 집행 `A-lite` (C2 → C1 → C3 → C5)

> **상태**: 설계 완료 · **착수 승인 대기.** 이 문서로 구현하지 않는다.
> **작성** 2026-07-29 · **기준 커밋** `fc65ce4` · **제출 마감** 2026-08-03 23:59 KST
> **선행 문서** [`onchain_budget_rfc.md`](onchain_budget_rfc.md) — 타당성·Go/No-Go 는 거기서 끝났다.
> 이 문서는 그 결론을 **착수 가능한 명세**로 내린 것이다.
>
> **표기 규칙**: `[실측]` = 실제 실행해 확인(명령·출력 있음) · `[코드]` = 소스 직접 확인(file:line) ·
> `[추정]` = 근거 있는 예상, 미검증.
>
> **⚠ 과장 방지 선언**: A-lite 는 **제품 배선이 아니다.** 이 작업이 끝나도
> *"402 Guard 의 예산이 온체인에서 집행된다"* 는 문장은 **거짓**이다. 정확한 문장은
> *"예산 상한을 체인이 집행하는 레일을 독립 스크립트로 실증했고, 엔진 배선은 로드맵이다."*
> 이 문서 §7 이 그 경계를 문서마다 어떻게 쓰는지 확정한다.

---

## 0. 범위 — 한 화면 요약

| | 커밋 | 파일 | 제품 동작 변화 | 중단 가능 |
|---|---|---|---|---|
| 1 | **C2** 위임 모듈 | `payments/delegation.py`(신규) · `scripts/test_delegation.py`(신규) | **없음** (아무도 호출 안 함) | ✅ |
| 2 | **C1** 출처 분리 | `payments/x402_solana.py` 1함수 | **없음** (기본값 경로 바이트 동일) | ✅ |
| 3 | **C3** 증빙 스크립트 | `scripts/demo_delegation.py`(신규) | **없음** | ✅ |
| 4 | **C5** 교차검증 3지갑 | `run_demo.py` · `web/engine.py` | **아카이브 JSON 에 `user` 행 추가** (라이브만) | ✅ |

합계 **약 3.5h** `[추정]`. 신규 파일 3 · 기존 파일 수정 3 · **엔진 결제 경로 수정 0줄.**

**하지 않는 것(C4 이후)**: 엔진 출처 선택 · 거부 예외 처리 · `state` 필드 · 화면 · devnet 재배포.

> **⚠ 순서 주의 — RFC 및 이 문서 초판의 `C2 → C1 → C5 → C3` 에서 C5 를 맨 뒤로 옮겼다(2026-07-29).**
> 사유 둘. ①**C5 는 유일하게 제품 산출물(아카이브 JSON)을 바꾸는 커밋**이고 C3 는 C5 에
> 의존하지 않는다 → C5 를 tip 에 두면 되돌리기가 `git revert` 한 번으로 가장 싸다.
> ②이 작업과 **프런트 전달본 병합이 시간상 겹칠 수 있다.** 프런트는 `web/static/**` 만,
> A-lite 는 그 밖만 건드려 **파일 교집합이 공집합**이므로 어느 쪽을 되돌려도 다른 쪽은
> diff 에 등장하지 않는다 — 다만 되돌릴 일이 가장 많은 커밋을 맨 뒤에 두는 편이 안전하다.
> 잃는 것: 시간이 모자라면 C5 를 못 한다. **C5 는 원래 선택 항목**이라 옳은 손실이다
> (데모·영상에 필요 없고, C4 배선을 미리 안전하게 만들어 두는 커밋이다).
> **§5 의 커밋별 DoD·롤백은 순서와 무관하게 그대로 유효하다.**

---

## 0-1. ★ 이번 설계 세션에서 새로 실측한 것 8건 (RFC 이후)

RFC 는 이것들을 `[추정]` 으로 남겼거나 아예 다루지 않았다. 전부 localnet 프로브로 확인했고
**설계의 계약 조항이 여기서 나온다.** (프로브는 스크래치패드 임시 파일 — 저장소 무수정)

| # | 질문 | 실측 결과 | 설계에 미치는 영향 |
|---|---|---|---|
| **P1** | ATA 가 없는 계정에 `get_account_info_json_parsed` | **예외 없음. `resp.value is None`** | `read_delegation` 이 `exists=False` 로 정상 반환. 예외 처리 불필요 |
| **P2** | 같은 계정에 `get_token_account_balance`(기존 경로) | `RPCException: "could not find account"` | 기존 `_is_account_not_found`(`x402_solana.py:251-260`)와 일치 |
| **P3** | 위임 없는 ATA 에 `revoke` | **성공한다 (오류 아님)** | `revoke_budget` 이 체인 수준에서 **멱등**. 사전 조회 불필요 |
| **P4** | `approve_checked(amount=0)` | **성공하고 `delegate` 는 남는다** — `delegatedAmount='0'` | "한도 0" ≠ "위임 해제". 둘은 **다른 에러 코드**를 낸다(아래) |
| **P5** | 위임 잔여 0(해제 아님) 상태에서 전송 | `custom program error 0x1` | 소진 경로(`0x4`)와 다르다. 분류표가 3분기가 아니라 4분기다 |
| **P6** | 거부 예외에서 코드를 **문자열 파싱 없이** 꺼낼 수 있는가 | **가능.** `exc.args[0].data.err.err.code` → `int` | RFC 가 암시하던 `"0x1" in str(e)` 문자열 매칭을 버린다 |
| **P7** | `approve`/`revoke` 수수료를 **에이전트가 대납**할 수 있는가 | **가능.** 메시지 fee payer=agent · 서명자 `[agent, user]` → user SOL **0 유지**, agent 가 0.00001 SOL 부담 | **user 지갑에 SOL 이 필요 없다.** devnet SOL 파셋 의존 1건 제거 |
| **P8** | 서명자가 모자란 트랜잭션을 만들면 | **`pyo3_runtime.PanicException`** — `BaseException` 직계라 **`except Exception` 에 안 잡힌다** | ★ 함정. 서명자 구성을 **호출 전에** 확정해야 한다. C3 공격 시나리오 설계도 이것 때문에 바뀐다(§6-2 ⑥) |

**P6 확인된 예외 구조** `[실측]`:

```
exc                     solana.rpc.core.RPCException
 └ .args[0]             solders.rpc.errors.SendTransactionPreflightFailureMessage
    ├ .message          "…Error processing Instruction 0: custom program error: 0x1"
    └ .data             RpcSimulateTransactionResult
       └ .err           TransactionErrorInstructionError   (.index=0, .err=…)
          └ .err        InstructionErrorCustom             (.code=1)
```

---

## 1. 인터페이스 계약

### 1-1. C1 — `payments/x402_solana.py`

```python
def build_transfer_transaction(
    payer: Keypair,
    mint: Pubkey,
    dest_owner: Pubkey,
    amount: int,
    decimals: int,
    blockhash: Hash,
    ensure_dest_ata: bool = True,
    memo: Optional[str] = None,
    source_owner: Optional[Pubkey] = None,      # ★ 신규 (맨 끝, 기본값 None)
) -> Transaction:
```

| 항목 | 계약 |
|---|---|
| **타입** | `Optional[Pubkey]`. `str` 은 받지 않는다(호출측이 `Pubkey.from_string`) — 기존 인자들과 동일 규칙 |
| **위치** | **인자 목록 맨 끝.** 기존 호출 4곳이 전부 키워드 인자라 위치 이동 위험은 없으나(`trading_agent.py:511`·`:557`, `broker_agent.py:178`·`:259` `[코드]`) 관례를 지킨다 |
| **`None`(기본)** | `src_ata = ATA(payer.pubkey(), mint)` · `TransferCheckedParams.owner = payer.pubkey()` — **현행 `:99`·`:118` 그대로** |
| **지정 시** | `src_ata = ATA(source_owner, mint)` · **`owner` 는 `payer.pubkey()` 유지** (SPL 은 authority 자리에 delegate 를 넣는다. 계정 순서 동일 — RFC §2-1 #1 `[실측]`) |
| **서명자** | **두 경우 모두 `[payer]` 하나.** 자금 소유자 서명 불필요 |
| **memo** | 변경 없음 — signer 는 계속 `payer.pubkey()`(`:104`) |
| **`ensure_dest_ata`** | 변경 없음 — rent 는 계속 `payer` 부담 |

**사전조건**: `source_owner` 가 주어지면 그 소유자의 ATA 가 이미 존재하고 `payer` 가 delegate 로
등록돼 있어야 한다. **함수는 그것을 검사하지 않는다** — 검사는 `read_delegation`(호출측)의 일이다.

**사후조건 / 하위호환 보장 근거**:
1. `source_owner=None` 이면 명령 목록·계정 순서·서명자 집합이 전부 동일 → **직렬화 바이트 동일**.
   증명은 골든 상수 회귀(§4-1 S1)로 한다.
2. `source_owner=payer.pubkey()` 를 명시해도 `ATA(payer, mint)` 라 (1)과 동일.
3. **판매자측 무영향** — `verify_payment` 는 `dest`·`mint`·`owner`(계정 idx 3)만 읽고 **출처 ATA 를
   아예 보지 않는다**(`x402_solana.py:197-225` `[코드]`, RFC §2-3 `[실측]`). `expected_payer` 는
   브로커 두 정산 경로에서 전달되지 않는다(`broker_agent.py:229-235`·`:151-157` `[코드]`).

**하지 않는 것**: 출처 선택 정책 · 잔액 확인 · 위임 상태 확인 · 실패 분류. 전부 호출측.

### 1-2. C2 — `payments/delegation.py` (신규, 공개 API 전부)

```python
# ---- 차단 코드 (4종) — guard.py:43-56 의 GUARD_* 명명 규칙을 따른다 ----
GUARD_ONCHAIN_BUDGET       = "GUARD_ONCHAIN_BUDGET"        # 위임 잔여 < 요청 (잔액은 충분) ★유일하게 '체인이 한도를 집행했다'
GUARD_ONCHAIN_FUNDS        = "GUARD_ONCHAIN_FUNDS"         # 잔액 < 요청 — 한도 집행이 아니다
GUARD_ONCHAIN_NO_DELEGATE  = "GUARD_ONCHAIN_NO_DELEGATE"   # 위임 소진·회수·미등록
GUARD_ONCHAIN_UNCLASSIFIED = "GUARD_ONCHAIN_UNCLASSIFIED"  # 설명되지 않음 — 어떤 주장도 하지 않는다

class DelegationError(Exception): ...
```

```python
@dataclass(frozen=True)
class DelegationState:
    """SPL 토큰 계정 1개의 위임 상태 스냅샷. 전부 base units 정수."""
    exists: bool                     # ATA 자체가 존재하는가
    delegate: Optional[str]          # 위임 대상 pubkey 문자열. 없으면 None
    delegated_amount: int            # 남은 위임 한도. delegate 가 None 이면 0
    balance: int                     # 계정 잔액

    def is_delegated_to(self, pubkey) -> bool:
        return self.delegate is not None and self.delegate == str(pubkey)
```

```python
async def read_delegation(client, owner: Pubkey, mint: Pubkey) -> DelegationState
```
- **RPC 1회** — `get_account_info_json_parsed(ATA(owner, mint))`. 이 한 번으로 `balance` 와
  `delegated_amount` 를 **둘 다** 얻는다(`tokenAmount.amount`, `delegatedAmount.amount`) `[실측]`.
- **사후조건**: ATA 미존재(`resp.value is None` `[실측 P1]`) → `DelegationState(False, None, 0, 0)`.
- **예외**: RPC 실패는 **삼키지 않고 전파**한다. 불명 실패를 0 으로 바꾸면 `get_token_balance_base`
  가 BUG-01 로 닫은 것과 **똑같은 결함**이 된다(`x402_solana.py:318-331` 독스트링 `[코드]`).
  계정은 있는데 토큰 계정 형식이 아니면 `DelegationError`.
- **하지 않는 것**: 재시도. `rpc_retry`(`:263`)로 감쌀지는 호출측 판단(C3 는 감싼다).

```python
async def approve_budget(client, owner_kp: Keypair, delegate: Pubkey, mint: Pubkey,
                         amount_base: int, decimals: int, *,
                         fee_payer: Optional[Keypair] = None,
                         allow_decrease: bool = False) -> tuple[str, DelegationState]
```
- **의미는 절대값 설정이다** — "이 지갑에서 `delegate` 가 앞으로 꺼낼 수 있는 총액 = `amount_base`".
  SPL `approve` 가 누적이 아니라 덮어쓰기이기 때문이다(RFC §2-1 #6 `[실측]`).
- **사전조건**: `amount_base >= 0`. 위반 시 `ValueError`.
- **동작**: ① `read_delegation` ② 같은 delegate 에게 **이미 더 큰 한도**가 걸려 있고
  `allow_decrease=False` 면 **트랜잭션을 보내지 않고 `DelegationError`** ③ 아니면 `approve_checked`
  전송·confirm ④ `read_delegation` 재조회.
- **서명자**: `fee_payer` 미지정 → 메시지 payer=`owner_kp`, 서명자 `[owner_kp]`.
  지정 & 다른 키 → payer=`fee_payer`, 서명자 **`[fee_payer, owner_kp]`** `[실측 P7]`.
  지정 & 같은 키 → `[owner_kp]` 하나(중복 서명자 방지).
  **★ 이 목록을 틀리면 `except Exception` 으로 못 잡는 `PanicException` 이 난다 `[실측 P8]`.**
- **반환**: `(tx_signature, 갱신된 DelegationState)`.
- **하지 않는 것**: ATA 생성(없으면 그냥 실패한다 — 자금 소유자 지갑에 잔액이 있다면 ATA 는 이미 있다).

```python
async def revoke_budget(client, owner_kp: Keypair, mint: Pubkey, *,
                        fee_payer: Optional[Keypair] = None) -> str
```
- **멱등** — 위임이 없는 계정에 보내도 체인이 성공을 준다 `[실측 P3]`. 사전 조회를 하지 않는다.
- ATA 자체가 없으면 `DelegationError`(보낼 계정이 없다).
- 반환: tx 서명.

```python
def spl_error_code(exc: BaseException) -> Optional[int]
```
- 순수 함수. `exc.args[0].data.err.err.code` 를 읽고, 그 경로가 없으면 `str(exc)` 에서
  `custom program error: 0x(\d+)` 를 찾는 **폴백 1단**. 둘 다 실패하면 `None`.
- 폴백을 두는 이유: solders 버전이 바뀌면 속성 경로가 깨질 수 있는데, **분류가 조용히
  UNCLASSIFIED 로 무너지는 것보다 문자열로라도 맞히는 편이 낫다**. 반대로 문자열을 1순위로 두면
  로케일·메시지 변경에 약하다.

```python
def classify_rejection(code: Optional[int], state: DelegationState,
                       requested_base: int, decimals: int, delegate) -> GuardResult
```
- **순수 함수 · 네트워크 없음.** 상태를 주입받는다 — `guard.check_delivery` 가 `balance_reader`
  를 주입받는 것과 같은 관례(`guard.py:19-21` `[코드]`)이고, 덕분에 분류 로직 전체가
  오프라인 테스트 대상이 된다.
- 판정 규칙은 §3-3.
- `where` 는 `guard.py:224` 와 같은 방식으로 런타임 생성 → `"delegation.py:L{n}"`.

### 1-3. C5 — `run_demo.py` (엔진이 그대로 임포트한다, `engine.py:36` `[코드]`)

```python
async def snapshot_balances(client, trading_pk, broker_pk, usdc_mint, stock_mint,
                            user_pk=None) -> dict          # ★ 신규 인자 (맨 끝, 기본 None)

def usdc_net_out(before: dict, after: dict) -> Decimal      # ★ 신규 (순수 함수로 추출)
```
- `user_pk=None`(기본) → 반환 키 `{"trading", "broker"}` — **현행과 동일**.
- 지정 → `{"trading", "broker", "user"}`. `print_snapshot`(`:94-96`)은 `snap.items()` 순회라
  자동으로 한 줄 늘어난다.
- `usdc_net_out` = `Σ(before[w].usdc) − Σ(after[w].usdc)`, `w ∈ {"trading","user"} ∩ snap.keys()`.
  **`"user"` 키가 없는 옛 아카이브·현행 스냅샷에서 결과가 오늘과 완전히 동일하다.**
- 호출 3곳(`run_demo.py:191`·`:345`, `engine.py:595`·`:1523`)은 `user_pk` 를 넘기도록 갱신한다.
  두 곳 모두 `user_kp` 가 이미 스코프에 있다(`run_demo.py:107`, `engine.py:_user_kp`) `[코드]`.
- `cross` 딕셔너리(`engine.py:1593-1598`, `run_demo.py:370-374`)에 **`"usdc_wallets": ["trading","user"]`**
  한 줄을 추가한다 — 나중에 아카이브를 읽는 사람이 어느 지갑을 합산한 값인지 알아야 한다.

**추가 비용**: 스냅샷 1회당 RPC 3회(SOL·USDC·주식) 증가 → 라이브 세션 1회당 **6회** `[추정]`.
공용 devnet RPC 는 429 를 주므로 무시할 수 없지만, 기존 `rpc_retry` 경로를 그대로 탄다.

### 1-4. 반환 타입 결정 — `GuardResult` 를 **재사용한다**

| 후보 | 판단 |
|---|---|
| 새 `RejectionResult` 신설 | ✗ 필드가 `GuardResult`(`guard.py:72-86`)와 6/6 동일해진다. 이벤트 직렬화(`as_event`)를 한 벌 더 쓰게 된다 |
| **`GuardResult` 재사용** | **✓ 채택.** C4 배선이 `self.bus.emit(ev.GUARD_BLOCKED, {..., **result.as_event()})`(`engine.py:1200-1202` 패턴) **한 줄**로 끝나고, 프론트가 `code`/`detail`/`where`/`expected`/`actual` 을 이미 렌더한다 → **화면 코드 변경 0** |

**임포트 방향**: `payments/delegation.py` → `from payments.guard import GuardResult`.
`guard.py` 는 `config`·`payments.invoice_semantics` 만 임포트한다(`guard.py:38-41` `[코드]`) →
**순환 없음.** `guard.py` 는 이번에 한 줄도 건드리지 않는다.

**단, `GuardError` 는 던지지 않는다.** `GuardError` 는 *"서명 자체가 일어나지 않았다 = 유출 0"* 을
뜻하는 예외다(`guard.py:89-93` `[코드]`). 온체인 거부는 **서명은 했고 체인이 거절한 것**이라 의미가
다르다. `classify_rejection` 은 값을 반환할 뿐 예외를 던지지 않는다.

### 1-5. 이번에 만들지 않는 것 (명시)

- 출처 선택 정책 객체 · `ONCHAIN_BUDGET` 환경변수 · `state.delegation` 필드 · 예산 카드 UI
- `TradingAgent.build_payment(source_owner=)` 통과 인자 — **C4 의 첫 줄**이다
- `setup_devnet.py` 변경 · 재배포 · Firestore 스키마

---

## 2. 데이터 흐름과 상태 전이

### 2-1. 위임 1건의 생애주기

```
        approve_budget(N)              전송 M(<N)              전송(잔여 전액)
 [없음] ───────────────▶ [활성 N] ──────────────▶ [활성 N−M] ──────────────▶ [해제]
   ▲                        │                         │                         │
   │  revoke_budget         │  approve_budget(K)      │                         │
   └────────────────────────┴─────────────────────────┴─────────────────────────┘
                              (덮어쓰기 — 누적 아님)
```

| 전이 | 트리거 | 관측 | 관측 실패 시 |
|---|---|---|---|
| 없음 → 활성 | `approve_budget` (owner 서명 필수) | 반환 `DelegationState` | 전송은 confirm 됐는데 재조회가 실패 → **예외 전파.** 호출측이 "상태 불명"으로 처리하고 진행하지 않는다 |
| 활성 → 부분소모 | delegate 서명 전송 성공 | 체인이 자동 차감 `[실측]`. 다음 `read_delegation` 에서 보인다 | 알 수 없어도 안전 — 다음 전송에서 체인이 다시 판정한다 |
| 부분소모 → 해제 | 잔여를 **정확히** 소진 | `delegate=None`·`delegatedAmount=None` `[실측]` | 위와 동일 |
| 활성 → 해제 | `revoke_budget` (owner 서명) | `delegate=None` | 멱등이라 재시도 안전 `[실측 P3]` |
| 활성 → 활성(재충전) | `approve_budget` | §2-3 | — |
| **활성(잔여 0)** | `approve_budget(0)` | `delegate` **유지**, `delegatedAmount='0'` `[실측 P4]` | 해제와 **다른 상태**다. 에러 코드도 다르다(§3-1) |

### 2-2. 관측은 언제나 `read_delegation` 1회

RPC 는 `get_account_info_json_parsed` 하나만 쓴다. `get_token_account_balance`(기존 경로)는
잔액만 주고 위임 정보를 안 준다 — **두 번 부를 이유가 없다** `[실측]`.

실패 정책은 저장소 관례를 그대로 따른다: **모르는 것을 0 으로 바꾸지 않는다.**
(`get_token_balance_base` 의 BUG-01 독스트링 — `x402_solana.py:319-323` `[코드]`)

### 2-3. ★ "재충전이 덮어쓰기" 실패 모드를 코드로 막는 법

**실패 시나리오**(RFC §7-2 #5): 잔여 15 인 상태에서 "10 만큼 더 주자"는 생각으로
`approve(10)` → 결과는 25 가 아니라 **10**. 한도가 **줄어든다.**

**방어 = 의미를 절대값으로 고정 + 하강을 기본 금지.**

1. 함수명·독스트링·인자명을 전부 절대값으로 읽히게 한다(`amount_base` = 앞으로의 총 상한).
   "재충전"이라는 단어를 API 에 쓰지 않는다.
2. `allow_decrease=False`(기본)에서 **현재 잔여보다 작은 값**을 넣으면 **트랜잭션을 보내기 전에**
   `DelegationError`. 즉 위 시나리오는 예외로 시끄럽게 실패한다.
3. 의도적 감액은 `allow_decrease=True` 로 **한 글자 명시**해야 한다.
4. 정말 "더하고 싶은" 호출자는 스스로 읽고 더한다:
   `st = await read_delegation(...); await approve_budget(..., st.delegated_amount + delta)`.
   **읽기-수정-쓰기 사이의 경쟁은 숨기지 않는다** — 그 사이에 delegate 가 썼다면 총액이 의도보다
   커진다(안전하지 않은 방향). 그래서 권장 사용법은 언제나 **절대값 지정**이다.

### 2-4. 자금 흐름 불변식 — RFC §3-2 를 한 군데 정정한다

RFC 는 *"`delegatedAmount` ≈ AP2 `remaining_usdc`"* 라고 적었다. **등식이 아니라 부등식이다.**

- 체인이 강제하는 것: `Σ(사용자 ATA 에서 나간 금액) ≤ approve 총액` — **단조 증가, 회복 없음.**
- AP2 가 세는 것: `spent = 누적 매수 − 누적 매도`(`credit_sale`, `ap2_mandate.py:157-173` `[코드]`)
  — **오르내린다.**

§3-2 라우팅(재활용은 에이전트 지갑, 신규 투입만 위임 계정)에서 두 값은 대체로 붙어 다니지만,
경계값 처리(§8-1)로 위임이 조금 더 빨리 닳는다. 따라서 성립하는 문장은

> **온체인 잔여 ≤ AP2 잔여** — 즉 **체인 쪽이 항상 더 빡빡하다.**

안전 기능으로서 옳은 방향의 오차다(한도가 예상보다 **늦게**가 아니라 **일찍** 걸린다).
문서·화면에는 등식이 아니라 이 부등식을 써야 한다.

---

## 3. 에러 분류 체계 (RFC §2-2a 미결 종결)

### 3-1. 원인 → 코드 대응표 `[실측]`

| 상황 | `delegate` | 코드 | 우리 라벨 |
|---|---|---|---|
| 위임 잔여 < 요청, 잔액 충분 | agent | **0x1** | `GUARD_ONCHAIN_BUDGET` ★ |
| 잔액 < 요청, 위임 충분 | agent | **0x1** | `GUARD_ONCHAIN_FUNDS` |
| 둘 다 부족 | agent | **0x1** | `GUARD_ONCHAIN_FUNDS`(보수적) |
| `approve(0)` 상태에서 전송 | agent | **0x1** `[P5]` | `GUARD_ONCHAIN_BUDGET` |
| 잔여 소진으로 자동 해제된 뒤 | `None` | **0x4** | `GUARD_ONCHAIN_NO_DELEGATE` |
| `revoke` 후 | `None` | **0x4** | `GUARD_ONCHAIN_NO_DELEGATE` |
| 위임 대상이 다른 지갑 | 타인 | **0x4** `[추정]` | `GUARD_ONCHAIN_NO_DELEGATE` |

**핵심**: `0x1` 하나에 서로 다른 원인 셋이 들어온다. **에러 코드만으로는 라벨을 붙일 수 없다.**

### 3-2. 코드 추출 — 문자열 파싱을 1순위에서 뺀다

`spl_error_code(exc)` 는 §0-1 의 속성 경로를 읽는다. RFC 초안이 암시하던
`"custom program error: 0x1" in str(e)` 는 **폴백으로만** 남는다.
`.index`(실패한 명령 번호)도 같은 객체에서 읽히지만, 우리 결제 tx 에서 Token 프로그램 명령은
`transfer_checked` 하나뿐이라(`build_transfer_transaction` `[코드]`) 사용하지 않는다.

### 3-3. 판정 알고리즘 — **추가 RPC 정확히 1회**

```
실패 발생
  ├ code = spl_error_code(exc)                       # RPC 0회
  ├ state = await read_delegation(user, mint)        # RPC 1회  ← 유일한 추가 호출
  │    └ 실패하면 → GUARD_ONCHAIN_UNCLASSIFIED (detail="상태 조회 실패: {타입}")
  │
  ├ code == 4  또는  not state.is_delegated_to(agent)
  │        → GUARD_ONCHAIN_NO_DELEGATE
  ├ code == 1:
  │    ├ state.balance >= requested and state.delegated_amount < requested
  │    │        → GUARD_ONCHAIN_BUDGET          ★ '체인이 한도를 집행했다' 를 말해도 되는 유일한 분기
  │    ├ state.balance < requested
  │    │        → GUARD_ONCHAIN_FUNDS
  │    └ 그 외(둘 다 충분한데 실패) → GUARD_ONCHAIN_UNCLASSIFIED
  └ code 가 1·4 가 아니거나 None → GUARD_ONCHAIN_UNCLASSIFIED
```

`expected`/`actual` 채우는 규칙(화면에 그대로 뜬다):

| 라벨 | `expected` | `actual` |
|---|---|---|
| BUDGET | `위임 잔여 {from_base_units(delegated)}` | `요청 {from_base_units(requested)}` |
| FUNDS | `잔액 {balance}` | `요청 {requested}` |
| NO_DELEGATE | `delegate={agent}` | `delegate={state.delegate or "없음"}` |
| UNCLASSIFIED | `""` | `code={code} delegated={…} balance={…}` |

### 3-4. "잔액 부족을 한도 집행으로 오표시" 방지 — 구체적 방어 4겹

1. **BUDGET 분기의 진입 조건에 `balance >= requested` 를 명시적으로 넣는다.**
   둘 다 부족하면 자동으로 FUNDS 로 떨어진다(§3-1 3행). 즉 *한도만으로 막혔을 때만* 한도를 주장한다.
2. **설명되지 않는 실패는 주장하지 않는다** — UNCLASSIFIED 를 만들어 "모른다"를 표현 가능하게 한다.
   (없으면 구현자는 반드시 BUDGET 이나 FUNDS 중 하나로 억지 분류하게 된다.)
3. **판정 근거를 아카이브에 함께 남긴다** — §6-4 `classification[]` 에 `spl_error_code`,
   `delegated_usdc`, `balance_usdc`, `requested_usdc` 원값을 적는다. 사후에 반박 가능해야 한다.
4. **테스트에 음성 대조를 박는다** — "에러 코드만 보는 순진한 분류기"가 케이스 2·3 에서
   BUDGET 을 내놓는다는 것을 같은 파일에서 단언한다(§4-2 N1). 회귀 방지이자 문서다.

**남는 한계(정직하게 적어 둘 것)**: 상태를 **실패 이후에** 읽으므로 그 사이에 소유자가 돈을 쓰거나
재승인하면 판정이 어긋날 수 있다. 그래서 라벨은 *최선 추정*이고, 원값을 함께 남긴다.

### 3-5. 하드 검사 **8종 카운트에 영향 없음** — 그리고 그게 왜 중요한가

`guard.py:117-120` 이 세는 기준을 명시한다 — *"서로 다른 이유로 **서명을 거부**시킬 수 있는 검사
1개 = 1종"* `[코드]`. 온체인 거부는 **서명한 뒤** 체인이 거절하는 것이므로 이 정의에 들어가지 않는다.

→ **`DEMAND_CODES`(`guard.py:53-56`)에 추가하지 않는다.** 8종은 8종 그대로다.
→ **문서 12지점 치환이 발생하지 않는다.** (CLAUDE.md 가 "검사 몇 종 숫자 통일"을 12지점 치환
   작업으로 기록해 둔 그 항목이다. A-lite 가 그 작업을 되살리지 않는다.)
→ 상수를 `guard.py` 가 아니라 **`payments/delegation.py` 에 두는 이유**이기도 하다. 이름만
   `GUARD_ONCHAIN_*` 접두를 따라가 로그·배지 렌더링이 같은 계열로 보이게 한다.

---

## 4. 테스트 매트릭스

신규 파일 **1개**(`scripts/test_delegation.py`) → 단위 테스트 **22종 → 23종**.
하네스는 저장소 관례(`scripts/test_dry_sell.py` 형태): pytest 아님, `check(name, cond, detail)` 누적,
`print(f"결과: {ok}/{total} 통과")`, `SystemExit(asyncio.run(main()))`.

### 4-1. 케이스 목록

**섹션 1 — `classify_rejection` (C2 · 오프라인 · 네트워크 0)**

| # | 입력 | 기대 |
|---|---|---|
| C-1 | code=1, balance=90, delegated=15, want=20 | `GUARD_ONCHAIN_BUDGET` |
| C-2 | code=1, balance=90, delegated=500, want=200 | `GUARD_ONCHAIN_FUNDS` |
| **C-3** | code=1, balance=5, delegated=5, want=20 (둘 다 부족) | **`GUARD_ONCHAIN_FUNDS`** ★ |
| C-4 | code=1, delegated=0, delegate=agent (`approve(0)`) | `GUARD_ONCHAIN_BUDGET` |
| C-5 | code=4, delegate=None | `GUARD_ONCHAIN_NO_DELEGATE` |
| C-6 | code=4, delegate=제3자 | `GUARD_ONCHAIN_NO_DELEGATE` |
| C-7 | code=1, delegate=제3자 (코드는 1인데 위임이 남에게) | `GUARD_ONCHAIN_NO_DELEGATE`(위임 확인이 우선) |
| C-8 | code=1, balance·delegated 둘 다 충분 | `GUARD_ONCHAIN_UNCLASSIFIED` |
| C-9 | code=None | `GUARD_ONCHAIN_UNCLASSIFIED` |
| C-10 | code=1, delegated == want (딱 맞음인데 실패) | `GUARD_ONCHAIN_UNCLASSIFIED` |
| C-11 | 반환이 `GuardResult` 이고 `ok is False`, `where` 가 `delegation.py:L` 로 시작 | 통과 |
| C-12 | `as_event()` 키 6개가 `guard.GuardResult.as_event` 와 동일 | 통과(프론트 무변경 보장) |
| C-13 | BUDGET 의 `expected`/`actual` 에 UI 단위 숫자가 들어간다 | 통과 |

**섹션 2 — `DelegationState`·`spl_error_code` (C2 · 오프라인)**

| # | 내용 |
|---|---|
| S-1 | `is_delegated_to` — None / 일치 / 불일치 3케이스 |
| S-2 | `spl_error_code` 폴백: 속성 경로 없는 가짜 예외 + `str()` 에 `0x4` → `4` |
| S-3 | `spl_error_code(ValueError("아무것도 아님"))` → `None` |
| **S-4** | **닫힌 포트 RPC(`http://127.0.0.1:1`)로 `read_delegation` → 예외 전파. 0 반환 아님** ★ BUG-01 계열 |

**섹션 3 — localnet (C2 · `--localnet` 플래그 있을 때만)**

| # | 내용 |
|---|---|
| L-1 | `approve_budget(25)` → `delegated_amount == 25_000_000`, `delegate == agent` |
| **L-2** | `approve_budget(10)` (allow_decrease 미지정) → `DelegationError` **이고 체인 상태가 25 그대로** ★ |
| L-3 | `approve_budget(10, allow_decrease=True)` → 10 으로 내려감 |
| L-4 | `revoke_budget` → `delegate is None` |
| L-5 | `revoke_budget` 재호출 → 오류 없음(멱등) |
| L-6 | ATA 없는 소유자에 `read_delegation` → `exists=False`, 예외 없음 |
| L-7 | `fee_payer=agent` 로 approve → 성공하고 **user SOL 불변** |
| L-8 | 실제 한도 초과 거부 예외에 `spl_error_code` → `1` / 소진 후 → `4` |

**섹션 4 — `source_owner` (C1 · 오프라인)**

| # | 내용 |
|---|---|
| **S1** | `source_owner=None` 으로 만든 tx 의 base64 가 **골든 상수와 바이트 동일** ★ |
| S2 | `source_owner=payer.pubkey()` → S1 과 동일 |
| S3 | `source_owner=user` → 출처 ATA == `ATA(user, mint)` |
| S4 | 같은 tx 의 authority(계정 idx 3) == `payer.pubkey()` |
| S5 | 서명자 1개(`len(tx.signatures)`), user 서명 불필요 |
| S6 | memo signer == payer |
| S7 | `verify_payment(...)` 가 `source_owner` 지정본에서도 **통과** (RFC §2-3 재현) |
| S8 | `verify_payment(expected_payer=payer)` 도 통과 |

**섹션 5 — 교차검증 합산 (C5 · 오프라인)**

| # | 내용 |
|---|---|
| X-1 | `snapshot_balances(user_pk=None)` → 키 2개 (스텁 클라이언트) |
| X-2 | `user_pk` 지정 → 키 3개 |
| X-3 | `usdc_net_out`: user 키 없음 → 현행 값과 동일 |
| **X-4** | user 있고 값이 0 → 현행 값과 **동일** ★ 하위호환 핵심 |
| X-5 | user 에서 10 나감·trading 불변 → `10` |
| X-6 | 옛 아카이브 형식(user 키 부재) → `KeyError` 없이 동작 |

### 4-2. 음성 대조 (negative control) — 3건

이 저장소 관례다(BUG-01 `test_settlement`, BUG-09 `test_finalize_race`, `test_dry_sell.py:65-85`).
**"그 테스트가 결함을 실제로 잡는가"를 같은 파일에서 증명한다.**

| # | 대상 | 방법 | 통과 판정 |
|---|---|---|---|
| **N1** | 분류기 | 테스트 파일 안에 `_naive_classify(code)` (0x1 → 무조건 BUDGET)를 두고 C-2·C-3 에 적용 | naive 는 **BUDGET 을 내놓는다**(=거짓 광고), 우리 분류기는 FUNDS. 둘이 달라야 통과 |
| **N2** | `source_owner` 바이트 동일성 | **C1 착수 직전** 현행 함수로 고정 키·고정 blockhash·고정 memo 의 payload base64 를 출력해 상수로 박는다 | 수정 후 S1 이 그 상수와 일치. (상수를 나중에 만들면 증명이 아니다) |
| **N3** | 3지갑 합산 | 옛 식(`trading` 만)으로 X-5 를 계산하면 `0` 이 나온다는 것을 단언 | 새 식 `10` ≠ 옛 식 `0` |

**N2 준비 명령**(C1 수정 전에 1회, 출력은 테스트 상수로 붙여넣기):

```bash
python -c "from solders.keypair import Keypair; from solders.pubkey import Pubkey; from solders.hash import Hash; import payments.x402_solana as x; kp=Keypair.from_bytes(bytes(range(1,65))); m=Pubkey.from_string('4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU'); d=Pubkey.from_string('So11111111111111111111111111111111111111112'); print(x.encode_payload(x.build_transfer_transaction(kp,m,d,1234567,6,Hash.default(),memo='AT1:ord_0123456789:abcd1234')))"
```

### 4-3. 기존 22종 중 재실행 대상과 사유

| 테스트 | 사유 | 어느 커밋 뒤 |
|---|---|---|
| `test_settlement.py` | `verify_payment` 를 검증하는 유일한 곳 = C1 이 만드는 tx 모양의 수신측 | C1 |
| `test_http402.py` | `build_transfer_transaction` → `encode_payload` → 원격 검증 왕복(53건) | C1 |
| `test_guard.py` | `GuardResult` 를 공유하게 되므로 | C2 |
| `test_leak_kpi.py` | RPC 3함수를 스텁한다 — C5 의 스냅샷 시그니처 변경에 가장 먼저 걸린다 | C5 |
| `test_dry_sell.py` · `test_multistock.py` · `test_trend.py` | `engine.start` 경로가 `snapshot_balances` 를 임포트한다(`engine.py:36`) | C5 |
| 나머지 15종 | 임포트 무관하나 배치가 싸다 | 푸시 전 1회 |

**전체 배치**(PowerShell, CLAUDE.md 기본 셸):

```powershell
Get-ChildItem scripts/test_*.py | ForEach-Object { python $_.FullName *> $null; "{0,-30} rc={1}" -f $_.Name, $LASTEXITCODE }
```

```bash
python scripts/red_team.py --report
```

### 4-4. localnet 필요 / 오프라인 분리

- **오프라인(기본 실행)**: 섹션 1·2·4·5 — 검증기 없이 돈다. 배치 실행이 네트워크에 묶이지 않는다.
- **localnet(`--localnet`)**: 섹션 3 (L-1~L-8) + C3 데모 전체.
- 기본 실행에서 섹션 3 은 **건너뛴 개수를 출력**한다(조용히 통과로 세지 않는다).

```bash
wsl -d Ubuntu --cd /root -- /root/.local/share/solana/install/active_release/bin/solana-test-validator
```

---

## 5. 커밋별 수용 기준(DoD)과 롤백

공통 규칙: 각 커밋은 **자체로 통과**해야 하고, 커밋 직후 `git push origin main`(상시 승인).
**전부 중단 가능 지점이다.**

### C2 — `payments/delegation.py`

| | |
|---|---|
| **착수 전 상태** | `approve`/`delegate`/`revoke` 가 저장소 코드에 **0줄** (`scripts/poc_delegate.py` 제외) `[코드]` |
| **변경 파일** | `payments/delegation.py`(신규) · `scripts/test_delegation.py`(신규) — **기존 파일 0개** |
| **완료 판정** | `python scripts/test_delegation.py` → rc=0 (오프라인 섹션 전부)<br>`python scripts/test_delegation.py --localnet` → rc=0 (L-1~L-8)<br>`git diff --stat HEAD~1` 이 신규 2파일만 |
| **롤백** | `git revert` 또는 두 파일 삭제. **호출자가 없어 회귀 0** |
| **다음에 넘기는 것** | `read_delegation`·`classify_rejection` 은 C3 가 그대로 쓴다 |
| **중단 시 저장소** | 새 모듈이 존재하되 제품 경로에서 도달 불가. 테스트 23종 통과 상태 |

### C1 — `source_owner`

| | |
|---|---|
| **착수 전** | **N2 골든 상수를 먼저 뽑는다**(§4-2). 이걸 빠뜨리면 하위호환 증명이 사후 조작이 된다 |
| **변경 파일** | `payments/x402_solana.py`(1함수) · `scripts/test_delegation.py`(섹션 4 추가) |
| **완료 판정** | `python scripts/test_delegation.py` (S1~S8 포함) rc=0<br>`python scripts/test_settlement.py` rc=0<br>`python scripts/test_http402.py` rc=0 |
| **롤백** | `git revert`. 호출부를 안 건드렸으므로 단독 되돌리기 안전 |
| **다음에 넘기는 것** | C3 가 `source_owner=user` 로 실제 결제 tx 를 만든다. C4 는 여기에 인자 하나를 더 태운다 |
| **중단 시** | 인자가 존재하지만 아무도 지정하지 않음 = 현행 동작 |

### C3 — 증빙 스크립트

| | |
|---|---|
| **변경 파일** | `scripts/demo_delegation.py`(신규) — **기존 파일 0개** |
| **완료 판정** | localnet 기동 후 `python scripts/demo_delegation.py` → rc=0<br>`artifacts/tx/*_delegation.json` 생성 · `summary.leak_usdc == "0.00"` · `summary.onchain_rejections == 3` · `wired_into_product == false`<br>터미널 출력에 ④와 ⑤가 **서로 다른 라벨**로 찍힌다 |
| **롤백** | 파일 삭제 |
| **중단 시** | 영상 컷·아카이브 확보 완료. **여기까지가 A-lite 가 심사에 내놓는 산출물 전부다** — 다음 C5 는 선택 항목이라 여기서 멈춰도 잃는 것이 없다 |

### C5 — 교차검증 3지갑 (선택 · 맨 뒤에 둔다)

| | |
|---|---|
| **왜 마지막인가** | 유일하게 제품 산출물을 바꾸는 커밋이라 tip 에 두면 되돌리기가 가장 싸다(§0 순서 주의). C3 는 이 커밋에 의존하지 않는다 |
| **변경 파일** | `run_demo.py`(`snapshot_balances` + `usdc_net_out` 추출 + 호출 2곳 + 아카이브) · `web/engine.py`(호출 2곳 + `_archive` cross 계산) · `scripts/test_delegation.py`(섹션 5) |
| **완료 판정** | 위 3개 + `test_leak_kpi.py`·`test_dry_sell.py`·`test_multistock.py` rc=0<br>**localnet 라이브 세션 1회**(`python run_demo.py --live --ticks 12`)에서 `usdc_ok=True`·`stock_ok=True`<br>생성된 아카이브에 `balances_before.user` 와 `cross_check.usdc_wallets` 존재 |
| **롤백** | `git revert` 1회(tip). 아카이브 스키마가 되돌아가지만 **읽는 코드가 없어** 마이그레이션 불필요. 이 커밋 뒤에 프런트 병합 커밋이 얹혀 있어도 파일 교집합이 공집합이라 충돌·되돌림 없음 |
| **다음에 넘기는 것** | C4 가 자금을 실제로 user 지갑에 두었을 때 교차검증이 이미 맞는다 |
| **중단 시** | 라이브 아카이브에 `user` 행이 하나 늘어난 상태. 값은 현재 항상 0 |
| **⚠ 유일한 관측 변화** | 이 커밋만 제품 산출물(JSON)을 바꾼다. 드라이런·화면·API 는 무영향 |

**하드 스톱**(RFC §6-3 유지): **8/2 00:00 이후 본 코드 변경 금지**(문서만).
C1+C2 가 **2.5h** 안에 안 끝나면 중단하고 그 시점 커밋에서 멈춘다.

**프런트 전달본이 중간에 도착하면**: 되돌리기 걱정보다 **작업트리 청결**이 실제 위험이다.
반쯤 만든 커밋 위에 전달본을 덮지 않는다 — 진행 중이면 `git stash` → 프런트 병합을 **단독 커밋**
(반드시 `web/static/**` 만 들어 있는지 먼저 확인) → `git stash pop`. A-lite 커밋과 프런트 커밋은
파일이 겹치지 않으므로 어느 쪽을 `git revert` 해도 상대는 diff 에 등장하지 않는다.

---

## 6. C3 — `scripts/demo_delegation.py` 설계 (실제 산출물)

형태는 `scripts/demo_http402.py` 를 그대로 따른다(단계별 `hr()` 배너 → 검증 → `transcript` 누적
→ `artifacts/` JSON → 요약 → rc). 그 파일이 이미 심사용 증빙 스크립트의 관례다.

### 6-1. 실행 형태와 네트워크 선택

```bash
python scripts/demo_delegation.py            # 기본: localnet + 임시 지갑 + 테스트 민트
python scripts/demo_delegation.py --devnet   # devnet: CFG 지갑 + CFG.usdc_mint (발행 안 함)
```

- **기본이 localnet 인 것이 안전장치다.** `.env` 의 `SOLANA_RPC_URL` 이 devnet 을 가리켜도
  플래그 없이는 devnet 으로 나가지 않는다(RPC 를 `http://127.0.0.1:8899` 로 고정).
- `--devnet` 은 `CFG.rpc_url`·`secrets/{user,trading,broker}.json`·`CFG.usdc_mint` 를 쓰고
  **민트를 만들지 않는다.** 시작 시 user 지갑 USDC 잔액을 조회해 부족하면 **중단하고 파우셋 안내**
  (`setup_devnet.py:283-289` 의 문구 관례를 따른다).
- `--budget`(기본 25) · `--spend`(기본 10) 로 금액 조절. devnet 은 소액이 기본값이어야 한다(§8-3).

### 6-2. 시나리오 8단계

| 단계 | 내용 | 온체인 tx | 검증 |
|---|---|---|---|
| ① 준비 | 3지갑 · (localnet) 테스트 민트 생성 · user 에 100 발행 | 2~3 | 잔액 100 |
| ② **approve** | user 가 agent 를 delegate 로 등록(25). **fee payer = agent** `[실측 P7]` | **1** ★ | `delegate==agent`·`delegatedAmount==25` |
| ③ **정상 결제** | **우리 실제 함수**로 10 전송 — `build_transfer_transaction(payer=agent, source_owner=user, memo=AT1:…)` → 브로커의 **실제 `verify_payment`** 통과 → 제출·확정 | **1** ★ | `ok=True`·잔액 90·`delegatedAmount` **25→15 (체인이 스스로 깎았다)** |
| ④ **한도 초과** | 20 요청 (잔액 90 은 충분) | 0 (preflight 거절) | `code=1` → `GUARD_ONCHAIN_BUDGET`·유출 0.00 |
| ⑤ **★대조군** | 한도를 500 으로 올린 뒤 잔액 초과 200 요청 | 0 | `code=1`(④와 동일) → **`GUARD_ONCHAIN_FUNDS`** |
| ⑥ 자기 상향 공격 | agent 가 **자기를 owner 로** `approve(999)` 시도 | 0 | 체인 거절(`0x4`). **⚠ `owner=user` 로 만들면 클라이언트에서 `PanicException` 으로 프로세스가 죽는다 `[실측 P8]` — 반드시 `owner=agent`** |
| ⑦ **revoke** | user 가 회수 → 이후 전송 시도 | **1** ★ | `delegate=None` → `code=4` → `GUARD_ONCHAIN_NO_DELEGATE` |
| ⑧ 아카이브 | JSON 기록 + 요약 출력 | — | rc=0 조건 |

⑤가 이 스크립트의 존재 이유다. **같은 에러 코드에서 우리가 두 원인을 갈라내는 장면**이고,
그것이 없으면 ④는 "잔액이 없어서 실패한 것 아니냐"는 한 문장에 무너진다.

### 6-3. 터미널 출력 초안 (영상 컷에 그대로 나갈 문구)

```
402 Guard — SPL Token 위임으로 예산 상한을 체인에 내린다 (레일 증명 · 제품 배선 아님)
  네트워크   : solana-localnet  http://127.0.0.1:8899
  user (자금 소유자)  : 9xQe…3Kd   USDC 100.000000
  agent(거래 실행자)  : 4Bn7…pQ2   USDC   0.000000   ← 이 지갑에는 돈이 없다

── ② 사용자가 에이전트에게 예산 25 USDC 를 위임한다 (SPL Token approve) ──────
  서명자     : user (자금 소유자)          수수료 : agent 대납 (user SOL 0)
  tx         : 5KJ…9fA
  explorer   : https://explorer.solana.com/tx/5KJ…?cluster=custom&customUrl=…
  계정 상태  : delegate=agent  delegatedAmount=25.000000

── ③ 에이전트가 자기 서명만으로 사용자 자금 10 USDC 를 결제한다 ──────────────
  트랜잭션   : payments/x402_solana.build_transfer_transaction(source_owner=user)
  판매자 검증: verify_payment -> ok=True  amount=10000000   (판매자 코드 변경 0줄)
  tx         : 2Ff…7Rm
  계정 상태  : 잔액 100 → 90 ·  delegatedAmount 25.000000 → 15.000000
  ※ 한도를 깎은 것은 우리 코드가 아닙니다. SPL Token 프로그램이 깎았습니다.

── ④ 한도를 넘는 20 USDC 결제를 시도한다 (잔액 90 은 충분하다) ───────────────
  체인 응답  : custom program error 0x1
  기록 여부  : 체인에 남지 않음 (preflight 거절 · 수수료 0 · 서명 미기록)
  판정       : GUARD_ONCHAIN_BUDGET — 위임 잔여 15.000000 < 요청 20.000000
  유출       : 0.00 USDC
  ※ 이 거절은 402 Guard(파이썬)가 한 것이 아닙니다. 우리는 서명해서 보냈고,
     Solana 가 거절했습니다.

── ⑤ 대조군 — 한도를 500 으로 올린 뒤, 잔액(90)을 넘는 200 을 요청한다 ────────
  체인 응답  : custom program error 0x1        ← ④와 같은 코드입니다
  판정       : GUARD_ONCHAIN_FUNDS — 잔액 90.000000 < 요청 200.000000
  ※ 에러 코드만으로는 '한도 거부'와 '잔액 부족'을 구분할 수 없습니다. 그래서 실패할 때마다
     계정 상태를 한 번 더 읽어 어느 쪽인지 판정합니다. 구분하지 않으면 지갑이 빈 것을
     "체인이 한도를 집행했다"고 광고하게 됩니다.

── ⑦ 사용자가 위임을 회수한다 (revoke) ───────────────────────────────────────
  tx         : 8Yh…2Lp    계정 상태 : delegate=None
  이후 전송  : custom program error 0x4 → GUARD_ONCHAIN_NO_DELEGATE
  ※ 엔진을 멈추지 않고도, 사용자가 자기 지갑에서 한 번에 끊을 수 있습니다.

── 요약 ──────────────────────────────────────────────────────────────────────
  체인이 집행한 것  : 이 계정에서 이 민트를 누적 25 USDC 까지 — 그 하나
  체인이 못 보는 것 : 수취인 · 종목 · 청구서 의미 · 건별 한도 · 배송 → 402 Guard 가 담당
  온체인 거절       : 3건 (한도 1 · 잔액 1 · 회수 후 1)   유출 : 0.00 USDC
  제품 배선 여부    : 아니오 — 이 스크립트는 레일 증명 전용이고 엔진 경로는 무변경입니다
  증빙              : artifacts/tx/20260731_1420_solana-localnet_delegation.json
```

### 6-4. 아카이브 스키마

경로: `artifacts/tx/{YYYYMMDD_HHMM}_{network}_delegation.json` — 기존 `_web_session`·`_live_buy`
접미 관례와 나란히 선다. 상위 키는 기존 파일과 **의도적으로 같은 이름**을 쓴다(`generated_at`,
`network`, `rpc_url`, `wallets`, `mints`).

```json
{
  "generated_at": "2026-07-31T14:20:11",
  "source": "scripts/demo_delegation.py",
  "kind": "spl-token-delegation",
  "wired_into_product": false,
  "network": "solana-localnet",
  "rpc_url": "http://127.0.0.1:8899",
  "wallets": { "user": "9xQe…", "agent": "4Bn7…", "broker": "7Tz2…" },
  "mints": { "usdc": "…", "decimals": 6, "self_minted": true },
  "budget": { "approved_usdc": "25", "unit": "cumulative-gross-draw-from-owner-ata" },
  "steps": [
    { "step": "approve", "ok": true, "signature": "5KJ…", "explorer": "https://…",
      "delegated_before": "0", "delegated_after": "25.000000" },
    { "step": "transfer", "ok": true, "signature": "2Ff…", "explorer": "https://…",
      "amount_usdc": "10", "verify_payment": { "ok": true, "reason": "검증 통과" },
      "balance_before": "100.000000", "balance_after": "90.000000",
      "delegated_before": "25.000000", "delegated_after": "15.000000" },
    { "step": "revoke", "ok": true, "signature": "8Yh…", "explorer": "https://…" }
  ],
  "classification": [
    { "case": "over-limit", "requested_usdc": "20", "balance_usdc": "90",
      "delegated_usdc": "15", "spl_error_code": 1,
      "code": "GUARD_ONCHAIN_BUDGET", "detail": "…", "where": "delegation.py:L…" },
    { "case": "insufficient-funds", "requested_usdc": "200", "balance_usdc": "90",
      "delegated_usdc": "500", "spl_error_code": 1,
      "code": "GUARD_ONCHAIN_FUNDS", "detail": "…" },
    { "case": "self-raise", "spl_error_code": 4, "code": "GUARD_ONCHAIN_NO_DELEGATE" },
    { "case": "after-revoke", "spl_error_code": 4, "code": "GUARD_ONCHAIN_NO_DELEGATE" }
  ],
  "summary": {
    "approve_tx": "5KJ…", "transfer_tx": "2Ff…", "revoke_tx": "8Yh…",
    "onchain_rejections": 3,
    "limit_enforced_by_chain": true,
    "self_raise_blocked": true,
    "leak_usdc": "0.00"
  },
  "boundary": "체인이 강제하는 것은 '이 계정에서 이 민트를 누적 얼마까지'뿐이다. 수취인·종목·청구서 의미·건별 한도·배송은 402 Guard(오프체인)가 계속 담당한다."
}
```

**`"wired_into_product": false` 가 이 스키마에서 가장 중요한 한 줄이다.** 기계 판독 가능한
과장 방지 장치이고, 나중에 C4 를 하면 `true` 로 바뀌는 단 하나의 필드가 된다.

`classification[]` 이 §3-4 방어 3번을 만족시킨다 — 라벨만이 아니라 **판정 근거 원값**이 남는다.

### 6-5. 영상 배치 — ★ 별도 컷을 새로 만들지 않는다

CLAUDE.md 는 이미 축③ 보강으로 **`curl -i` 402 원문 6초 컷**을 D 앞에 신설하기로 적어 두었다.
A-lite 컷도 축③·터미널·같은 성격이다 → **둘을 하나의 12초 축③ 세그먼트로 합친다.**

| | 내용 | 시간 |
|---|---|---|
| X-1 | 배포 URL `curl -i -X POST /broker/orders` → **402 + accepts[] + Circle 공식 민트** | 6s |
| X-2 | `demo_delegation.py` 의 **④와 ⑤ 두 줄** — 같은 `0x1`, 다른 판정 | 6s |

- **위치**: D(공격 1) 앞. **재원**: A −4s · B −3s · G −5s (큐시트가 이미 "3분 초과 시 G 축약"을
  1순위로 규정, `submission.md:212` `[코드]`). **D·E 는 건드리지 않는다.**
- **부수 이득**: G 구간의 explorer 화면이 자체발행 민트 문제로 조건부였는데(`submission.md:217-219`),
  X-2 는 **localnet 증빙**이라 그 논란이 없다. 심사 기준이 명시한 평가 네트워크가
  Devnet·Localnet 둘 다이고, SPL Token 프로그램 주소는 두 네트워크에서 동일하다.
- **내레이션 초안**: *"한도는 지금 파이썬이 지킵니다. 그래서 한도를 체인에 내려 봤습니다.
  같은 에러 코드가 돌아오는 두 경우를 구분하지 않으면, 지갑이 빈 것을 한도라고 광고하게 됩니다."*
- **금지**: 이 컷에 *"예산이 온체인에서 집행됩니다"* 자막을 붙이지 않는다. 자막은
  **"레일 검증 · 제품 배선은 로드맵"**.

### 6-6. 심사위원 재현 조건

```bash
git clone … && cd solana-agent && pip install -r requirements.txt
solana-test-validator                      # 별도 터미널
python scripts/demo_delegation.py
```
`.env` 불필요(localnet 기본값 · 임시 지갑 · 테스트 민트 자체 생성) · 시크릿 불필요 ·
Gemini 키 불필요 · devnet SOL 불필요. **`run_demo.py` 대표 명령과 같은 수준의 무설정 재현**이다.

---

## 7. 문서 치환 목록

**원칙 — A-lite 는 제품을 바꾸지 않으므로, "무엇이 좋아졌다"고 고칠 문서가 거의 없다.**
RFC §5-12 는 *전체 A 배선*을 전제로 문서를 "전부 재작성"이라 적었지만, A-lite 에 그대로 적용하면
그게 바로 과장이다. 아래는 **A-lite 에서 실제로 손대는 것만** 확정한 목록이다.

### 7-1. 바꾸는 것 (5곳)

| # | 파일:줄 | 현재 | 교체/추가 |
|---|---|---|---|
| **D1** | `docs/artifacts/pitch_deck.html:639` | "**저희 한도 집행도 온체인이 아닙니다** — 에이전트 프로세스 안의 코드가 서명 직전에 차감합니다(`ap2_mandate.py`). 온체인 이관은 로드맵입니다." | 앞부분 유지 + 마지막 문장 교체: "온체인 이관은 로드맵이고, **레일은 이미 실증했습니다** — SPL Token 위임으로 예산 상한을 체인이 집행하고 초과 결제를 거절하는 것을 독립 스크립트로 확인했습니다(`scripts/demo_delegation.py`). **제품 경로에는 아직 연결하지 않았습니다.**" **⚠ 게시본 재발행 필요**(같은 URL 유지) |
| **D2** | `README.md:105-106` | "3. … 악성·버그 있는 엔진이 사용자를 배신하는 것은 현재 코드로 막지 못하며, **온체인 프로그램 이관이 로드맵입니다.**" | 같은 항목 뒤에 한 문장 추가: "그 이관의 첫 조각(예산 상한의 체인 집행)은 `scripts/demo_delegation.py` 로 **레일만 검증한 상태**이고, 엔진 결제 경로는 아직 오프체인입니다." |
| **D3** | `docs/reports/agentfabric_benchmark.md:73` | "1. **온체인 스마트컨트랙트 집행** — … 우리 Guard 는 파이썬 프로세스 안 평범한 객체이고 …" | 각주 1줄 추가: "(2026-07-31 갱신 — 금액 축 한정으로 SPL Token 위임 레일을 실증했다: `docs/design/onchain_budget_design.md`. **갭은 닫히지 않았다** — 배선 전이고, 위임은 수취인·대상을 강제하지 않는다.)" |
| **D4** | `docs/FEATURES.md` §1-1 표(`:30` 헤더 = 기능·설명·위치·상태) | — | 행 1개 추가 — **기능**: `온체인 예산 레일 (A-lite)` · **설명**: "SPL Token 위임으로 예산 상한을 체인이 집행. 한도 초과·회수 후 결제를 체인이 거절하고, 같은 에러 코드인 잔액 부족과 구분해 라벨링" · **위치**: `payments/delegation.py`, `scripts/demo_delegation.py` · **상태**: "⚠ **제품 미배선** — 독립 증빙 전용. localnet 아카이브 1건 · 단위 23종" |
| **D5** | `docs/submission.md §6` 큐시트 | A 22s · B 18s · G 20s | §6-5 대로 X 컷 12s 신설 + A 18s · B 15s · G 15s. **말하지 말 것** 목록(`:214-215`)에 한 줄 추가: *"예산이 온체인에서 집행됩니다"(배선 전)* |

### 7-2. **바꾸지 않는 것** (판단과 사유)

| 파일:줄 | 현재 | 왜 그대로 두는가 |
|---|---|---|
| `web/static/landing.html:224` | "**예산을 코드가 집행** / 표시용 한도가 아니라 AP2 위임장에서 실제 차감" | **지금도 사실이다**(`ap2_mandate.py:126-133` 이 실제로 검사·차감). 여기에 온체인을 붙이면 **배선하지 않은 것을 첫 화면이 주장**하게 된다 |
| `web/static/index.html:364` | 예산 카드 "코드가 집행하는 상한" | 위와 같음. **화면 변경 0 = 재촬영 사유 0** |
| `docs/submission.md §2-1` 정직한 경계선 6줄 | — | 온체인 항목이 원래 없다. A-lite 로 **새로 생기는 경계선도 없다**(제품이 안 바뀌므로) |
| `docs/axis2_ai_narrative.md:212` | "온체인 이관이 로드맵입니다" | 여전히 참 |
| `payments/guard.py` 문서·"하드 검사 8종" 12지점 | — | §3-5 — **숫자가 안 바뀐다** |

### 7-3. 부수 발견 (A-lite 와 무관 · 선택 사항)

`docs/submission.md:189` 가 *"단위 테스트 **20종**"* 이라고 적고 있는데 실제는 22종
(`FEATURES.md`·CLAUDE.md 기준)이고 A-lite 후 23종이 된다. **기존 드리프트**이며 이번 작업이
만든 것이 아니다. 고칠 거면 D5 와 같은 커밋에서 한 글자.

---

## 8. 미결 4건 — 결론

### 8-1. 경계값 — 에이전트 잔액이 결제액보다 조금 모자랄 때

> **결정: "출처는 건당 하나 · 에이전트 잔액이 결제액 이상이면 에이전트, 아니면 전액을 위임 계정에서."
> 보류하지 않고, 임계값도 두지 않는다.** (C4 에서 구현. A-lite 는 이 규칙을 문서로만 확정한다.)

**근거**
1. **분할이 불가능하다**(RFC §3 각주와 동일) — `verify_payment` 가 첫 `TransferChecked` 의 금액이
   **정확히** 일치해야 통과시킨다(`x402_solana.py:218-220` `[코드]`). 두 출처로 쪼개면 판매자측에서
   떨어진다. 따라서 선택지는 사실상 "전액 위임" vs "보류" 둘뿐이다.
2. **보류는 제품을 멈춘다.** 추세추종은 올인/올아웃이라 잔액이 딱 떨어지는 경우가 드물다.
   경계값마다 매수를 거르면 데모가 조용히 거래 없는 화면이 된다 — RFC §3-1 이 지적한 것과 같은 실패.
3. **미리 충전(임계값)은 자금 이동 tx 를 늘리고 새 실패 지점을 만든다.** 마감 안에서 살 수 없다.
4. **오차 방향이 안전하다.** 전액 위임을 택하면 위임이 실제 순투입보다 **조금 빨리** 닳는다
   (§2-4 부등식). 안전 기능은 늦게 걸리는 것보다 일찍 걸리는 쪽이 옳다. 남은 에이전트 잔액은
   다음 매도 대금과 합쳐져 그 다음 매수에 쓰이므로 **자기 교정된다.**

**틀렸을 때의 신호**: 세션 중 `delegatedAmount` 가 AP2 `spent` 보다 **눈에 띄게 빨리** 줄어
(경험칙: 매수 건수의 절반 이상에서 위임이 소모되면) 세션이 예산 전에 멈춘다. 그때는
"에이전트 잔액 < 결제액이면 그 틱은 매수 금액을 잔액에 맞춰 축소" 로 바꾼다(견적을 다시 받는다).

### 8-2. 드라이런 표시 — (a)안을 어디에 어떻게

> **결정: A-lite 에서는 화면에 아무것도 넣지 않는다.** 온체인 예산 UI 가 생기는 순간 배선됐다는
> 인상을 주기 때문이다. (a)안 문구는 **C4 의 첫 화면 항목**으로 아래에 확정해 둔다.

**C4 때의 구체안**(지금 만들지 않음):
- 위치: 예산 카드(`index.html:364` 영역, `data-card="budget"`) **안의 마지막 한 줄**.
  새 카드를 만들지 않는다 — `DEFAULT_LAYOUT`·`LAYOUT_KEY` 를 건드리면 사용자 배치가 초기화된다.
- 훅: `data-budget-onchain` 한 개. 렌더는 `renderState` 에서.
- 문구:
  - 샌드박스 — `온체인 위임: 라이브 세션에서만 사용합니다` (회색, 클릭 불가)
  - 라이브 — `온체인 위임 잔여 12.34 USDC · explorer ↗`
- 표시 전환은 **기존 `body.live-mode` 클래스를 재사용**한다 — 거래 내역 `.col-tx` 열이 이미
  같은 방식으로 라이브에서만 보인다(CLAUDE.md `44dddee`). **새 메커니즘 0개.**

### 8-3. devnet(C9) 진입 조건

> **결정: A-lite 의 devnet 실행은 "있으면 좋은 것"이고, 착수 조건은 딱 하나 —
> `user` 지갑에서 USDC 잔액이 조회로 확인될 때. 파우셋 대기를 일정에 넣지 않는다.**

**순서**
1. Circle 파우셋(`https://faucet.circle.com`, Solana Devnet)에서 **`user` 지갑 주소**로 수령.
   ⚠ 기존 `setup_devnet.py:280` 은 **trading** 지갑을 본다 — A안에서는 자금 소유자가 user 다.
   A-lite 는 `setup_devnet.py` 를 건드리지 않고 데모 스크립트가 직접 확인한다.
2. `python scripts/demo_delegation.py --devnet` 을 **`--budget 2 --spend 1`** 로 실행.
3. SOL: **user 지갑에는 필요 없다** `[실측 P7]`. agent 지갑에 수수료분만 있으면 된다.

**왜 C9 보다 훨씬 싸게 되는가**: 전체 A 배선의 devnet 재실증은 세션 예산(수십 USDC)이 필요하지만,
A-lite 는 **approve 1 · 전송 1 · revoke 1** 이라 **약 2 USDC 면 끝난다.** Circle 파우셋 제약
(주소당 2시간 20 USDC, `setup_devnet.py:310-312` `[코드]`)에 거의 걸리지 않는다.

**실패했을 때의 문안**(localnet 증빙만으로 갈 때):
> "이 증빙은 localnet 입니다. 해커톤 심사 기준이 명시한 평가 네트워크(Devnet·Localnet) 중 하나이고,
> 여기서 쓴 SPL Token 프로그램은 두 네트워크에서 **같은 프로그램 주소**(`TokenkegQ…`)입니다.
> 결제 통화가 Circle 공식 민트가 아닌 점은 devnet 증빙과 동일한 기존 한계이며, 저희가 먼저 밝힙니다."

**틀렸을 때의 신호**: 파우셋이 24시간 안에 안 되면 그대로 localnet 으로 확정한다. 마감 D-3 이후
파우셋을 다시 시도하지 않는다.

### 8-4. A-lite → C4 로 이어질 때 — 남길 확장점과 버릴 것

> **결정: 확장점은 `source_owner` 인자 **하나**뿐. 그 외 hook 을 지금 만들지 않는다.**

| C4 가 필요로 하는 것 | A-lite 에서 | 추가 공수 |
|---|---|---|
| 출처 ATA 분리 | ✅ `build_transfer_transaction(source_owner=)` | 0 |
| 상태 조회·분류 | ✅ `read_delegation` · `classify_rejection` | 0 |
| 교차검증 3지갑 | ✅ C5 | 0 |
| 에이전트로 인자 전달 | ❌ — `TradingAgent.build_payment(source_owner=None)` 통과 인자 | 2줄 |
| 출처 선택 규칙(§8-1) | ❌ — `_buy_cycle` 안 3줄 | 3줄 |
| 거부 예외 처리 | ❌ — `_buy_cycle` 의 `try` 에 `except` 신설(현재 `finally` 만, `engine.py:1241-1299` `[코드]`) | 15줄 |
| `ONCHAIN_BUDGET` 플래그·state 필드·UI | ❌ | C4 |

**B(자체 온체인 프로그램)로 갈 때 버려지는 것**: `payments/delegation.py` 의
`approve_budget`/`revoke_budget`, 그리고 `classify_rejection` 의 0x1/0x4 분기(Anchor 커스텀 에러는
애초에 모호하지 않다). **`source_owner`·C5·§8-1 자금 흐름 규칙은 전부 살아남는다** — RFC §8-1 그대로.

**틀렸을 때의 신호**: C4 착수 시 `_buy_cycle` 수정이 20줄을 넘어가면 확장점 설계가 틀린 것이다.
그때는 출처 선택을 엔진이 아니라 `TradingAgent` 안으로 내린다.

---

## 9. 남은 위험

| # | 위험 | 크기 | 완화 |
|---|---|---|---|
| 1 | **C5 가 유일하게 제품 산출물을 바꾼다**(아카이브 JSON) — 교차검증은 우리 증빙의 무결성 증명이다 | 중 | X-4(user=0 이면 값 동일) + localnet 라이브 세션 1회 PASS 를 DoD 에 박음. 되돌리기는 `git revert` 1회 |
| 2 | `PanicException` 이 `except Exception` 을 통과한다 `[실측 P8]` | 중 | 서명자 목록을 `approve_budget` 안에서 결정(호출자가 못 틀리게). C3 ⑥은 `owner=agent` 로 고정 |
| 3 | 분류 라벨이 사후 조회라 경합에 취약(§3-4 한계) | 낮 | 원값을 아카이브에 남기고, 설명 안 되면 UNCLASSIFIED |
| 4 | 시간 초과 | **낮**(순서 변경 후) | C3 가 셋째로 앞당겨져 **영상 컷·아카이브가 먼저 확보된다.** 못 하는 것은 선택 항목인 C5 다. 그마저 못 하면 C2·C1 만으로도 회귀 0 인 중단 지점이고, 축③ 보강은 `curl` 402 컷만으로 간다(원래 계획) |
| 5 | 심사장에서 "그래서 제품에 붙어 있나요?" | **높음** | 먼저 말한다. `wired_into_product: false` · 터미널 요약 마지막 줄 · D1~D3 문안이 전부 같은 문장을 쓴다 |
| 6 | devnet 파우셋 실패 | 낮 | §8-3 — 애초에 일정에 없다 |

**가장 큰 위험은 5번이고, 기술이 아니라 문장이다.** 이 저장소의 강점은 못 하는 것을 먼저 말해
온 것이고(`README.md:98-110`·`submission.md §2-1`), A-lite 를 과장하면 **그 강점 자체를 잃는다.**
얻는 것과 정확히 같은 크기로만 말한다:

> **"예산 상한을 체인이 집행하는 레일을 실증했습니다. 나머지 7가지 검사는 여전히 서명 직전
> 오프체인이고, 이 레일도 아직 제품 경로에 연결하지 않았습니다."**

---

## 부록 — 이 문서를 만들며 실행한 것

```bash
# localnet 기동
wsl -d Ubuntu --cd /root -- /root/.local/share/solana/install/active_release/bin/solana-test-validator

# 프로브 3종 (스크래치패드 임시 파일 — 저장소 무수정)
#   probe_delegation_api.py   → P1·P2·P3·P4·P5
#   probe_err_struct.py       → P6 (에러 구조 · 0x1/0x4 4케이스)
#   probe_feepayer.py         → P7·P8
```

RFC 의 재현 명령은 그대로 유효하다:

```bash
python scripts/poc_delegate.py
```
