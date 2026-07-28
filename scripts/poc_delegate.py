"""PoC (임시 파일) — SPL Token 위임(approve/transfer/revoke)이 실제로 성립하는지 localnet 검증.

docs/design/onchain_budget_rfc.md 의 근거 수집용. 본 코드는 한 줄도 건드리지 않는다.
localnet 전용 — devnet/mainnet 로 실행하지 말 것.

실행: python scripts/poc_delegate.py      (localnet http://127.0.0.1:8899 필요)

확인 항목:
  1) delegate 가 서명자일 때 transfer_checked 의 authority 자리
  2) delegated_amount 가 전송마다 깎이는가 / 0 이 되면 어떻게 되는가
  3) 한도 초과·소진 시 어떤 에러로 거절되는가 (우리가 '한도 거부'로 표시할 수 있는가)
  4) 재충전(추가 approve) / 회수(revoke)
  5) 위임 중에도 소유자 본인은 자유롭게 쓸 수 있는가
  6) 잔액 부족과 한도 초과가 구분되는가
"""
from __future__ import annotations
import asyncio
import sys
from decimal import Decimal

sys.stdout.reconfigure(errors="replace")
sys.stderr.reconfigure(errors="replace")

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.message import Message
from solders.transaction import Transaction
from solders.system_program import create_account, CreateAccountParams

from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import (
    approve_checked, revoke, transfer_checked,
    get_associated_token_address, create_idempotent_associated_token_account,
    initialize_mint2, mint_to,
)
from spl.token.models import (
    ApproveCheckedParams, RevokeParams, TransferCheckedParams,
    InitializeMint2Params, MintToParams,
)
from spl.token._layouts import MINT_LAYOUT

RPC = "http://127.0.0.1:8899"
DECIMALS = 6
OK, NG = "[OK]", "[!!]"


def base(ui: str) -> int:
    return int(Decimal(ui) * (10 ** DECIMALS))


async def send(client, payer: Keypair, ixs, signers) -> str:
    bh = (await client.get_latest_blockhash()).value.blockhash
    msg = Message.new_with_blockhash(ixs, payer.pubkey(), bh)
    tx = Transaction(signers, msg, bh)
    resp = await client.send_raw_transaction(
        bytes(tx), opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed))
    await client.confirm_transaction(resp.value, commitment=Confirmed)
    return str(resp.value)


async def try_send(client, payer: Keypair, ixs, signers) -> tuple[bool, str]:
    """성공 여부 + (실패 시) 예외 원문. 우리 코드 경로와 같은 preflight 설정."""
    try:
        sig = await send(client, payer, ixs, signers)
        return True, sig
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def airdrop(client, pk: Pubkey, sol: float = 2.0):
    r = await client.request_airdrop(pk, int(sol * 1_000_000_000))
    await client.confirm_transaction(r.value, commitment=Confirmed)


async def read_ata(client, ata: Pubkey) -> dict:
    """파싱된 토큰 계정 — amount / delegate / delegatedAmount."""
    resp = await client.get_account_info_json_parsed(ata)
    info = resp.value.data.parsed["info"]
    return {
        "amount": info["tokenAmount"]["amount"],
        "delegate": info.get("delegate"),
        "delegatedAmount": (info.get("delegatedAmount") or {}).get("amount"),
    }


def transfer_ix(src_ata, mint, dst_ata, authority: Pubkey, amount: int):
    """authority 자리에 소유자 대신 delegate 를 넣는다 (SPL 계정 순서는 동일)."""
    return transfer_checked(TransferCheckedParams(
        program_id=TOKEN_PROGRAM_ID, source=src_ata, mint=mint, dest=dst_ata,
        owner=authority, amount=amount, decimals=DECIMALS, signers=[]))


