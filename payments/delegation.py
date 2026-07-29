"""온체인 예산 레일 — SPL Token 위임(approve/revoke)의 상태 읽기·설정과 거부 사유 분류.

**이 모듈은 아직 제품 결제 경로에 연결돼 있지 않다.** 저장소 어디에서도 호출하지 않고,
`scripts/demo_delegation.py`(레일 증명 전용)와 단위 테스트만 사용한다. 정확한 문장은
*"예산 상한을 체인이 집행하는 레일을 실증했고, 엔진 배선은 로드맵"* 이다.
설계·근거는 `docs/design/onchain_budget_design.md`.

무엇을 하는가 — SPL Token 의 `approve` 는 "이 토큰 계정에서 저 지갑이 앞으로 꺼낼 수 있는
총액"을 체인에 기록한다. 위임받은 지갑이 단독 서명으로 전송하면 그 잔여(`delegatedAmount`)가
체인에서 자동으로 깎이고, 넘어서면 SPL Token 프로그램이 트랜잭션을 거절한다. 즉 **금액 축
하나만큼은 상한을 오프체인 코드가 아니라 체인이 집행한다.**

체인이 강제하지 **못하는** 것(그래서 402 Guard 가 계속 담당하는 것): 수취인·종목·청구서
의미·건별 한도·배송 확인. 위임은 "누구에게 얼마를 보내는가"를 보지 않고 "이 계정에서
누적 얼마까지 나갈 수 있는가"만 본다.

── ⚠ 실측으로 확인한 함정 3건 (설계 §0-1) ────────────────────────────────────────
1. **한도 초과와 잔액 부족이 같은 에러 코드(0x1)** 다. 코드만 보고 "체인이 한도를 집행했다"고
   표시하면 지갑이 빈 것을 한도라고 광고하게 된다 → `classify_rejection` 이 실패 직후 계정
   상태를 함께 받아 갈라낸다. 잔액이 충분했을 때만 한도를 주장한다.
2. **`approve(0)` 은 위임 해제가 아니다.** `delegate` 는 남고 잔여만 0 이 되며, 이 상태의
   전송 거부는 0x4 가 아니라 0x1 이다. 해제(0x4)와 다른 상태로 다룬다.
3. **서명자가 모자란 트랜잭션을 만들면 `pyo3_runtime.PanicException`** 이 난다. `BaseException`
   직계라 `except Exception` 에 잡히지 않는다 → 서명자 목록은 호출자가 아니라 `_send_owner_ix`
   가 결정한다.

실패 정책은 저장소 관례를 따른다 — **모르는 것을 0 으로 바꾸지 않는다.** `read_delegation` 의
RPC 실패는 삼키지 않고 전파한다(`x402_solana.get_token_balance_base` 가 BUG-01 로 닫은 것과
같은 결함을 만들지 않기 위해서다).
"""
from __future__ import annotations
import re
import sys
from dataclasses import dataclass
from typing import Optional

from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import (
    approve_checked, revoke, get_associated_token_address,
)
from spl.token.models import ApproveCheckedParams, RevokeParams

from config import from_base_units
from payments import x402_solana as x
from payments.guard import GuardResult

# ---- 온체인 거부 분류 코드 (4종) ----
# guard.py 의 GUARD_* 명명 규칙을 따라 로그·배지가 같은 계열로 보이게 하되, guard.py 의
# DEMAND_CODES(하드 검사 8종)에는 넣지 않는다. 8종은 '서명을 거부시키는 검사'를 세는 값이고,
# 온체인 거부는 서명한 뒤 체인이 거절하는 것이라 정의가 다르다(설계 §3-5). 8종은 8종 그대로다.
GUARD_ONCHAIN_BUDGET = "GUARD_ONCHAIN_BUDGET"            # 위임 잔여 < 요청 (잔액은 충분)
GUARD_ONCHAIN_FUNDS = "GUARD_ONCHAIN_FUNDS"              # 잔액 < 요청 — 한도 집행이 아니다
GUARD_ONCHAIN_NO_DELEGATE = "GUARD_ONCHAIN_NO_DELEGATE"  # 위임 소진·회수·미등록
GUARD_ONCHAIN_UNCLASSIFIED = "GUARD_ONCHAIN_UNCLASSIFIED"  # 설명되지 않음 — 어떤 주장도 하지 않는다

