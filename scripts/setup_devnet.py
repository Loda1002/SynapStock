"""devnet 준비 스크립트 (네트워크 필요 — Cloud Shell 또는 로컬에서 실행).

수행:
  1) user/trading/broker 지갑 생성·저장 (secrets/, WALLET_DIR)
  2) trading·broker 지갑에 devnet SOL 에어드랍 (수수료용)
  3) 결제 통화(USDC) 준비 — 기본은 **Circle 공식 devnet USDC**(config 기본값)를 그대로 쓴다
  4) 테스트 주식 민트(tAAPL) 생성 → broker 지갑에 100 tAAPL 지급
  5) 사용한 민트 주소를 .env 에 기록

결제 통화에 대하여 (2026-07-27 정정):
  예전에는 USDC 까지 자체 발행했다 — 그런데 민트 권한이 **구매자(trading) 지갑**이었다.
  즉 "자기가 찍은 돈으로, 자기가 잔고를 채워준 상대에게" 지불한 증빙이 되어, 심사에서
  explorer 로 민트 권한만 확인하면 무너진다. 그래서 기본을 Circle 공식 민트로 바꿨다.
  파우셋: https://faucet.circle.com  (Solana Devnet 선택 → trading 지갑 주소 입력)

  주식 토큰은 그대로 자체 발행한다 — devnet 에 토큰화 주식이 실재하지 않기 때문이며,
  이건 숨길 필요 없는 정당한 제약이다(민트 상수·스왑 경로 2곳 교체로 실물 전환).

실행:
  python scripts/setup_devnet.py                 # Circle 공식 devnet USDC 사용(권장)
  python scripts/setup_devnet.py --self-mint-usdc  # 옛 동작(자체 발행) — 파우셋 없이 오프라인 실험용
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from decimal import Decimal, ROUND_DOWN

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
    get_associated_token_address, transfer_checked,
)
from spl.token.models import InitializeMint2Params, MintToParams, TransferCheckedParams

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts

from config import CFG
from payments import x402_solana as x

# Circle 공식 devnet USDC (developers.circle.com 의 USDC contract addresses 표).
# config.py 기본값과 같은 값이며, 여기서는 '설정이 이걸 가리키고 있는가' 검사에만 쓴다.
CIRCLE_DEVNET_USDC = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"

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


async def mint_authority(client, mint: Pubkey) -> str:
    """민트의 발행 권한자 주소. 조회 실패·미파싱이면 빈 문자열.

    '설정된 USDC 가 우리가 찍은 토큰인가'를 판별하는 데 쓴다 — 주소만 봐서는 알 수 없고,
    심사위원이 explorer 에서 확인하는 것도 정확히 이 필드다."""
    try:
        resp = await _rpc_retry(lambda: client.get_account_info_json_parsed(mint), label="민트조회")
        return str(resp.value.data.parsed["info"].get("mintAuthority") or "")
    except Exception:
        return ""


async def transfer_tokens(client, owner: Keypair, mint: Pubkey, to: Pubkey,
                          amount_ui: Decimal, decimals: int) -> str:
    """owner → to SPL 토큰 이체 (수취인 ATA 없으면 생성).

    Circle 공식 devnet USDC 처럼 **민트 권한이 없는 토큰**으로 브로커 운용자본을 채울 때
    쓴다 — mint_tokens 는 민트 권한이 필요해 쓸 수 없다."""
    src = get_associated_token_address(owner.pubkey(), mint)
    dst = get_associated_token_address(to, mint)
    ix_ata = create_idempotent_associated_token_account(
        payer=owner.pubkey(), owner=to, mint=mint)
    ix_send = transfer_checked(TransferCheckedParams(
        program_id=TOKEN_PROGRAM_ID, source=src, mint=mint, dest=dst,
        owner=owner.pubkey(), amount=int(amount_ui * (10 ** decimals)),
        decimals=decimals, signers=[],
    ))
    return await send(client, owner, [ix_ata, ix_send], [owner])


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


async def main(self_mint_usdc: bool = False) -> None:
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

        # ---- [2/4] 결제 통화(USDC) ----
        if self_mint_usdc:
            print("\n[2/4] 테스트 USDC 민트 생성 (--self-mint-usdc)…")
            print("  ⚠ 자체 발행입니다 — 민트 권한이 구매자(trading) 지갑입니다. explorer 에서")
            print("     민트 권한만 확인하면 '자기가 찍은 돈으로 지불'로 읽힙니다. 심사 증빙에는 쓰지 마세요.")
            usdc_mint = await create_mint(client, trading, trading.pubkey(), USDC_DECIMALS)
            print(f"  USDC_MINT = {usdc_mint}")
        else:
            usdc_mint = Pubkey.from_string(CFG.usdc_mint)
            print(f"\n[2/4] 결제 통화 = 설정된 USDC 민트 (이번 실행에서 발행하지 않음)")
            print(f"  USDC_MINT = {usdc_mint}")

            # ★ 설정값이 '우리가 예전에 찍은 자체 토큰' 을 그대로 가리키고 있을 수 있다
            #   (.env 는 예전 실행이 덮어써 둔 값이다). 그 상태로 진행하면 이번 수정이
            #   무의미해지고 오히려 '자체 발행 안 함' 이라는 오독만 남는다 → 발행권한을 조회해 막는다.
            authority = await mint_authority(client, usdc_mint)
            ours = {str(trading.pubkey()), str(broker.pubkey()), str(user.pubkey())}
            if authority and authority in ours:
                print(f"\n[중단] 설정된 USDC_MINT 는 우리 지갑이 발행한 자체 토큰입니다.")
                print(f"  발행 권한: {authority} (우리 지갑)")
                print("  이 상태의 증빙은 '자기가 찍은 돈으로, 자기가 잔고를 채워준 상대에게 지불' 로 읽힙니다.")
                print("  .env 의 USDC_MINT 를 Circle 공식 devnet USDC 로 바꾼 뒤 다시 실행하세요:")
                print(f"    USDC_MINT={CIRCLE_DEVNET_USDC}")
                print("  (자체 발행으로 계속하려면 --self-mint-usdc — 심사 증빙으로는 쓸 수 없습니다.)")
                return
            if str(usdc_mint) != CIRCLE_DEVNET_USDC:
                print(f"  ⚠ Circle 공식 민트({CIRCLE_DEVNET_USDC})가 아닙니다."
                      f" 발행 권한: {authority or '조회 실패'}")

            have_base = await x.get_token_balance_base(client, trading.pubkey(), usdc_mint)
            have = Decimal(have_base) / Decimal(10 ** USDC_DECIMALS)
            print(f"  trading 지갑 USDC 잔액: {have}")
            if have <= 0:
                print("\n[중단] trading 지갑에 USDC 가 없습니다. 민트 권한이 없으므로 발행할 수 없습니다.")
                print("  파우셋에서 충전한 뒤 이 스크립트를 다시 실행하세요:")
                print("    https://faucet.circle.com  → Solana Devnet 선택 → 아래 주소 입력")
                print(f"    {trading.pubkey()}")
                print("  (파우셋 없이 진행하려면 --self-mint-usdc — 단 심사 증빙으로는 쓸 수 없습니다.)")
                return

        # ---- [3/4] 주식 토큰 (항상 자체 발행 — devnet 에 토큰화 주식이 실재하지 않는다) ----
        print("\n[3/4] 테스트 주식 민트 생성…")
        stock_mint = await create_mint(client, broker, broker.pubkey(), STOCK_DECIMALS)
        print(f"  STOCK_MINT = {stock_mint}  ({CFG.stock_symbol})")

        # ---- [4/4] 초기 잔액 ----
        print("\n[4/4] 초기 잔액 지급…")
        await mint_tokens(client, broker, stock_mint, broker.pubkey(), STOCK_SUPPLY, STOCK_DECIMALS)
        print(f"  broker 지갑에 {STOCK_SUPPLY} {CFG.stock_symbol} 지급")

        # 브로커 USDC 운용자본 — 익절 매도(오른 뒤 되사기) 대금을 지급할 재원. 없으면 매수로
        # 받은 USDC 만으로는 평가이익만큼을 지급하지 못해 매도 정산이 실패한다.
        if self_mint_usdc:
            await mint_tokens(client, trading, usdc_mint, trading.pubkey(), USDC_SUPPLY, USDC_DECIMALS)
            print(f"  trading 지갑에 {USDC_SUPPLY} USDC 지급")
            await mint_tokens(client, trading, usdc_mint, broker.pubkey(), BROKER_USDC_RESERVE,
                              USDC_DECIMALS)
            print(f"  broker 지갑에 {BROKER_USDC_RESERVE} USDC 운용자본 지급")
        else:
            # 민트 권한이 없다 → 파우셋으로 받은 잔액을 나눈다. Circle 파우셋은 주소당
            # 2시간마다 20 USDC 라 배분이 빡빡하다: 브로커에 너무 많이 묶으면 정작
            # 구매자가 살 돈이 없다. 총액의 1/3 만(상한 BROKER_USDC_RESERVE) 넘긴다.
            # '부족분만 top-up' 이라 재실행해도 매번 또 보내지 않는다(멱등).
            broker_base = await x.get_token_balance_base(client, broker.pubkey(), usdc_mint)
            broker_have = Decimal(broker_base) / Decimal(10 ** USDC_DECIMALS)
            target = min((have + broker_have) / 3, Decimal(BROKER_USDC_RESERVE))
            short = (target - broker_have).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            if short <= 0:
                print(f"  broker 운용자본 {broker_have} USDC — 목표 {target} 충족, 이체 생략")
            else:
                sig = await transfer_tokens(client, trading, usdc_mint, broker.pubkey(),
                                            short, USDC_DECIMALS)
                print(f"  broker 지갑에 {short} USDC 운용자본 이체({sig[:16]}…)"
                      f" → 브로커 보유 {broker_have + short}")
            left = have - max(short, Decimal(0))
            print(f"  trading 지갑 잔여: {left} USDC")
            # 지출 한도를 잔여 이하로 맞추라는 안내. 세션 예산은 BUDGET_USDC 이고
            # MAX_BUDGET_USDC 는 update_limits 의 상한 검사에만 쓰인다(세션 시작을 clamp 하지 않음).
            print(f"  ⚠ 파우셋 한도가 낮습니다. 아래를 잔여({left}) 이하로 맞추세요:")
            print(f"     .env  BUDGET_USDC · PER_TRADE_MAX_USDC")
            # 웹 세션의 1회 매수는 예산 대비 비율(web/engine.SPEND_PCT)이라 예산만 맞추면
            # 따라온다. 그 상수를 여기서 읽지는 않는다 — web.engine 을 임포트하면 엔진·에이전트
            # 의존이 통째로 딸려 와서, 네트워크 준비 스크립트가 앱 전체에 묶인다.
            print(f"     웹 세션의 1회 매수는 예산 대비 비율이라 예산만 맞추면 따라옵니다.")
            print(f"     CLI 는 `python run_demo.py --live --spend <USDC>` 로 지정합니다.")

    write_env(usdc_mint, stock_mint)
    print("\n완료. 이제 `python run_demo.py --live` 로 실제 devnet 매수를 실행할 수 있습니다.")
    print(f"Explorer: https://explorer.solana.com/address/{usdc_mint}?cluster=devnet")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="devnet 지갑·민트 준비")
    ap.add_argument("--self-mint-usdc", action="store_true",
                    help="USDC 도 자체 발행한다(옛 동작). 파우셋 없이 실험할 때만 — "
                         "민트 권한이 구매자 지갑이라 심사 증빙으로는 쓸 수 없다.")
    args = ap.parse_args()
    asyncio.run(main(self_mint_usdc=args.self_mint_usdc))
