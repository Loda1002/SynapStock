"""온체인 예산 레일(payments/delegation.py) 단위 테스트.

설계: docs/design/onchain_budget_design.md §4. 이 모듈은 제품 결제 경로에 연결돼 있지 않다 —
검증 대상은 "레일이 실제로 성립하는가"와 "거부 사유를 정직하게 갈라내는가" 둘이다.

★ 이 파일이 지키는 핵심 명제
  체인은 '한도 초과'와 '잔액 부족'을 **같은 에러 코드(0x1)** 로 거절한다. 코드만 보고 라벨을
  붙이면 지갑이 빈 것을 "체인이 한도를 집행했다"고 광고하게 된다. 그래서 실패 직후 계정
  상태를 한 번 더 읽어 갈라내고, **잔액이 충분했을 때만** 한도를 주장한다.

  그 명제가 진짜로 지켜지는지 증명하기 위해 **음성 대조(N1)** 를 같은 파일에 둔다 —
  "에러 코드만 보는 순진한 분류기"를 나란히 돌려, 그것이 실제로 거짓 광고를 하고 우리
  분류기는 하지 않는다는 것을 단언한다. 이 대조가 없으면 테스트는 자기 자신을 확인할 뿐이다.

섹션
  1) classify_rejection            — 오프라인 (네트워크 0)
  2) DelegationState·spl_error_code — 오프라인
  3) localnet 왕복                  — `--localnet` 일 때만

실행:
  python scripts/test_delegation.py              # 오프라인 섹션만 (검증기 불필요)
  python scripts/test_delegation.py --localnet   # + localnet 왕복
      wsl -d Ubuntu --cd /root -- /root/.local/share/solana/install/active_release/bin/solana-test-validator
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from decimal import Decimal

sys.stdout.reconfigure(errors="replace")
sys.stderr.reconfigure(errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from solders.keypair import Keypair  # noqa: E402
from solders.message import Message  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402
from solders.system_program import create_account, CreateAccountParams  # noqa: E402
from solders.transaction import Transaction  # noqa: E402

from spl.token._layouts import MINT_LAYOUT  # noqa: E402
from spl.token.constants import TOKEN_PROGRAM_ID  # noqa: E402
from spl.token.instructions import (  # noqa: E402
    create_idempotent_associated_token_account, get_associated_token_address,
    initialize_mint2, mint_to, transfer_checked,
)
from spl.token.models import (  # noqa: E402
    InitializeMint2Params, MintToParams, TransferCheckedParams,
)

from payments import x402_solana as x  # noqa: E402
from payments.delegation import (  # noqa: E402
    GUARD_ONCHAIN_BUDGET, GUARD_ONCHAIN_FUNDS, GUARD_ONCHAIN_NO_DELEGATE,
    GUARD_ONCHAIN_UNCLASSIFIED, DelegationError, DelegationState,
    approve_budget, classify_rejection, read_delegation, revoke_budget, spl_error_code,
)
from payments.guard import GuardResult  # noqa: E402

DECIMALS = 6
RPC = "http://127.0.0.1:8899"
PASS, FAIL = "통과", "실패"

_results: list[tuple[str, bool, str]] = []
_skipped: list[str] = []

AGENT = "AgentAgentAgentAgentAgentAgentAgentAgentAg1"     # 위임 대상 자리표시 (문자열 비교만 한다)
OTHER = "OtherOtherOtherOtherOtherOtherOtherOther99"      # 제3자


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def skip(name: str, reason: str) -> None:
    _skipped.append(name)
    print(f"  [건너뜀] {name} — {reason}")


def base(ui: str) -> int:
    return int(Decimal(ui) * (10 ** DECIMALS))


def st(balance_ui: str, delegated_ui: str, delegate: str | None = AGENT) -> DelegationState:
    """UI 단위로 읽히는 DelegationState 생성 헬퍼."""
    return DelegationState(True, delegate, base(delegated_ui), base(balance_ui))


# ═══════════════════════════════════════════════════════════════════════════════
# 음성 대조 N1 — "에러 코드만 보는 순진한 분류기"
# ═══════════════════════════════════════════════════════════════════════════════

def _naive_classify(code: int | None) -> str:
    """설계 §3-4 방어 4번. 계정 상태를 안 읽고 0x1 을 무조건 '한도 거부'로 부르는 구현이다.

    이것이 정확히 우리가 만들지 않기로 한 분류기다 — 잔액이 빈 것도 '체인이 한도를 집행했다'
    로 광고한다. 아래 테스트가 이 순진한 분류기와 우리 분류기를 같은 입력에 나란히 돌려
    **결과가 달라야만** 통과한다. 우리 코드가 상태 조회를 잃어버리면 둘이 같아져 실패한다.
    """
    return GUARD_ONCHAIN_BUDGET if code == 1 else GUARD_ONCHAIN_UNCLASSIFIED


# ═══════════════════════════════════════════════════════════════════════════════
# 섹션 1 — classify_rejection (오프라인)
# ═══════════════════════════════════════════════════════════════════════════════

def test_classify() -> None:
    print("\n[1] classify_rejection — 같은 0x1 에서 원인을 갈라낸다 (네트워크 0)")

    # C-1: 한도만 모자란다. 잔액은 충분 → 이것만 '체인이 한도를 집행했다' 를 말해도 된다.
    r1 = classify_rejection(1, st("90", "15"), base("20"), DECIMALS, AGENT)
    check("C-1 한도 초과(잔액 충분) → BUDGET", r1.code == GUARD_ONCHAIN_BUDGET, r1.code)

    # C-2: 위임은 넉넉한데 지갑이 비었다 → 한도 집행이 아니다.
    r2 = classify_rejection(1, st("90", "500"), base("200"), DECIMALS, AGENT)
    check("C-2 잔액 부족(한도 충분) → FUNDS", r2.code == GUARD_ONCHAIN_FUNDS, r2.code)

    # C-3 ★ 둘 다 부족 — 보수적으로 FUNDS. 한도를 주장하지 않는다.
    r3 = classify_rejection(1, st("5", "5"), base("20"), DECIMALS, AGENT)
    check("C-3 ★ 둘 다 부족 → FUNDS (한도 주장 금지)",
          r3.code == GUARD_ONCHAIN_FUNDS, r3.code)

    # C-4: approve(0) — delegate 는 남아 있고 잔여만 0. 해제(0x4)와 다른 상태다.
    r4 = classify_rejection(1, st("90", "0"), base("20"), DECIMALS, AGENT)
    check("C-4 approve(0) 상태 → BUDGET", r4.code == GUARD_ONCHAIN_BUDGET, r4.code)

    r5 = classify_rejection(4, st("90", "0", delegate=None), base("20"), DECIMALS, AGENT)
    check("C-5 0x4 · delegate 없음 → NO_DELEGATE", r5.code == GUARD_ONCHAIN_NO_DELEGATE, r5.code)

    r6 = classify_rejection(4, st("90", "50", delegate=OTHER), base("20"), DECIMALS, AGENT)
    check("C-6 0x4 · 제3자 위임 → NO_DELEGATE", r6.code == GUARD_ONCHAIN_NO_DELEGATE, r6.code)

    # C-7: 코드는 1인데 위임이 남에게 걸려 있다 → 위임 확인이 우선한다.
    r7 = classify_rejection(1, st("90", "50", delegate=OTHER), base("20"), DECIMALS, AGENT)
    check("C-7 0x1 · 제3자 위임 → NO_DELEGATE(위임 확인 우선)",
          r7.code == GUARD_ONCHAIN_NO_DELEGATE, r7.code)

    # C-8: 둘 다 충분한데 실패했다 = 설명되지 않는다. 억지 분류하지 않는다.
    r8 = classify_rejection(1, st("90", "50"), base("20"), DECIMALS, AGENT)
    check("C-8 둘 다 충분한데 실패 → UNCLASSIFIED",
          r8.code == GUARD_ONCHAIN_UNCLASSIFIED, r8.code)

    r9 = classify_rejection(None, st("90", "50"), base("20"), DECIMALS, AGENT)
    check("C-9 코드 추출 실패 → UNCLASSIFIED", r9.code == GUARD_ONCHAIN_UNCLASSIFIED, r9.code)

    # C-10: 잔여 == 요청인데 실패 = 한도 때문이 아니다(한도는 딱 맞으면 통과한다).
    r10 = classify_rejection(1, st("90", "20"), base("20"), DECIMALS, AGENT)
    check("C-10 잔여 == 요청인데 실패 → UNCLASSIFIED",
          r10.code == GUARD_ONCHAIN_UNCLASSIFIED, r10.code)

    # C-11: 반환 타입·where 관례
    check("C-11 반환이 GuardResult 이고 ok=False", isinstance(r1, GuardResult) and r1.ok is False)
    check("C-11 where 가 delegation.py:L 로 시작", r1.where.startswith("delegation.py:L"), r1.where)

    # C-12: 이벤트 스키마가 guard 와 동일 → 프론트 렌더 코드 변경 0
    keys = set(r1.as_event().keys())
    check("C-12 as_event 키 6개가 guard.GuardResult 와 동일",
          keys == {"ok", "code", "detail", "where", "expected", "actual"}, ",".join(sorted(keys)))

    # C-13: 화면에 그대로 뜨는 값이 UI 단위인가 (base units 가 새어 나오면 안 된다)
    check("C-13 BUDGET 의 expected/actual 이 UI 단위",
          "15" in r1.expected and "20" in r1.actual and "15000000" not in r1.expected,
          f"{r1.expected} / {r1.actual}")

    # ---- 음성 대조 N1 ----
    print("\n  · 음성 대조 N1 — 코드만 보는 순진한 분류기와 나란히")
    for label, res, state, want in (
        ("C-2", r2, st("90", "500"), base("200")),
        ("C-3", r3, st("5", "5"), base("20")),
    ):
        naive = _naive_classify(1)
        check(f"N1 {label}: 순진한 분류기는 BUDGET 이라 거짓 광고한다",
              naive == GUARD_ONCHAIN_BUDGET, naive)
        check(f"N1 {label}: 우리 분류기는 다른 판정을 낸다",
              res.code != naive, f"우리={res.code} vs 순진={naive}")


# ═══════════════════════════════════════════════════════════════════════════════
# 섹션 2 — DelegationState · spl_error_code (오프라인)
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeErr(Exception):
    """속성 경로가 없는 예외 — solders 버전이 바뀌어 경로가 깨진 상황을 흉내낸다."""


async def test_state_and_code() -> None:
    print("\n[2] DelegationState · spl_error_code (네트워크 0)")

    # S-1
    check("S-1 delegate 없음 → is_delegated_to False",
          st("1", "1", delegate=None).is_delegated_to(AGENT) is False)
    check("S-1 일치 → True", st("1", "1").is_delegated_to(AGENT) is True)
    check("S-1 불일치 → False", st("1", "1", delegate=OTHER).is_delegated_to(AGENT) is False)

    # S-2: 1순위 속성 경로가 없으면 문자열 폴백으로라도 맞힌다(조용한 UNCLASSIFIED 방지)
    e = _FakeErr("Transaction simulation failed: Error processing Instruction 0: "
                 "custom program error: 0x4")
    check("S-2 속성 경로 없음 → 문자열 폴백으로 4 추출", spl_error_code(e) == 4, str(spl_error_code(e)))

    # S-3: 아무 신호도 없으면 None. 0 으로 넘기지 않는다(0 은 유효한 코드다).
    check("S-3 무관한 예외 → None", spl_error_code(ValueError("아무것도 아님")) is None)

    # S-4 ★ BUG-01 계열 — 조회 실패를 0 으로 바꾸면 '위임 잔여 0' 이라는 거짓 판정이 된다
    client = await x.get_client("http://127.0.0.1:1")   # 닫힌 포트
    raised = ""
    try:
        await read_delegation(client, Keypair().pubkey(), Keypair().pubkey())
    except BaseException as ex:                          # noqa: BLE001 — 타입 무관, 전파만 확인
        raised = type(ex).__name__
    finally:
        await client.close()
    check("S-4 ★ RPC 실패는 예외 전파 (0 반환 아님)", bool(raised), raised or "예외 없이 반환됨")


# ═══════════════════════════════════════════════════════════════════════════════
# 섹션 3 — localnet 왕복 (--localnet)
# ═══════════════════════════════════════════════════════════════════════════════

async def _send(client, payer: Keypair, ixs, signers) -> str:
    bh = await x.get_latest_blockhash(client)
    msg = Message.new_with_blockhash(ixs, payer.pubkey(), bh)
    sig, ok = await x.submit_and_confirm(client, Transaction(signers, msg, bh))
    if not ok:
        raise RuntimeError(f"트랜잭션 실패: {sig}")
    return sig


async def _airdrop(client, pk: Pubkey, sol: float) -> None:
    from solana.rpc.commitment import Confirmed
    r = await client.request_airdrop(pk, int(sol * 1_000_000_000))
    await client.confirm_transaction(r.value, commitment=Confirmed)


async def _delegate_transfer(client, agent: Keypair, owner: Pubkey, dest_owner: Pubkey,
                             mint: Pubkey, amount: int):
    """위임받은 agent 가 단독 서명으로 owner 자금을 전송한다. (성공여부, 예외)

    C1 의 build_transfer_transaction(source_owner=) 이전이라 여기서는 명령을 직접 만든다 —
    이 테스트가 검증하는 것은 우리 트랜잭션 빌더가 아니라 **체인의 거부 코드**다.

    ⚠ 수취 ATA 생성 명령을 여기 붙이면 안 된다. 첫 전송이 한도 초과로 거절되면 같은
    트랜잭션 안의 ATA 생성도 함께 롤백돼, 다음 전송이 '한도'가 아니라 '수취 계정 없음'으로
    실패한다(코드 None). 수취 ATA 는 준비 단계에서 미리 만든다.
    """
    ix = transfer_checked(TransferCheckedParams(
        program_id=TOKEN_PROGRAM_ID,
        source=get_associated_token_address(owner, mint), mint=mint,
        dest=get_associated_token_address(dest_owner, mint),
        owner=agent.pubkey(),          # authority 자리에 delegate 를 넣는다 (계정 순서 동일)
        amount=amount, decimals=DECIMALS, signers=[]))
    try:
        return True, await _send(client, agent, [ix], [agent])
    except BaseException as e:                            # noqa: BLE001
        return False, e


async def test_localnet() -> None:
    print(f"\n[3] localnet 왕복 ({RPC})")
    client = await x.get_client(RPC)
    try:
        funder, agent, broker = Keypair(), Keypair(), Keypair()
        user, user_nosol = Keypair(), Keypair()   # user_nosol 은 끝까지 SOL 을 받지 않는다
        await _airdrop(client, funder.pubkey(), 5)
        await _airdrop(client, user.pubkey(), 2)
        await _airdrop(client, agent.pubkey(), 2)

        # 테스트 민트 + 두 사용자 ATA (rent 는 funder 부담)
        mint_kp = Keypair()
        rent = (await client.get_minimum_balance_for_rent_exemption(MINT_LAYOUT.sizeof())).value
        await _send(client, funder, [
            create_account(CreateAccountParams(
                from_pubkey=funder.pubkey(), to_pubkey=mint_kp.pubkey(), lamports=rent,
                space=MINT_LAYOUT.sizeof(), owner=TOKEN_PROGRAM_ID)),
            initialize_mint2(InitializeMint2Params(
                program_id=TOKEN_PROGRAM_ID, mint=mint_kp.pubkey(), decimals=DECIMALS,
                mint_authority=funder.pubkey(), freeze_authority=None)),
        ], [funder, mint_kp])
        m = mint_kp.pubkey()
        for owner, amount in ((user.pubkey(), "100"), (user_nosol.pubkey(), "50")):
            await _send(client, funder, [
                create_idempotent_associated_token_account(
                    payer=funder.pubkey(), owner=owner, mint=m),
                mint_to(MintToParams(program_id=TOKEN_PROGRAM_ID, mint=m,
                                     dest=get_associated_token_address(owner, m),
                                     mint_authority=funder.pubkey(),
                                     amount=base(amount), signers=[])),
            ], [funder])
        # 수취인 ATA 도 미리 만든다 — 거절되는 전송과 같은 트랜잭션에 두면 함께 롤백된다.
        await _send(client, funder, [create_idempotent_associated_token_account(
            payer=funder.pubkey(), owner=broker.pubkey(), mint=m)], [funder])

        # L-6: ATA 가 없는 소유자 — 예외가 아니라 exists=False 로 온다
        empty = await read_delegation(client, Keypair().pubkey(), m)
        check("L-6 ATA 없는 소유자 → exists=False, 예외 없음",
              empty.exists is False and empty.delegated_amount == 0 and empty.balance == 0,
              str(empty))

        # L-1: 예산 25 위임
        sig, s1 = await approve_budget(client, user, agent.pubkey(), m, base("25"), DECIMALS)
        check("L-1 approve_budget(25) → delegated 25", s1.delegated_amount == base("25"),
              str(s1.delegated_amount))
        check("L-1 delegate == agent", s1.is_delegated_to(agent.pubkey()), str(s1.delegate))

        # L-2 ★ 하강 금지 — 트랜잭션을 보내기 전에 막고, 체인 상태가 그대로여야 한다
        msg = ""
        try:
            await approve_budget(client, user, agent.pubkey(), m, base("10"), DECIMALS)
        except DelegationError as e:
            msg = str(e)
        after = await read_delegation(client, user.pubkey(), m)
        check("L-2 ★ 감액 시도가 DelegationError", "allow_decrease" in msg, msg[:80] or "예외 없음")
        check("L-2 ★ 체인 상태는 25 그대로", after.delegated_amount == base("25"),
              str(after.delegated_amount))

        # L-8a: 실제 한도 초과 거부 — 잔액(100)은 충분한데 한도(25)를 넘는 30
        ok, err = await _delegate_transfer(client, agent, user.pubkey(), broker.pubkey(),
                                           m, base("30"))
        check("L-8 한도 초과 전송이 거절됨", not ok)
        code_over = spl_error_code(err) if not ok else None
        check("L-8 한도 초과의 에러 코드 = 1", code_over == 1, f"code={code_over}")
        # 그 실패를 우리 분류기에 그대로 태운다 (실제 체인 응답 + 실제 계정 상태)
        state_now = await read_delegation(client, user.pubkey(), m)
        verdict = classify_rejection(code_over, state_now, base("30"), DECIMALS, agent.pubkey())
        check("L-8 실 거부를 분류하면 BUDGET", verdict.code == GUARD_ONCHAIN_BUDGET,
              f"{verdict.code} — {verdict.detail}")

        # L-3: 명시적 감액
        _, s3 = await approve_budget(client, user, agent.pubkey(), m, base("10"), DECIMALS,
                                     allow_decrease=True)
        check("L-3 allow_decrease=True → 10 으로 내려감", s3.delegated_amount == base("10"),
              str(s3.delegated_amount))

        # L-8b: 잔여를 정확히 소진하면 위임이 자동 해제되고, 이후 거부 코드가 4 로 바뀐다
        ok, _ = await _delegate_transfer(client, agent, user.pubkey(), broker.pubkey(),
                                         m, base("10"))
        check("L-8 잔여 전액 전송 성공", ok)
        exhausted = await read_delegation(client, user.pubkey(), m)
        check("L-8 소진되면 delegate 가 해제된다", exhausted.delegate is None, str(exhausted))
        ok, err2 = await _delegate_transfer(client, agent, user.pubkey(), broker.pubkey(),
                                            m, base("1"))
        code_gone = spl_error_code(err2) if not ok else None
        check("L-8 소진 후 에러 코드 = 4", code_gone == 4, f"code={code_gone}")
        check("L-8 소진 후 분류 = NO_DELEGATE",
              classify_rejection(code_gone, exhausted, base("1"), DECIMALS,
                                 agent.pubkey()).code == GUARD_ONCHAIN_NO_DELEGATE)

        # L-4 / L-5: 회수와 멱등성
        await approve_budget(client, user, agent.pubkey(), m, base("5"), DECIMALS)
        await revoke_budget(client, user, m)
        revoked = await read_delegation(client, user.pubkey(), m)
        check("L-4 revoke 후 delegate = None", revoked.delegate is None, str(revoked))
        again = ""
        try:
            await revoke_budget(client, user, m)
        except BaseException as e:                        # noqa: BLE001
            again = f"{type(e).__name__}: {e}"
        check("L-5 revoke 재호출이 오류 없음(멱등)", not again, again)

        # L-7 ★ 수수료 대납 — 자금 소유자 지갑에 SOL 이 한 번도 필요하지 않다
        sol_before = await x.get_sol_balance(client, user_nosol.pubkey())
        _, s7 = await approve_budget(client, user_nosol, agent.pubkey(), m, base("7"),
                                     DECIMALS, fee_payer=agent)
        sol_after = await x.get_sol_balance(client, user_nosol.pubkey())
        check("L-7 ★ agent 대납으로 approve 성공", s7.delegated_amount == base("7"),
              str(s7.delegated_amount))
        check("L-7 ★ 자금 소유자 SOL 불변(0)", sol_before == 0 and sol_after == 0,
              f"{sol_before} → {sol_after}")
    finally:
        await client.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 섹션 4 — build_transfer_transaction(source_owner=) (C1 · 오프라인)
# ═══════════════════════════════════════════════════════════════════════════════

# ★ 음성 대조 N2 — 하위호환 골든 상수.
# 이 문자열은 **C1 수정 전, 저장소 코드를 한 줄도 건드리기 전에** 현행
# build_transfer_transaction 이 만든 payload 다. 수정한 뒤에 뽑으면 "바뀌지 않았다"는
# 증명이 사후 조작이 된다. 고정 키·고정 blockhash·고정 memo 라 재현 가능하다.
#   (설계 §4-2 는 Keypair.from_bytes(bytes(range(1,65))) 로 적었는데 solders 가 뒤 32바이트를
#    파생 공개키와 대조해 ValueError 를 내므로, 결정론적 고정 키 생성만 from_seed 로 바꿨다.)
GOLDEN_PAYER = "9C6hybhQ6Aycep9jaUnP6uL9ZYvDjUp1aSkFWPUFJtpj"
GOLDEN_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"      # Circle devnet USDC
GOLDEN_DEST = "So11111111111111111111111111111111111111112"
GOLDEN_AMOUNT = 1234567
GOLDEN_MEMO = "AT1:ord_0123456789:abcd1234"
GOLDEN_PAYLOAD = (
    "Ac6Fb7m3BXDx1AmuqEB90IOW1qCL7HlqaP8+7O8zJbDCx2Fqcg2d3H5zwk+kpVGhU6GC07qTUg6oSYKKY1ly"
    "ggsBAAYJebVWLo/mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmRe+MpJgLj4DZlNWEcqLj+yWf+88QyxV6ak"
    "jZgsGwgDt9mpsffhWGg1eimsX2seUCA4qxdsQxw0WMeAAOgvUGQ1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAFSlNamSkhBk0k6HFg2jh8fDW13bySu4HkH6hAQQVEjQabiFf+q4GE+2h/Y0YYwDXaxDncGus7"
    "VZig8AAAAAABBt324ddloZPZy+FGzut5rBy0he1fWzeROoz1hX7/AKk7RCyzkSFX8TqTPQE0KC0DK1/+zQGi"
    "2/G3eQYI3wAup4yXJY9OJInxuz0QKRSODYMLWhOZ2v8QhASOe9jb6fhZAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAADBAEAG0FUMTpvcmRfMDEyMzQ1Njc4OTphYmNkMTIzNAgGAAEFBwMGAQEGBAIHAQAKDIfW"
    "EgAAAAAABg=="
)
assert len(GOLDEN_PAYLOAD) == 600, "골든 상수가 손상됐다 — 조각을 이어 붙인 길이가 다르다"


def _golden_tx(**kw):
    """골든 상수와 같은 고정 입력으로 트랜잭션을 만든다. kw 로 source_owner 만 바꾼다."""
    kp = Keypair.from_seed(bytes(range(1, 33)))
    from solders.hash import Hash
    return kp, x.build_transfer_transaction(
        kp, Pubkey.from_string(GOLDEN_MINT), Pubkey.from_string(GOLDEN_DEST),
        GOLDEN_AMOUNT, DECIMALS, Hash.default(), memo=GOLDEN_MEMO, **kw)


def _transfer_accounts(tx) -> list:
    """tx 안 TransferChecked 의 계정 4개 [source, mint, dest, owner]."""
    keys = list(tx.message.account_keys)
    for ix in tx.message.instructions:
        if keys[ix.program_id_index] != TOKEN_PROGRAM_ID:
            continue
        data = bytes(ix.data)
        if data and data[0] == x._TRANSFER_CHECKED_TAG:
            return [keys[i] for i in list(ix.accounts)[:4]]
    raise AssertionError("TransferChecked instruction 을 찾지 못했습니다")


def test_source_owner() -> None:
    print("\n[4] build_transfer_transaction(source_owner=) — C1 (네트워크 0)")
    mint = Pubkey.from_string(GOLDEN_MINT)
    dest = Pubkey.from_string(GOLDEN_DEST)

    kp, tx_default = _golden_tx()
    check("S1 골든 상수의 고정 키가 맞다", str(kp.pubkey()) == GOLDEN_PAYER, str(kp.pubkey()))
    check("S1 ★ 기본 경로(source_owner=None)가 수정 전 골든 payload 와 바이트 동일 (N2)",
          x.encode_payload(tx_default) == GOLDEN_PAYLOAD,
          "일치" if x.encode_payload(tx_default) == GOLDEN_PAYLOAD else "불일치 — 하위호환 깨짐")

    _, tx_self = _golden_tx(source_owner=kp.pubkey())
    check("S2 source_owner=payer 를 명시해도 기본과 동일",
          x.encode_payload(tx_self) == GOLDEN_PAYLOAD)

    user = Keypair()
    _, tx_deleg = _golden_tx(source_owner=user.pubkey())
    src, _m, _d, owner = _transfer_accounts(tx_deleg)
    check("S3 출처 ATA == 자금 소유자(user)의 ATA",
          src == get_associated_token_address(user.pubkey(), mint), str(src))
    check("S4 authority(계정 idx 3)는 여전히 payer — SPL 이 그 자리에 delegate 를 받는다",
          owner == kp.pubkey(), str(owner))
    check("S5 서명자 1개 — 자금 소유자 서명 불필요",
          len(tx_deleg.signatures) == 1, str(len(tx_deleg.signatures)))

    keys = list(tx_deleg.message.account_keys)
    memo_signer = next((keys[list(ix.accounts)[0]] for ix in tx_deleg.message.instructions
                        if keys[ix.program_id_index] == x.MEMO_PROGRAM_ID), None)
    check("S6 memo signer == payer (대사 키 귀속 불변)", memo_signer == kp.pubkey(),
          str(memo_signer))

    # S7·S8 ★ 판매자측 코드는 한 줄도 안 바꾼다 — verify_payment 는 출처 ATA 를 보지 않는다.
    ok7, reason7, amt7 = x.verify_payment(tx_deleg, mint, dest, GOLDEN_AMOUNT,
                                          expected_order_id="ord_0123456789")
    check("S7 판매자 verify_payment 가 위임 출처 tx 도 통과시킨다",
          ok7 and amt7 == GOLDEN_AMOUNT, f"{reason7} / amount={amt7}")
    ok8, reason8, _ = x.verify_payment(tx_deleg, mint, dest, GOLDEN_AMOUNT,
                                       expected_payer=kp.pubkey())
    check("S8 expected_payer=payer 대조도 통과", ok8, reason8)


async def main(localnet: bool) -> int:
    test_classify()
    await test_state_and_code()
    test_source_owner()
    if localnet:
        await test_localnet()
    else:
        skip("섹션 3 (L-1~L-8 localnet 왕복)", "--localnet 없이 실행됨")

    ok = sum(1 for _, c, _ in _results if c)
    total = len(_results)
    print(f"\n결과: {ok}/{total} 통과" + (f" · 건너뜀 {len(_skipped)}종" if _skipped else ""))
    for name, cond, detail in _results:
        if not cond:
            print(f"  실패: {name} — {detail}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="온체인 예산 레일 테스트")
    ap.add_argument("--localnet", action="store_true",
                    help=f"localnet({RPC}) 왕복 섹션까지 실행")
    raise SystemExit(asyncio.run(main(ap.parse_args().localnet)))