# SPL Token 프로그램 커스텀 에러 (실측)
_SPL_INSUFFICIENT = 1   # 0x1 — 한도 초과 / 잔액 부족 / 위임 잔여 0. 셋이 같은 코드다.
_SPL_OWNER_MISMATCH = 4  # 0x4 — 위임 해제·미등록·타인 위임

_CUSTOM_ERR_RE = re.compile(r"custom program error:?\s*0x([0-9a-fA-F]+)")


class DelegationError(Exception):
    """위임 조작이 성립하지 않는다(사전 조건 위반·전송 실패). 결제 거부와는 다른 층위다."""


@dataclass(frozen=True)
class DelegationState:
    """SPL 토큰 계정 1개의 위임 상태 스냅샷. 금액은 전부 base units 정수."""

    exists: bool                # ATA 자체가 존재하는가
    delegate: Optional[str]     # 위임 대상 pubkey 문자열. 없으면 None
    delegated_amount: int       # 남은 위임 한도. delegate 가 None 이면 0
    balance: int                # 계정 잔액

    def is_delegated_to(self, pubkey) -> bool:
        return self.delegate is not None and self.delegate == str(pubkey)


# ---------- 관측 ----------

async def read_delegation(client, owner: Pubkey, mint: Pubkey) -> DelegationState:
    """소유자 ATA 의 잔액과 위임 상태를 **RPC 1회**로 읽는다.

    `get_account_info_json_parsed` 한 번이면 `tokenAmount.amount`(잔액)와
    `delegatedAmount.amount`(위임 잔여)를 둘 다 얻는다. 기존 경로
    (`get_token_account_balance`)는 잔액만 주고 위임 정보를 안 주므로 두 번 부를 이유가 없다.

    ATA 미존재는 예외가 아니라 `resp.value is None` 으로 온다(실측 P1) → exists=False.
    RPC 실패는 **삼키지 않고 전파한다** — 불명 실패를 0 으로 바꾸면 위임 잔여가 0 으로 보여
    "체인이 한도를 집행했다"는 거짓 판정을 만든다.
    """
    ata = get_associated_token_address(owner, mint)
    resp = await client.get_account_info_json_parsed(ata)
    if resp.value is None:
        return DelegationState(False, None, 0, 0)
    try:
        info = resp.value.data.parsed["info"]
        balance = int(info["tokenAmount"]["amount"])
        delegate = info.get("delegate")
        delegated = int((info.get("delegatedAmount") or {}).get("amount") or 0)
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        raise DelegationError(
            f"토큰 계정 형식이 아닙니다 ({ata}) — {type(e).__name__}: {e}") from e
    if delegate is None:
        delegated = 0
    return DelegationState(True, str(delegate) if delegate is not None else None,
                           delegated, balance)


# ---------- 설정 (소유자 서명 필요) ----------

async def _send_owner_ix(client, ix, owner_kp: Keypair,
                         fee_payer: Optional[Keypair]) -> tuple[str, bool]:
    """소유자 서명이 필요한 명령 1개를 전송·확정한다. **서명자 목록을 여기서 결정한다.**

    호출자에게 맡기지 않는 이유: 서명자가 모자란 트랜잭션을 만들면 solders 가
    `pyo3_runtime.PanicException` 을 내는데 `BaseException` 직계라 `except Exception` 으로
    잡히지 않아 프로세스가 그대로 죽는다(설계 §0-1 P8).

    fee_payer 를 주면 수수료를 그 지갑이 대납한다 — 자금 소유자 지갑에 SOL 이 없어도
    위임을 걸고 회수할 수 있다(실측 P7).
    """
    payer_kp = fee_payer or owner_kp
    signers = ([payer_kp] if payer_kp.pubkey() == owner_kp.pubkey()
               else [payer_kp, owner_kp])
    bh = await x.get_latest_blockhash(client)
    msg = Message.new_with_blockhash([ix], payer_kp.pubkey(), bh)
    tx = Transaction(signers, msg, bh)
    return await x.submit_and_confirm(client, tx)


