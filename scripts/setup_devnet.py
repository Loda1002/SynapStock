"""devnet 준비 스크립트 (네트워크 필요 — Cloud Shell 또는 로컬에서 실행).

수행:
  1) user/trading/broker 지갑 생성·저장 (secrets/, WALLET_DIR)
  2) trading·broker 지갑에 devnet SOL 에어드랍 (수수료용)
  3) 테스트 USDC 민트 생성 → trading 지갑에 1000 USDC 지급
  4) 테스트 주식 민트(tAAPL) 생성 → broker 지갑에 100 tAAPL 지급
  5) 생성된 민트 주소를 .env 에 기록

주의: 여기서 만드는 USDC 는 데모용 테스트 토큰입니다(Circle 실제 devnet USDC 아님).
전 과정 통제 가능·재현 가능하게 하기 위함. 실제 devnet USDC 를 쓰려면 Circle 파우셋
사용 후 .env 의 USDC_MINT 를 교체하세요.

실행:  python scripts/setup_devnet.py
"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import create_account, CreateAccountParams, transfer, TransferParams
from solders.message import Message
from solders.transaction import Transaction

from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token._layouts import MINT_LAYOUT
from spl.token.instructions import (
    initialize_mint2, mint_to, create_idempotent_associated_token_account,
    get_associated_token_address,
)
from spl.token.models import InitializeMint2Params, MintToParams

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts

from config import CFG
from payments import x402_solana as x

USDC_DECIMALS = 6
STOCK_DECIMALS = 6
USDC_SUPPLY = 1000
STOCK_SUPPLY = 100
# 브로커 운용자본(USDC 준비금) — 주식이 오른 뒤 되사줄 때(익절 매도) 지급 재원.
# 없으면 브로커가 매수로 받은 USDC 만으로는 평가이익만큼을 지급하지 못해 매도 정산이 실패한다.
BROKER_USDC_RESERVE = 500


def _is_transient(e: Exception) -> bool:
    """예외 체인(래핑된 원인 포함)에서 일시적 RPC 오류 신호를 찾는다.
    solana-py 는 429 를 SolanaRpcException 으로 감싸 str() 에 '429' 가 안 보이므로
    __cause__/__context__ 를 따라가며 확인한다."""
    cur, parts = e, []
    for _ in range(6):
        if cur is None:
            break
        parts.append(type(cur).__name__)
        parts.append(str(cur))
        cur = cur.__cause__ or cur.__context__
    text = " ".join(parts)
    return any(s in text for s in ("429", "Too Many", "Internal error",
                                   "SolanaRpcException", "timed out", "Timeout"))


async def _rpc_retry(factory, retries: int = 6, label: str = ""):
    """devnet 공용 RPC(api.devnet.solana.com)는 짧은 시간에 요청이 몰리면 429 를 준다.
    429·일시적 내부 오류는 지수 백오프로 재시도한다 — 조회·confirm 은 idempotent 하고
    재제출도 동일 blockhash·서명이라 중복 tx 가 생기지 않아 안전하다."""
    for attempt in range(retries):
        try:
            return await factory()
        except Exception as e:
            if _is_transient(e) and attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"    (RPC 혼잡{' ' + label if label else ''} — {wait}s 후 재시도 {attempt + 1}/{retries - 1})")
                await asyncio.sleep(wait)
                continue
            raise
    raise RuntimeError("RPC 재시도 소진")


async def send(client: AsyncClient, payer: Keypair, instructions, signers) -> str:
    bh = (await _rpc_retry(lambda: client.get_latest_blockhash())).value.blockhash
    msg = Message.new_with_blockhash(instructions, payer.pubkey(), bh)
    tx = Transaction(signers, msg, bh)
    resp = await _rpc_retry(lambda: client.send_raw_transaction(
        bytes(tx), opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed)), label="제출")
    sig = resp.value
    await _rpc_retry(lambda: client.confirm_transaction(sig, commitment=Confirmed), label="confirm")
    return str(sig)


async def transfer_sol(client: AsyncClient, payer: Keypair, to: Pubkey, sol: float) -> str:
    """payer → to 네이티브 SOL 이체. devnet 파우셋이 막힐 때(GitHub 조건 등) 여유
    지갑에서 부족한 지갑으로 수수료를 충당하는 용도."""
    lamports = int(sol * 1_000_000_000)
    ix = transfer(TransferParams(from_pubkey=payer.pubkey(), to_pubkey=to, lamports=lamports))
    return await send(client, payer, [ix], [payer])


async def create_mint(client, payer: Keypair, authority: Pubkey, decimals: int) -> Pubkey:
    mint = Keypair()
    rent = (await client.get_minimum_balance_for_rent_exemption(MINT_LAYOUT.sizeof())).value
    ix_create = create_account(CreateAccountParams(
        from_pubkey=payer.pubkey(), to_pubkey=mint.pubkey(),
        lamports=rent, space=MINT_LAYOUT.sizeof(), owner=TOKEN_PROGRAM_ID,
    ))
    ix_init = initialize_mint2(InitializeMint2Params(
        program_id=TOKEN_PROGRAM_ID, mint=mint.pubkey(),
        decimals=decimals, mint_authority=authority, freeze_authority=None,
    ))
    await send(client, payer, [ix_create, ix_init], [payer, mint])
    return mint.pubkey()


async def mint_tokens(client, payer_authority: Keypair, mint: Pubkey, owner: Pubkey,
                      amount_ui: int, decimals: int) -> None:
    ata = get_associated_token_address(owner, mint)
    ix_ata = create_idempotent_associated_token_account(
        payer=payer_authority.pubkey(), owner=owner, mint=mint)
    ix_mint = mint_to(MintToParams(
        program_id=TOKEN_PROGRAM_ID, mint=mint, dest=ata,
        mint_authority=payer_authority.pubkey(),
        amount=amount_ui * (10 ** decimals), signers=[],
    ))
    await send(client, payer_authority, [ix_ata, ix_mint], [payer_authority])


def write_env(usdc_mint: Pubkey, stock_mint: Pubkey) -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root, ".env")
    example = os.path.join(root, ".env.example")
    lines = []
    src = env_path if os.path.exists(env_path) else example
    # 한국어 주석이 들어간 .env 는 UTF-8 이다 — 인코딩 미지정 시 cp949(한국어 Windows)로
    # 열려 UnicodeDecodeError 로 죽는다. 읽기·쓰기 모두 UTF-8 로 고정한다.
    with open(src, encoding="utf-8") as f:
        for line in f:
            if line.startswith("USDC_MINT="):
                line = f"USDC_MINT={usdc_mint}\n"
            elif line.startswith("STOCK_MINT="):
                line = f"STOCK_MINT={stock_mint}\n"
            lines.append(line)
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"→ .env 업데이트: USDC_MINT / STOCK_MINT 기록 완료")


async def main() -> None:
    wd = CFG.wallet_dir
    user = x.load_keypair(os.path.join(wd, "user.json")) if os.path.exists(
        os.path.join(wd, "user.json")) else x.new_keypair()
    trading = x.load_keypair(os.path.join(wd, "trading.json")) if os.path.exists(
        os.path.join(wd, "trading.json")) else x.new_keypair()
    broker = x.load_keypair(os.path.join(wd, "broker.json")) if os.path.exists(
        os.path.join(wd, "broker.json")) else x.new_keypair()
    # 사용자(위임자) 키 — mandate 서명 전용. 온체인 자금 불필요라 에어드랍 대상에서 제외.
    x.save_keypair(user, os.path.join(wd, "user.json"))
    x.save_keypair(trading, os.path.join(wd, "trading.json"))
    x.save_keypair(broker, os.path.join(wd, "broker.json"))
    print(f"User    지갑: {user.pubkey()} (mandate 서명 전용, 에어드랍 없음)")
    print(f"Trading 지갑: {trading.pubkey()}")
    print(f"Broker  지갑: {broker.pubkey()}")

    async with AsyncClient(CFG.rpc_url, commitment=Confirmed) as client:
        print("\n[1/4] SOL 확인·에어드랍 (수수료용)…")
        for name, kp in [("trading", trading), ("broker", broker)]:
            cur = await x.get_sol_balance(client, kp.pubkey())
            if cur >= 0.05:
                print(f"  {name}: 이미 {cur:.4f} SOL — 에어드랍 생략")
                continue
            try:
                sig = await x.request_airdrop(client, kp.pubkey(), 2.0)
                print(f"  {name}: airdrop OK ({sig[:16]}…)")
            except Exception as e:
                print(f"  {name}: airdrop 실패 — 파우셋 한도일 수 있음"
                      f"(뒤에서 여유 지갑으로 충당 시도). ({type(e).__name__})")

        # SOL 선검사 — 에어드랍이 파우셋 한도로 실패하면 지갑에 SOL 이 없다. 이 상태로
        # [2/4] 로 넘어가면 민트 생성(rent 지불)이 정체불명 RPC 에러로 크래시한다.
        MIN_SOL = 0.05
        FUND_SOL = 0.5   # trading→broker 자동 이체량 (민트·ATA rent 에 충분한 여유)
        bal = {}
        for name, kp in [("trading", trading), ("broker", broker)]:
            bal[name] = await x.get_sol_balance(client, kp.pubkey())
            print(f"  {name} SOL 잔액: {bal[name]:.4f}")
        # devnet 파우셋은 GitHub 공개 repo 조건 등으로 막히기 쉽다. 한쪽(보통 trading)에
        # 여유가 있으면 broker 부족분을 거기서 자동 충당한다 → 파우셋 한 번(또는 0번)으로 양쪽 커버.
        if bal["broker"] < MIN_SOL and bal["trading"] >= FUND_SOL + MIN_SOL:
            print(f"  broker SOL 부족 → trading 에서 {FUND_SOL} SOL 이체(파우셋 없이 충당)…")
            sig = await transfer_sol(client, trading, broker.pubkey(), FUND_SOL)
            bal["broker"] = await x.get_sol_balance(client, broker.pubkey())
            print(f"  이체 완료({sig[:16]}…) → broker SOL 잔액: {bal['broker']:.4f}")
        low = [(n, kp.pubkey(), bal[n]) for n, kp in [("trading", trading), ("broker", broker)]
               if bal[n] < MIN_SOL]
        if low:
            print(f"\n[중단] 민트 생성에 필요한 SOL 이 부족합니다(지갑당 최소 {MIN_SOL} SOL).")
            print("아래 지갑을 https://faucet.solana.com 에서 수동 충전한 뒤 이 스크립트를 다시 실행하세요:")
            for name, pk, b in low:
                print(f"  - {name}: {pk}  (현재 {b:.4f} SOL)")
            print("(지갑 키는 secrets/ 에 이미 저장됐으므로, 충전 후 재실행하면 같은 지갑을 그대로 씁니다.)")
            return

        print("\n[2/4] 테스트 USDC 민트 생성…")
        usdc_mint = await create_mint(client, trading, trading.pubkey(), USDC_DECIMALS)
        print(f"  USDC_MINT = {usdc_mint}")

        print("\n[3/4] 테스트 주식 민트 생성…")
        stock_mint = await create_mint(client, broker, broker.pubkey(), STOCK_DECIMALS)
        print(f"  STOCK_MINT = {stock_mint}  ({CFG.stock_symbol})")

        print("\n[4/4] 초기 잔액 지급…")
        await mint_tokens(client, trading, usdc_mint, trading.pubkey(), USDC_SUPPLY, USDC_DECIMALS)
        print(f"  trading 지갑에 {USDC_SUPPLY} USDC 지급")
        await mint_tokens(client, broker, stock_mint, broker.pubkey(), STOCK_SUPPLY, STOCK_DECIMALS)
        print(f"  broker 지갑에 {STOCK_SUPPLY} {CFG.stock_symbol} 지급")
        # 브로커 USDC 운용자본 — 익절 매도(오른 뒤 되사기) 대금을 지급할 재원 (USDC 민트 권한=trading)
        await mint_tokens(client, trading, usdc_mint, broker.pubkey(), BROKER_USDC_RESERVE, USDC_DECIMALS)
        print(f"  broker 지갑에 {BROKER_USDC_RESERVE} USDC 운용자본 지급")

    write_env(usdc_mint, stock_mint)
    print("\n완료. 이제 `python run_demo.py --live` 로 실제 devnet 매수를 실행할 수 있습니다.")
    print(f"Explorer: https://explorer.solana.com/address/{usdc_mint}?cluster=devnet")


if __name__ == "__main__":
    asyncio.run(main())