async def main() -> int:
    fails = []

    def check(cond, label, extra=""):
        print(f"  {OK if cond else NG} {label}{(' | ' + extra) if extra else ''}")
        if not cond:
            fails.append(label)

    async with AsyncClient(RPC, commitment=Confirmed) as client:
        h = await client.is_connected()
        print(f"localnet 연결: {h}  ({RPC})")

        user, agent, broker = Keypair(), Keypair(), Keypair()
        print(f"user (자금 소유자) : {user.pubkey()}")
        print(f"agent(위임받은 자) : {agent.pubkey()}")
        print(f"broker(수취인)     : {broker.pubkey()}")

        # user 는 mint 권한자 겸 rent 지불자, agent 는 수수료 지불자
        await airdrop(client, user.pubkey(), 5)
        await airdrop(client, agent.pubkey(), 5)

        # ---- 민트 생성 + user ATA 에 100 발행 ----
        mint = Keypair()
        rent = (await client.get_minimum_balance_for_rent_exemption(MINT_LAYOUT.sizeof())).value
        await send(client, user, [
            create_account(CreateAccountParams(
                from_pubkey=user.pubkey(), to_pubkey=mint.pubkey(), lamports=rent,
                space=MINT_LAYOUT.sizeof(), owner=TOKEN_PROGRAM_ID)),
            initialize_mint2(InitializeMint2Params(
                program_id=TOKEN_PROGRAM_ID, mint=mint.pubkey(), decimals=DECIMALS,
                mint_authority=user.pubkey(), freeze_authority=None)),
        ], [user, mint])
        m = mint.pubkey()
        user_ata = get_associated_token_address(user.pubkey(), m)
        broker_ata = get_associated_token_address(broker.pubkey(), m)
        await send(client, user, [
            create_idempotent_associated_token_account(payer=user.pubkey(), owner=user.pubkey(), mint=m),
            mint_to(MintToParams(program_id=TOKEN_PROGRAM_ID, mint=m, dest=user_ata,
                                 mint_authority=user.pubkey(), amount=base("100"), signers=[])),
        ], [user])
        print(f"\n민트 {m} · user 잔액 100.0")

        # =============== 1) approve — 예산 25 위임 ===============
        print("\n[1] user 가 agent 를 delegate 로 등록 (한도 25)")
        await send(client, user, [approve_checked(ApproveCheckedParams(
            program_id=TOKEN_PROGRAM_ID, source=user_ata, mint=m, delegate=agent.pubkey(),
            owner=user.pubkey(), amount=base("25"), decimals=DECIMALS, signers=[]))], [user])
        st = await read_ata(client, user_ata)
        print(f"    {st}")
        check(st["delegate"] == str(agent.pubkey()), "delegate = agent")
        check(st["delegatedAmount"] == str(base("25")), "delegatedAmount = 25.0")

        # =============== 2) delegate 서명 전송 (우리 tx 모양 그대로) ===============
        print("\n[2] agent 서명만으로 user 자금 10 전송 (memo + ATA 생성 + transfer_checked)")
        from solders.instruction import Instruction, AccountMeta
        memo_ix = Instruction(
            program_id=Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"),
            data=b"AT1:ord_deadbeef01:abcd1234",
            accounts=[AccountMeta(pubkey=agent.pubkey(), is_signer=True, is_writable=False)])
        ok, res = await try_send(client, agent, [
            memo_ix,
            create_idempotent_associated_token_account(payer=agent.pubkey(), owner=broker.pubkey(), mint=m),
            transfer_ix(user_ata, m, broker_ata, agent.pubkey(), base("10")),
        ], [agent])
        check(ok, "delegate 단독 서명으로 전송 성공", res[:90])
        st = await read_ata(client, user_ata)
        print(f"    user ATA: {st}")
        check(st["amount"] == str(base("90")), "user 잔액 100 -> 90")
        check(st["delegatedAmount"] == str(base("15")), "delegatedAmount 25 -> 15 (자동 차감)")

        # =============== 3) 한도 초과 ===============
        print("\n[3] 한도(15) 초과 전송 20 시도 — 잔액(90)은 충분")
        ok, res = await try_send(client, agent, [
            transfer_ix(user_ata, m, broker_ata, agent.pubkey(), base("20"))], [agent])
        check(not ok, "한도 초과 전송 거절됨")
        print(f"    에러 원문: {res[:400]}")
        over_limit_err = res

        # =============== 3b) 잔액 부족과 구분되는가 ===============
        print("\n[3b] 대조군 — 한도를 잔액보다 크게 올린 뒤 잔액 초과(200) 시도")
        await send(client, user, [approve_checked(ApproveCheckedParams(
            program_id=TOKEN_PROGRAM_ID, source=user_ata, mint=m, delegate=agent.pubkey(),
            owner=user.pubkey(), amount=base("500"), decimals=DECIMALS, signers=[]))], [user])
        ok, res_bal = await try_send(client, agent, [
            transfer_ix(user_ata, m, broker_ata, agent.pubkey(), base("200"))], [agent])
        check(not ok, "잔액 초과 전송도 거절됨")
        print(f"    에러 원문: {res_bal[:400]}")
        same = ("custom program error: 0x1" in over_limit_err) and ("custom program error: 0x1" in res_bal)
        print(f"    >>> 한도 초과와 잔액 부족이 같은 에러코드인가: {same}")

        # 한도를 다시 15 로 되돌린다
        await send(client, user, [approve_checked(ApproveCheckedParams(
            program_id=TOKEN_PROGRAM_ID, source=user_ata, mint=m, delegate=agent.pubkey(),
            owner=user.pubkey(), amount=base("15"), decimals=DECIMALS, signers=[]))], [user])

        # =============== 4) 한도 정확히 소진 ===============
        print("\n[4] 남은 한도 전액(15) 전송 — 소진 시 delegate 가 어떻게 되는가")
        ok, res = await try_send(client, agent, [
            transfer_ix(user_ata, m, broker_ata, agent.pubkey(), base("15"))], [agent])
        check(ok, "한도 전액 전송 성공", res[:90])
        st = await read_ata(client, user_ata)
        print(f"    user ATA: {st}")
        check(st["delegate"] is None, "소진되면 delegate 가 해제된다(None)")

        print("\n[4b] 소진 후 추가 전송 1 시도")
        ok, res_exhausted = await try_send(client, agent, [
            transfer_ix(user_ata, m, broker_ata, agent.pubkey(), base("1"))], [agent])
        check(not ok, "소진 후 전송 거절됨")
        print(f"    에러 원문: {res_exhausted[:400]}")

        # =============== 5) 재충전 ===============
        print("\n[5] 재충전 — user 가 다시 approve (10)")
        ok, res = await try_send(client, user, [approve_checked(ApproveCheckedParams(
            program_id=TOKEN_PROGRAM_ID, source=user_ata, mint=m, delegate=agent.pubkey(),
            owner=user.pubkey(), amount=base("10"), decimals=DECIMALS, signers=[]))], [user])
        check(ok, "재충전 approve 성공 (user 서명 필요)", res[:90])
        st = await read_ata(client, user_ata)
        check(st["delegatedAmount"] == str(base("10")), "delegatedAmount = 10 (누적이 아니라 덮어쓰기)")
        print(f"    {st}")

        # =============== 5b) agent 가 스스로 한도를 올릴 수 있는가 ===============
        print("\n[5b] agent 가 스스로 approve 를 시도 (자기 한도 상향 공격)")
        ok, res_self = await try_send(client, agent, [approve_checked(ApproveCheckedParams(
            program_id=TOKEN_PROGRAM_ID, source=user_ata, mint=m, delegate=agent.pubkey(),
            owner=agent.pubkey(), amount=base("999"), decimals=DECIMALS, signers=[]))], [agent])
        check(not ok, "agent 자체 한도 상향 거절됨 (owner 만 approve 가능)")
        print(f"    에러 원문: {res_self[:300]}")

        # =============== 6) 소유자는 위임 중에도 자유롭게 쓸 수 있는가 ===============
        print("\n[6] 위임이 걸린 상태에서 user 본인 전송 30")
        ok, res = await try_send(client, user, [
            transfer_ix(user_ata, m, broker_ata, user.pubkey(), base("30"))], [user])
        check(ok, "소유자 본인 전송은 위임과 무관하게 가능", res[:90])
        st = await read_ata(client, user_ata)
        print(f"    user ATA: {st}")
        check(st["delegatedAmount"] == str(base("10")), "소유자 전송은 delegatedAmount 를 깎지 않는다")

        # =============== 7) revoke ===============
        print("\n[7] revoke — user 가 위임 회수")
        await send(client, user, [revoke(RevokeParams(
            program_id=TOKEN_PROGRAM_ID, account=user_ata, owner=user.pubkey(), signers=[]))], [user])
        st = await read_ata(client, user_ata)
        check(st["delegate"] is None, "revoke 후 delegate = None")
        ok, res_revoked = await try_send(client, agent, [
            transfer_ix(user_ata, m, broker_ata, agent.pubkey(), base("1"))], [agent])
        check(not ok, "revoke 후 agent 전송 거절됨")
        print(f"    에러 원문: {res_revoked[:300]}")

        # =============== 8) 에러 코드 요약 ===============
        print("\n[8] 에러 코드 요약 (우리가 '한도 거부'로 표시할 수 있는가)")
        for label, txt in (("한도 초과", over_limit_err), ("잔액 부족", res_bal),
                           ("한도 소진 후", res_exhausted), ("revoke 후", res_revoked)):
            code = "0x?"
            for c in ("0x1", "0x4", "0x3"):
                if f"custom program error: {c}" in txt:
                    code = c
                    break
            print(f"    {label:<12} -> custom program error {code}")

    print("\n" + "=" * 60)
    if fails:
        print(f"실패 {len(fails)}건: {fails}")
        return 1
    print("전 항목 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
