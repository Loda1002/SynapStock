"""온체인 예산 레일 증빙 데모 — SPL Token 위임으로 예산 상한을 체인에 내린다.

**제품 배선이 아니다.** 402 Guard 의 엔진 결제 경로는 이 스크립트가 보여주는 레일을 아직
쓰지 않는다(아카이브의 `wired_into_product: false` 가 그 사실을 기계 판독 가능하게 남긴다).
정확한 문장은 *"예산 상한을 체인이 집행하는 레일을 실증했고, 엔진 배선은 로드맵"* 이다.

무엇을 보이는가
  ② 사용자가 에이전트를 delegate 로 등록한다(한도 N). 수수료는 에이전트가 대납한다.
  ③ 에이전트가 **자기 서명만으로** 사용자 자금을 결제한다. 우리 실제 결제 함수
     (build_transfer_transaction) 로 만들고, 판매자의 실제 verify_payment 로 검증한다.
     체인이 위임 잔여를 스스로 깎는다 — 우리 코드가 깎는 것이 아니다.
  ④ 한도를 넘는 결제를 시도한다. 잔액은 충분하다. 체인이 거절한다.
  ⑤ ★대조군 — 한도를 올리고 이번엔 잔액을 넘겨 시도한다. **에러 코드가 ④와 똑같다.**
     구분하지 않으면 지갑이 빈 것을 "체인이 한도를 집행했다"고 광고하게 된다.
  ⑥ 에이전트가 스스로 한도를 올리려 한다. 체인이 거절한다(approve 는 소유자만 가능).
  ⑦ 사용자가 위임을 회수한다. 엔진을 멈추지 않고도 한 번에 끊긴다.

체인이 강제하는 것은 "이 계정에서 이 민트를 누적 얼마까지"뿐이다. 수취인·종목·청구서
의미·건별 한도·배송은 402 Guard(오프체인)가 계속 담당한다.

실행:
  python scripts/demo_delegation.py                        # localnet · 임시 지갑 · 테스트 민트
  python scripts/demo_delegation.py --budget 25 --spend 10
  python scripts/demo_delegation.py --devnet --budget 2 --spend 1   # CFG 지갑 · CFG USDC 민트

localnet 은 무설정으로 재현된다(.env·시크릿·Gemini 키 불필요):
  wsl -d Ubuntu --cd /root -- /root/.local/share/solana/install/active_release/bin/solana-test-validator

증빙: artifacts/tx/<ts>_<network>_delegation.json
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import secrets as pysecrets
import sys
import unicodedata
from datetime import datetime
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
    approve_checked, create_idempotent_associated_token_account,
    get_associated_token_address, initialize_mint2, mint_to,
)
from spl.token.models import (  # noqa: E402
    ApproveCheckedParams, InitializeMint2Params, MintToParams,
)

from config import CFG, from_base_units, to_base_units  # noqa: E402
from payments import x402_solana as x  # noqa: E402
from payments.delegation import (  # noqa: E402
    GUARD_ONCHAIN_BUDGET, GUARD_ONCHAIN_FUNDS, GUARD_ONCHAIN_NO_DELEGATE,
    approve_budget, classify_rejection, read_delegation, revoke_budget, spl_error_code,
)

LOCALNET_RPC = "http://127.0.0.1:8899"
LOCALNET_NAME = "solana-localnet"


def _width(s: str) -> int:
    """한글·전각 문자를 2칸으로 세는 표시 폭 (배너 줄이 영상 컷에 그대로 나간다)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def hr(title: str) -> None:
    head = f"── {title} "
    print("\n" + head + "─" * max(4, (78 - _width(head)) // 2))


def explorer(sig: str, rpc_url: str, network: str) -> str:
    if "devnet" in network:
        return f"https://explorer.solana.com/tx/{sig}?cluster=devnet"
    from urllib.parse import quote
    return (f"https://explorer.solana.com/tx/{sig}"
            f"?cluster=custom&customUrl={quote(rpc_url, safe='')}")


def ui(base_units: int, decimals: int) -> str:
    return f"{from_base_units(base_units, decimals):.6f}"


async def _send(client, payer: Keypair, ixs, signers) -> str:
    bh = await x.get_latest_blockhash(client)
    msg = Message.new_with_blockhash(ixs, payer.pubkey(), bh)
    sig, ok = await x.submit_and_confirm(client, Transaction(signers, msg, bh))
    if not ok:
        raise RuntimeError(f"준비 트랜잭션 실패: {sig}")
    return sig


async def _airdrop(client, pk: Pubkey, sol: float) -> None:
    from solana.rpc.commitment import Confirmed
    r = await client.request_airdrop(pk, int(sol * 1_000_000_000))
    await client.confirm_transaction(r.value, commitment=Confirmed)


async def setup_localnet(client, decimals: int):
    """임시 3지갑 + 테스트 민트 + user 에 100 발행. 심사위원이 무설정으로 재현하는 경로다."""
    user, agent, broker = Keypair(), Keypair(), Keypair()
    funder = Keypair()
    await _airdrop(client, funder.pubkey(), 5)
    await _airdrop(client, agent.pubkey(), 2)      # 수수료는 전부 agent 가 낸다
    # ★ user 에게는 SOL 을 주지 않는다 — approve/revoke 수수료를 agent 가 대납할 수 있다는
    #   것을 화면에서 그대로 보이기 위해서다(자금 소유자 지갑에 SOL 이 필요 없다).

    mint_kp = Keypair()
    rent = (await client.get_minimum_balance_for_rent_exemption(MINT_LAYOUT.sizeof())).value
    await _send(client, funder, [
        create_account(CreateAccountParams(
            from_pubkey=funder.pubkey(), to_pubkey=mint_kp.pubkey(), lamports=rent,
            space=MINT_LAYOUT.sizeof(), owner=TOKEN_PROGRAM_ID)),
        initialize_mint2(InitializeMint2Params(
            program_id=TOKEN_PROGRAM_ID, mint=mint_kp.pubkey(), decimals=decimals,
            mint_authority=funder.pubkey(), freeze_authority=None)),
    ], [funder, mint_kp])
    mint = mint_kp.pubkey()

    await _send(client, funder, [
        create_idempotent_associated_token_account(
            payer=funder.pubkey(), owner=user.pubkey(), mint=mint),
        mint_to(MintToParams(program_id=TOKEN_PROGRAM_ID, mint=mint,
                             dest=get_associated_token_address(user.pubkey(), mint),
                             mint_authority=funder.pubkey(),
                             amount=to_base_units(Decimal("100"), decimals), signers=[])),
        # 수취인 ATA 도 미리 만든다 — 거절되는 결제와 같은 트랜잭션에 두면 함께 롤백된다.
        create_idempotent_associated_token_account(
            payer=funder.pubkey(), owner=broker.pubkey(), mint=mint),
    ], [funder])
    return user, agent, broker, mint


def load_devnet_wallets():
    """CFG 지갑 3개를 읽는다. 없으면 무엇이 없는지 말하고 중단한다."""
    kps = {}
    for name in ("user", "trading", "broker"):
        path = os.path.join(CFG.wallet_dir, f"{name}.json")
        if not os.path.exists(path):
            raise SystemExit(
                f"\n[중단] 지갑 파일이 없습니다: {path}\n"
                f"        먼저 python scripts/setup_devnet.py 를 실행하세요.")
        kps[name] = x.load_keypair(path)
    return kps["user"], kps["trading"], kps["broker"]


async def attempt_payment(client, agent: Keypair, user_pk: Pubkey, broker_pk: Pubkey,
                          mint: Pubkey, amount_base: int, decimals: int, order_id: str):
    """우리 실제 결제 함수로 위임 출처 결제를 만들어 제출한다. (성공여부, 서명 or 예외, tx)"""
    memo = f"{x.MEMO_PREFIX}:{order_id}:{pysecrets.token_hex(4)}"
    bh = await x.get_latest_blockhash(client)
    tx = x.build_transfer_transaction(
        payer=agent, mint=mint, dest_owner=broker_pk, amount=amount_base,
        decimals=decimals, blockhash=bh, memo=memo, source_owner=user_pk)
    try:
        sig, ok = await x.submit_and_confirm(client, tx)
        return ok, sig, tx
    except BaseException as e:                       # noqa: BLE001 — 거부 예외를 분류한다
        return False, e, tx


async def classify_failure(client, exc, user_pk: Pubkey, mint: Pubkey,
                           requested_base: int, decimals: int, agent_pk: Pubkey):
    """실패 직후 계정 상태를 **1회** 재조회해 원인을 갈라낸다 (설계 §3-3)."""
    code = spl_error_code(exc)
    try:
        state = await read_delegation(client, user_pk, mint)
    except BaseException as e:                       # noqa: BLE001
        # 상태를 못 읽으면 아무 주장도 하지 않는다 — '한도'라고 부르지 않는다.
        from payments.delegation import GUARD_ONCHAIN_UNCLASSIFIED
        from payments.guard import GuardResult
        return code, None, GuardResult(
            False, GUARD_ONCHAIN_UNCLASSIFIED,
            f"상태 조회 실패: {type(e).__name__}", "demo_delegation.py", "", f"code={code}")
    return code, state, classify_rejection(code, state, requested_base, decimals, agent_pk)


async def main() -> int:
    ap = argparse.ArgumentParser(description="온체인 예산 레일 증빙 데모 (레일 검증 전용)")
    ap.add_argument("--devnet", action="store_true",
                    help="CFG 지갑·CFG USDC 민트로 devnet 에서 실행 (민트 생성 안 함)")
    ap.add_argument("--budget", default="25", help="사용자가 위임할 예산 (기본 25)")
    ap.add_argument("--spend", default="10", help="정상 결제 금액 (기본 10)")
    args = ap.parse_args()

    budget_ui, spend_ui = Decimal(args.budget), Decimal(args.spend)
    if spend_ui <= 0 or budget_ui <= spend_ui:
        print("[중단] --budget 은 --spend 보다 커야 합니다 (한도 초과 시나리오가 성립해야 함).")
        return 1

    # ---------- 네트워크 선택 ----------
    # 기본이 localnet 인 것이 안전장치다. .env 의 SOLANA_RPC_URL 이 devnet 을 가리켜도
    # --devnet 플래그 없이는 devnet 으로 나가지 않는다.
    if args.devnet:
        rpc_url, network, decimals = CFG.rpc_url, CFG.network, CFG.usdc_decimals
    else:
        rpc_url, network, decimals = LOCALNET_RPC, LOCALNET_NAME, 6

    budget = to_base_units(budget_ui, decimals)
    spend = to_base_units(spend_ui, decimals)

    print("402 Guard — SPL Token 위임으로 예산 상한을 체인에 내린다 (레일 증명 · 제품 배선 아님)")
    print(f"  네트워크   : {network}  {rpc_url}")

    client = await x.get_client(rpc_url)
    steps: list = []
    classification: list = []
    unexpected: list = []
    try:
        # ═══════════ ① 준비 ═══════════
        if args.devnet:
            user, agent, broker = load_devnet_wallets()
            mint = Pubkey.from_string(CFG.usdc_mint)
            self_minted = False
        else:
            user, agent, broker, mint = await setup_localnet(client, decimals)
            self_minted = True

        start = await read_delegation(client, user.pubkey(), mint)
        agent_usdc = await x.get_token_balance_ui(client, agent.pubkey(), mint)
        print(f"  user (자금 소유자) : {user.pubkey()}   USDC {ui(start.balance, decimals)}")
        print(f"  agent(거래 실행자) : {agent.pubkey()}   USDC {agent_usdc}"
              f"   <- 이 지갑에는 결제할 돈이 없다")
        print(f"  broker(수취인)     : {broker.pubkey()}")
        print(f"  민트               : {mint}"
              f"{'  (테스트 민트 · 이 데모가 발행)' if self_minted else '  (CFG.usdc_mint)'}")

        if not start.exists or start.balance < spend * 2:
            print(f"\n[중단] user 지갑의 잔액이 부족합니다 "
                  f"(현재 {ui(start.balance, decimals)} · 최소 {ui(spend * 2, decimals)} 필요).")
            if args.devnet:
                print(f"        Circle devnet 파우셋(https://faucet.circle.com)에서 "
                      f"user 지갑 {user.pubkey()} 로 USDC 를 받은 뒤 다시 실행하세요.")
            return 1

        # ═══════════ ② approve ═══════════
        hr(f"② 사용자가 에이전트에게 예산 {budget_ui} USDC 를 위임한다 (SPL Token approve)")
        user_sol_before = await x.get_sol_balance(client, user.pubkey())
        sig_approve, st = await approve_budget(client, user, agent.pubkey(), mint, budget,
                                               decimals, fee_payer=agent)
        user_sol_after = await x.get_sol_balance(client, user.pubkey())
        print(f"  서명자     : user (자금 소유자)        수수료 : agent 대납 "
              f"(user SOL {user_sol_before:.6f} -> {user_sol_after:.6f})")
        print(f"  tx         : {sig_approve}")
        print(f"  explorer   : {explorer(sig_approve, rpc_url, network)}")
        print(f"  계정 상태  : delegate={st.delegate}  "
              f"delegatedAmount={ui(st.delegated_amount, decimals)}")
        steps.append({"step": "approve", "ok": True, "signature": sig_approve,
                      "explorer": explorer(sig_approve, rpc_url, network),
                      "delegated_before": ui(start.delegated_amount, decimals),
                      "delegated_after": ui(st.delegated_amount, decimals),
                      "fee_payer": "agent", "owner_sol_before": f"{user_sol_before:.9f}",
                      "owner_sol_after": f"{user_sol_after:.9f}"})
        if not st.is_delegated_to(agent.pubkey()) or st.delegated_amount != budget:
            unexpected.append("approve 후 위임 상태가 기대와 다릅니다")

        # ═══════════ ③ 정상 결제 ═══════════
        hr(f"③ 에이전트가 자기 서명만으로 사용자 자금 {spend_ui} USDC 를 결제한다")
        order_id = "ord_" + pysecrets.token_hex(5)
        bal_before = st.balance
        ok, res, tx = await attempt_payment(client, agent, user.pubkey(), broker.pubkey(),
                                            mint, spend, decimals, order_id)
        print(f"  트랜잭션   : payments/x402_solana.build_transfer_transaction(source_owner=user)")
        v_ok, v_reason, v_amount = x.verify_payment(tx, mint, broker.pubkey(), spend,
                                                    expected_order_id=order_id)
        print(f"  판매자 검증: verify_payment -> ok={v_ok}  amount={v_amount}  "
              f"({v_reason} · 판매자 코드 변경 0줄)")
        if not ok:
            print(f"  [실패] 정상 결제가 거절됐습니다: {res}")
            return 1
        st3 = await read_delegation(client, user.pubkey(), mint)
        broker_bal = await x.get_token_balance_ui(client, broker.pubkey(), mint)
        print(f"  tx         : {res}")
        print(f"  explorer   : {explorer(str(res), rpc_url, network)}")
        print(f"  계정 상태  : 잔액 {ui(bal_before, decimals)} -> {ui(st3.balance, decimals)}"
              f" ·  delegatedAmount {ui(st.delegated_amount, decimals)}"
              f" -> {ui(st3.delegated_amount, decimals)}")
        print(f"  수취인 잔액: {broker_bal}")
        print(f"  ※ 한도를 깎은 것은 우리 코드가 아닙니다. SPL Token 프로그램이 깎았습니다.")
        steps.append({"step": "transfer", "ok": True, "signature": str(res),
                      "explorer": explorer(str(res), rpc_url, network),
                      "order_id": order_id, "amount_usdc": str(spend_ui),
                      "verify_payment": {"ok": v_ok, "reason": v_reason, "amount": v_amount},
                      "balance_before": ui(bal_before, decimals),
                      "balance_after": ui(st3.balance, decimals),
                      "delegated_before": ui(st.delegated_amount, decimals),
                      "delegated_after": ui(st3.delegated_amount, decimals)})
        if not v_ok or st3.delegated_amount != budget - spend:
            unexpected.append("정상 결제 후 판매자 검증 또는 위임 차감이 기대와 다릅니다")

        # ═══════════ ④ 한도 초과 ═══════════
        over = spend * 2                       # 잔여(budget-spend)보다 크고 잔액보다는 작다
        hr(f"④ 한도를 넘는 {from_base_units(over, decimals)} USDC 결제를 시도한다 "
           f"(잔액 {ui(st3.balance, decimals)} 은 충분하다)")
        agent_sol_before = await x.get_sol_balance(client, agent.pubkey())
        ok, res, _ = await attempt_payment(client, agent, user.pubkey(), broker.pubkey(),
                                           mint, over, decimals, "ord_" + pysecrets.token_hex(5))
        agent_sol_after = await x.get_sol_balance(client, agent.pubkey())
        if ok:
            unexpected.append("한도 초과 결제가 체인에서 통과했습니다")
            print("  [실패] 한도 초과 결제가 통과했습니다 — 레일이 성립하지 않습니다.")
            return 1
        code, state, verdict = await classify_failure(client, res, user.pubkey(), mint,
                                                      over, decimals, agent.pubkey())
        print(f"  체인 응답  : custom program error 0x{code:x}" if code is not None
              else "  체인 응답  : (코드 추출 실패)")
        print(f"  기록 여부  : 체인에 남지 않음 (preflight 거절 · 수수료 "
              f"{agent_sol_before - agent_sol_after:.9f} SOL · 서명 미기록)")
        print(f"  판정       : {verdict.code} — {verdict.expected} < {verdict.actual}")
        print(f"  유출       : 0.00 USDC")
        print(f"  ※ 이 거절은 402 Guard(파이썬)가 한 것이 아닙니다. 우리는 서명해서 보냈고,")
        print(f"     Solana 가 거절했습니다.")
        classification.append({
            "case": "over-limit", "requested_usdc": str(from_base_units(over, decimals)),
            "balance_usdc": ui(state.balance, decimals) if state else "",
            "delegated_usdc": ui(state.delegated_amount, decimals) if state else "",
            "spl_error_code": code, "fee_charged_sol": f"{agent_sol_before - agent_sol_after:.9f}",
            **verdict.as_event()})
        if verdict.code != GUARD_ONCHAIN_BUDGET:
            unexpected.append(f"한도 초과 판정이 {verdict.code} 입니다")

        # ═══════════ ⑤ 대조군 — 같은 코드, 다른 원인 ═══════════
        funds_request = st3.balance + spend            # 잔액을 넘는 금액
        funds_limit = funds_request * 2                # 한도는 넉넉하게 올려 둔다
        hr(f"⑤ 대조군 — 한도를 {from_base_units(funds_limit, decimals)} USDC 로 올린 뒤, "
           f"잔액({ui(st3.balance, decimals)})을 넘는 "
           f"{from_base_units(funds_request, decimals)} USDC 를 요청한다")
        sig_raise, st5 = await approve_budget(client, user, agent.pubkey(), mint,
                                              funds_limit, decimals, fee_payer=agent)
        ok, res, _ = await attempt_payment(client, agent, user.pubkey(), broker.pubkey(),
                                           mint, funds_request, decimals,
                                           "ord_" + pysecrets.token_hex(5))
        if ok:
            unexpected.append("잔액을 넘는 결제가 체인에서 통과했습니다")
            print("  [실패] 잔액 초과 결제가 통과했습니다.")
            return 1
        code5, state5, verdict5 = await classify_failure(client, res, user.pubkey(), mint,
                                                         funds_request, decimals, agent.pubkey())
        print(f"  한도 상향  : delegatedAmount = {ui(st5.delegated_amount, decimals)}  "
              f"(tx {sig_raise})")
        print(f"  체인 응답  : custom program error 0x{code5:x}        <- ④와 같은 코드입니다"
              if code5 is not None else "  체인 응답  : (코드 추출 실패)")
        print(f"  판정       : {verdict5.code} — {verdict5.expected} < {verdict5.actual}")
        print(f"  ※ 에러 코드만으로는 '한도 거부'와 '잔액 부족'을 구분할 수 없습니다. 그래서")
        print(f"     실패할 때마다 계정 상태를 한 번 더 읽어 어느 쪽인지 판정합니다. 구분하지")
        print(f"     않으면 지갑이 빈 것을 \"체인이 한도를 집행했다\"고 광고하게 됩니다.")
        classification.append({
            "case": "insufficient-funds",
            "requested_usdc": str(from_base_units(funds_request, decimals)),
            "balance_usdc": ui(state5.balance, decimals) if state5 else "",
            "delegated_usdc": ui(state5.delegated_amount, decimals) if state5 else "",
            "spl_error_code": code5, **verdict5.as_event()})
        if verdict5.code != GUARD_ONCHAIN_FUNDS:
            unexpected.append(f"잔액 부족 판정이 {verdict5.code} 입니다")
        same_code = code is not None and code == code5

        # ═══════════ ⑥ 자기 상향 공격 ═══════════
        hr("⑥ 에이전트가 스스로 자기 한도를 올리려 한다 (자기 상향 공격)")
        # ⚠ owner 자리에 user 를 넣으면 서명자가 모자라 solders 가 PanicException 을 내고
        #    (BaseException 직계라 except Exception 에 안 잡힌다) 데모가 통째로 죽는다.
        #    공격자가 실제로 만들 수 있는 트랜잭션은 자기가 서명 가능한 것뿐이므로 owner=agent 다.
        self_ix = approve_checked(ApproveCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=get_associated_token_address(user.pubkey(), mint), mint=mint,
            delegate=agent.pubkey(), owner=agent.pubkey(),
            amount=to_base_units(Decimal("999999"), decimals), decimals=decimals, signers=[]))
        self_ok, self_err = True, None
        try:
            await _send(client, agent, [self_ix], [agent])
        except BaseException as e:                    # noqa: BLE001
            self_ok, self_err = False, e
        code6 = spl_error_code(self_err) if self_err is not None else None
        st6 = await read_delegation(client, user.pubkey(), mint)
        print(f"  체인 응답  : "
              + (f"custom program error 0x{code6:x}" if code6 is not None else "거절"))
        print(f"  계정 상태  : delegatedAmount {ui(st6.delegated_amount, decimals)} "
              f"(그대로 — 에이전트는 자기 한도를 못 올린다)")
        print(f"  ※ approve 는 계정 소유자만 할 수 있습니다. 상한을 정하는 권한은 사용자에게 "
              f"남습니다.")
        classification.append({
            "case": "self-raise", "spl_error_code": code6,
            "code": GUARD_ONCHAIN_NO_DELEGATE,
            "detail": "에이전트가 자기 한도를 올리려 했으나 체인이 거절했습니다 "
                      "(approve 는 계정 소유자만 가능 — owner mismatch). 결제 거부가 아니라 "
                      "권한 거부라 classify_rejection 의 판정 대상이 아니다.",
            "delegated_usdc": ui(st6.delegated_amount, decimals)})
        if self_ok or st6.delegated_amount != funds_limit:
            unexpected.append("에이전트의 자기 한도 상향이 막히지 않았습니다")

        # ═══════════ ⑦ revoke ═══════════
        hr("⑦ 사용자가 위임을 회수한다 (revoke)")
        sig_revoke = await revoke_budget(client, user, mint, fee_payer=agent)
        st7 = await read_delegation(client, user.pubkey(), mint)
        ok, res, _ = await attempt_payment(client, agent, user.pubkey(), broker.pubkey(),
                                           mint, spend, decimals, "ord_" + pysecrets.token_hex(5))
        if ok:
            unexpected.append("회수 후에도 결제가 통과했습니다")
            print("  [실패] 회수 후에도 결제가 통과했습니다.")
            return 1
        code7, state7, verdict7 = await classify_failure(client, res, user.pubkey(), mint,
                                                         spend, decimals, agent.pubkey())
        print(f"  tx         : {sig_revoke}    계정 상태 : delegate={st7.delegate}")
        print(f"  explorer   : {explorer(sig_revoke, rpc_url, network)}")
        print(f"  이후 전송  : "
              + (f"custom program error 0x{code7:x}" if code7 is not None else "거절")
              + f" -> {verdict7.code}")
        print(f"  ※ 엔진을 멈추지 않고도, 사용자가 자기 지갑에서 한 번에 끊을 수 있습니다.")
        steps.append({"step": "revoke", "ok": True, "signature": sig_revoke,
                      "explorer": explorer(sig_revoke, rpc_url, network),
                      "delegate_after": st7.delegate})
        classification.append({
            "case": "after-revoke", "requested_usdc": str(spend_ui),
            "balance_usdc": ui(state7.balance, decimals) if state7 else "",
            "delegated_usdc": ui(state7.delegated_amount, decimals) if state7 else "",
            "spl_error_code": code7, **verdict7.as_event()})
        if verdict7.code != GUARD_ONCHAIN_NO_DELEGATE:
            unexpected.append(f"회수 후 판정이 {verdict7.code} 입니다")

        # ---------- 유출 계측 (상수가 아니라 온체인 차분이다) ----------
        end = await read_delegation(client, user.pubkey(), mint)
        left_account = start.balance - end.balance          # 사용자 계정에서 실제로 나간 금액
        leak_base = left_account - spend                    # 정상 결제 1건 외에 나간 것
        leak = from_base_units(max(leak_base, 0), decimals)
    finally:
        await client.close()

    # ---------- 증빙 아카이빙 ----------
    out_dir = os.path.join(ROOT, "artifacts", "tx")
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(out_dir, f"{ts}_{network}_delegation.json")
    rejections = [c for c in classification if c["case"] != "self-raise"]
    archive = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "scripts/demo_delegation.py",
        "kind": "spl-token-delegation",
        # ★ 이 스키마에서 가장 중요한 한 줄 — 기계 판독 가능한 과장 방지 장치다.
        "wired_into_product": False,
        "network": network,
        "rpc_url": rpc_url,
        "wallets": {"user": str(user.pubkey()), "agent": str(agent.pubkey()),
                    "broker": str(broker.pubkey())},
        "mints": {"usdc": str(mint), "decimals": decimals, "self_minted": self_minted},
        "budget": {"approved_usdc": str(budget_ui),
                   "unit": "cumulative-gross-draw-from-owner-ata"},
        "steps": steps,
        "classification": classification,
        "summary": {
            "approve_tx": steps[0]["signature"] if steps else "",
            "transfer_tx": steps[1]["signature"] if len(steps) > 1 else "",
            "revoke_tx": steps[2]["signature"] if len(steps) > 2 else "",
            "onchain_rejections": len(rejections),
            "same_error_code_for_budget_and_funds": same_code,
            "limit_enforced_by_chain": any(c.get("code") == GUARD_ONCHAIN_BUDGET
                                           for c in classification),
            "self_raise_blocked": not self_ok,
            "leak_usdc": f"{leak:.2f}",
        },
        "boundary": "체인이 강제하는 것은 '이 계정에서 이 민트를 누적 얼마까지'뿐이다. "
                    "수취인·종목·청구서 의미·건별 한도·배송은 402 Guard(오프체인)가 계속 "
                    "담당한다. 이 스크립트는 레일 증명 전용이고 엔진 결제 경로는 무변경이다.",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2, default=str)

    # ---------- 요약 ----------
    s = archive["summary"]
    hr("요약")
    print(f"  체인이 집행한 것  : 이 계정에서 이 민트를 누적 {budget_ui} 까지 — 그 하나")
    print(f"  체인이 못 보는 것 : 수취인 · 종목 · 청구서 의미 · 건별 한도 · 배송 "
          f"-> 402 Guard 가 담당")
    print(f"  온체인 거절       : {s['onchain_rejections']}건 (한도 1 · 잔액 1 · 회수 후 1)"
          f"   유출 : {s['leak_usdc']} USDC")
    print(f"  같은 에러 코드    : {'예' if s['same_error_code_for_budget_and_funds'] else '아니오'}"
          f" — 그래서 상태를 한 번 더 읽어 갈라냅니다")
    print(f"  자기 상향 차단    : {'예' if s['self_raise_blocked'] else '아니오'}")
    print(f"  제품 배선 여부    : 아니오 — 이 스크립트는 레일 증명 전용이고 "
          f"엔진 경로는 무변경입니다")
    print(f"  증빙              : {os.path.relpath(path, ROOT)}")

    if unexpected:
        print("\n[실패] 기대와 다른 결과:")
        for u in unexpected:
            print(f"  - {u}")
        return 1
    ok_all = (s["onchain_rejections"] == 3 and s["limit_enforced_by_chain"]
              and s["self_raise_blocked"] and Decimal(s["leak_usdc"]) == 0
              and archive["wired_into_product"] is False)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