async def approve_budget(client, owner_kp: Keypair, delegate: Pubkey, mint: Pubkey,
                         amount_base: int, decimals: int, *,
                         fee_payer: Optional[Keypair] = None,
                         allow_decrease: bool = False) -> tuple[str, DelegationState]:
    """소유자가 `delegate` 에게 이 민트의 지출 상한을 건다. **의미는 절대값 설정이다.**

    amount_base = "앞으로 delegate 가 이 계정에서 꺼낼 수 있는 총액". SPL `approve` 가 누적이
    아니라 덮어쓰기이기 때문이다 — 잔여 15 인 상태에서 "10 만큼 더 주자"고 approve(10) 을
    부르면 결과는 25 가 아니라 **10** 이고 한도가 줄어든다. 그래서 이 API 에는 '재충전'이라는
    말을 쓰지 않고, `allow_decrease=False`(기본)에서 현재 잔여보다 작은 값이 오면 **트랜잭션을
    보내기 전에** DelegationError 로 시끄럽게 실패한다.

    정말 더하고 싶으면 호출자가 읽고 더한다:
        st = await read_delegation(...); await approve_budget(..., st.delegated_amount + delta)
    읽기-수정-쓰기 사이의 경쟁은 숨기지 않는다 — 그 사이에 delegate 가 썼다면 총액이 의도보다
    커진다(안전하지 않은 방향). 그래서 권장 사용법은 언제나 절대값 지정이다.

    ATA 는 만들지 않는다. 자금 소유자 지갑에 잔액이 있다면 ATA 는 이미 있다.
    반환: (tx 서명, 전송 후 재조회한 DelegationState).
    """
    if amount_base < 0:
        raise ValueError(f"amount_base 는 0 이상이어야 합니다: {amount_base}")

    before = await read_delegation(client, owner_kp.pubkey(), mint)
    if not before.exists:
        raise DelegationError(
            f"소유자 토큰 계정이 없습니다 ({owner_kp.pubkey()} / {mint}) — "
            f"위임을 걸 계정이 없습니다.")
    if (not allow_decrease and before.is_delegated_to(delegate)
            and before.delegated_amount > amount_base):
        raise DelegationError(
            f"현재 위임 잔여 {from_base_units(before.delegated_amount, decimals)} 보다 작은 "
            f"{from_base_units(amount_base, decimals)} 로 덮어쓰려 합니다 — SPL approve 는 "
            f"누적이 아니라 절대값 덮어쓰기라 한도가 줄어듭니다. 의도한 감액이면 "
            f"allow_decrease=True 를 명시하세요.")

    ata = get_associated_token_address(owner_kp.pubkey(), mint)
    ix = approve_checked(ApproveCheckedParams(
        program_id=TOKEN_PROGRAM_ID, source=ata, mint=mint, delegate=delegate,
        owner=owner_kp.pubkey(), amount=amount_base, decimals=decimals, signers=[]))
    sig, ok = await _send_owner_ix(client, ix, owner_kp, fee_payer)
    if not ok:
        raise DelegationError(f"approve 트랜잭션이 확정에 실패했습니다 (tx {sig})")
    after = await read_delegation(client, owner_kp.pubkey(), mint)
    return sig, after


async def revoke_budget(client, owner_kp: Keypair, mint: Pubkey, *,
                        fee_payer: Optional[Keypair] = None) -> str:
    """소유자가 위임을 회수한다. 이후 위임받았던 지갑의 전송은 체인이 0x4 로 거절한다.

    **멱등이다** — 위임이 걸려 있지 않은 계정에 보내도 체인이 성공을 준다(실측 P3). 그래서
    사전 조회를 하지 않는다(RPC 1회 절약). 보낼 토큰 계정 자체가 없으면 전송이 실패하므로
    DelegationError 로 바꿔 던진다.
    """
    ata = get_associated_token_address(owner_kp.pubkey(), mint)
    ix = revoke(RevokeParams(
        program_id=TOKEN_PROGRAM_ID, account=ata, owner=owner_kp.pubkey(), signers=[]))
    try:
        sig, ok = await _send_owner_ix(client, ix, owner_kp, fee_payer)
    except Exception as e:
        raise DelegationError(
            f"위임 회수 전송 실패 (토큰 계정 {ata} 가 없거나 RPC 오류) — "
            f"{type(e).__name__}: {e}") from e
    if not ok:
        raise DelegationError(f"revoke 트랜잭션이 확정에 실패했습니다 (tx {sig})")
    return sig


# ---------- 거부 사유 분류 ----------

def spl_error_code(exc: BaseException) -> Optional[int]:
    """전송 거부 예외에서 SPL 커스텀 에러 코드를 꺼낸다. 못 꺼내면 None.

    1순위는 속성 경로 `exc.args[0].data.err.err.code`(실측 P6) 다. 문자열 파싱은 로케일·메시지
    변경에 약해서 1순위로 두지 않는다. 다만 solders 버전이 바뀌어 속성 경로가 깨졌을 때
    분류가 조용히 UNCLASSIFIED 로 무너지는 것보다는 문자열로라도 맞히는 편이 나으므로
    폴백 1단을 둔다.
    """
    try:
        code = exc.args[0].data.err.err.code
        if isinstance(code, int):
            return int(code)
    except Exception:
        pass
    m = _CUSTOM_ERR_RE.search(str(exc))
    if m:
        return int(m.group(1), 16)
    return None


def _reject(code: str, detail: str, expected: str = "", actual: str = "") -> GuardResult:
    """판정 결과 생성 — 판정이 난 소스 라인을 런타임에 캡처한다(guard.py 와 같은 방식)."""
    where = f"delegation.py:L{sys._getframe(1).f_lineno}"
    return GuardResult(False, code, detail, where, str(expected), str(actual))


def classify_rejection(code: Optional[int], state: DelegationState,
                       requested_base: int, decimals: int, delegate) -> GuardResult:
    """체인이 결제를 거절한 이유를 라벨링한다. **순수 함수 · 네트워크 없음.**

    상태를 주입받는다 — `guard.check_delivery` 가 `balance_reader` 를 주입받는 것과 같은
    관례이고, 덕분에 분류 로직 전체가 오프라인 테스트 대상이 된다. 호출자는 실패 직후
    `read_delegation` 을 1회 더 불러 그 결과를 여기 넘긴다. 그 조회마저 실패하면 호출자가
    GUARD_ONCHAIN_UNCLASSIFIED 로 처리한다(이 함수는 상태를 만들지 않는다).

    ★ 잔액 부족을 한도 집행으로 오표시하지 않는 것이 이 함수의 존재 이유다. 0x1 하나에
    '한도 초과'·'잔액 부족'·'위임 잔여 0' 셋이 들어오므로, **잔액이 충분했을 때만**
    GUARD_ONCHAIN_BUDGET 을 주장한다. 둘 다 부족하면 보수적으로 FUNDS 로 떨어진다.

    한계(정직하게 적어 둔다): 상태를 실패 **이후에** 읽으므로 그 사이에 소유자가 돈을 쓰거나
    재승인하면 판정이 어긋날 수 있다. 라벨은 최선 추정이고, 근거 원값은 호출자가 함께 남긴다.
    """
    want = from_base_units(requested_base, decimals)

    # 위임이 아예 없거나 남에게 걸려 있으면, 코드가 무엇이든 그것이 원인이다.
    if code == _SPL_OWNER_MISMATCH or not state.is_delegated_to(delegate):
        return _reject(GUARD_ONCHAIN_NO_DELEGATE,
                       "이 지갑에 위임이 걸려 있지 않습니다 (소진·회수·미등록)",
                       f"delegate={delegate}", f"delegate={state.delegate or '없음'}")

    if code == _SPL_INSUFFICIENT:
        left = from_base_units(state.delegated_amount, decimals)
        have = from_base_units(state.balance, decimals)
        if state.balance >= requested_base and state.delegated_amount < requested_base:
            # ★ '체인이 한도를 집행했다' 를 말해도 되는 유일한 분기 — 잔액은 충분했다.
            return _reject(GUARD_ONCHAIN_BUDGET,
                           f"위임 잔여 {left} < 요청 {want} — 체인이 예산 상한에서 거절했습니다",
                           f"위임 잔여 {left}", f"요청 {want}")
        if state.balance < requested_base:
            return _reject(GUARD_ONCHAIN_FUNDS,
                           f"잔액 {have} < 요청 {want} — 한도 집행이 아니라 잔액 부족입니다",
                           f"잔액 {have}", f"요청 {want}")

    return _reject(GUARD_ONCHAIN_UNCLASSIFIED,
                   "체인이 거절했으나 계정 상태로 설명되지 않습니다 — 원인을 주장하지 않습니다",
                   "", f"code={code} delegated={state.delegated_amount} "
                       f"balance={state.balance} requested={requested_base}")
