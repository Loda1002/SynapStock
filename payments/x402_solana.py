"""x402 결제 코어 (Solana / SPL 토큰).

핵심 책임:
  - 결제 트랜잭션 생성·서명 (구매자)
  - x402 payload(base64) 인코딩/디코딩
  - 제출된 결제의 구조 검증 (판매자) — 수취인/금액/민트/프로그램 확인
  - devnet 브로드캐스트 & 컨펌 (네트워크 필요)

오프라인(네트워크 없이)에서도 생성·서명·검증 로직은 그대로 동작한다.
브로드캐스트(submit_and_confirm)와 airdrop 만 실제 RPC가 필요하다.
"""
from __future__ import annotations
import base64
import json
import os
from typing import Optional, Tuple

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.message import Message
from solders.transaction import Transaction

from spl.token.instructions import (
    transfer_checked,
    get_associated_token_address,
    create_idempotent_associated_token_account,
)
from spl.token.models import TransferCheckedParams
from spl.token.constants import TOKEN_PROGRAM_ID

# SPL Token 프로그램의 TransferChecked instruction 식별자
_TRANSFER_CHECKED_TAG = 12


# ---------- 지갑 ----------

def new_keypair() -> Keypair:
    return Keypair()


def save_keypair(kp: Keypair, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # solders Keypair 는 bytes(kp) 로 64바이트 시크릿을 직렬화
    with open(path, "w") as f:
        json.dump(list(bytes(kp)), f)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_keypair(path: str) -> Keypair:
    with open(path) as f:
        return Keypair.from_bytes(bytes(json.load(f)))


def token_account(owner: Pubkey, mint: Pubkey) -> Pubkey:
    """소유자의 특정 민트에 대한 Associated Token Account 주소."""
    return get_associated_token_address(owner, mint)


# ---------- 결제 트랜잭션 생성 (구매자) ----------

def build_transfer_transaction(
    payer: Keypair,
    mint: Pubkey,
    dest_owner: Pubkey,
    amount: int,
    decimals: int,
    blockhash: Hash,
    ensure_dest_ata: bool = True,
) -> Transaction:
    """payer → dest_owner 로 `amount`(base units) SPL 토큰 전송 트랜잭션을 생성·서명한다.

    ensure_dest_ata=True 이면 수취인 ATA 가 없을 경우를 대비해 생성 명령을 앞에 넣는다
    (idempotent — 이미 있으면 무시). payer 가 수수료/생성비를 부담.
    """
    src_ata = get_associated_token_address(payer.pubkey(), mint)
    dst_ata = get_associated_token_address(dest_owner, mint)

    instructions = []
    if ensure_dest_ata:
        instructions.append(
            create_idempotent_associated_token_account(
                payer=payer.pubkey(), owner=dest_owner, mint=mint
            )
        )
    instructions.append(
        transfer_checked(
            TransferCheckedParams(
                program_id=TOKEN_PROGRAM_ID,
                source=src_ata,
                mint=mint,
                dest=dst_ata,
                owner=payer.pubkey(),
                amount=amount,
                decimals=decimals,
                signers=[],
            )
        )
    )
    msg = Message.new_with_blockhash(instructions, payer.pubkey(), blockhash)
    return Transaction([payer], msg, blockhash)


def signature_str(tx: Transaction) -> str:
    return str(tx.signatures[0])


# ---------- x402 payload 인코딩/디코딩 ----------

def encode_payload(tx: Transaction) -> str:
    """서명 트랜잭션 → base64 문자열 (x402 payload.serializedTransaction)."""
    return base64.b64encode(bytes(tx)).decode()


def decode_payload(serialized_b64: str) -> Transaction:
    return Transaction.from_bytes(base64.b64decode(serialized_b64))


# ---------- 결제 검증 (판매자) ----------

def verify_payment(
    tx: Transaction,
    expected_mint: Pubkey,
    expected_dest_owner: Pubkey,
    min_amount: int,
    expected_payer: Optional[Pubkey] = None,
) -> Tuple[bool, str, int]:
    """제출된 트랜잭션이 올바른 결제인지 구조적으로 검증한다 (오프라인 가능).

    확인 항목:
      - SPL Token 프로그램의 TransferChecked instruction 존재
      - 수취 ATA == expected_dest_owner 의 expected_mint ATA
      - 금액 >= min_amount
      - (옵션) 서명자/지불자 == expected_payer
      - 서명 유효성

    반환: (통과여부, 사유, 검출금액)
    """
    msg = tx.message
    account_keys = list(msg.account_keys)

    # 서명 유효성 (오프라인 검증)
    try:
        results = tx.verify_with_results()
        if hasattr(results, "__iter__") and not all(bool(r) for r in results):
            return False, "서명 검증 실패", 0
    except Exception:
        # 일부 버전은 verify() 사용
        try:
            tx.verify()
        except Exception as e:  # noqa
            return False, f"서명 검증 실패: {e}", 0

    expected_dst_ata = get_associated_token_address(expected_dest_owner, expected_mint)

    for ix in msg.instructions:
        program_id = account_keys[ix.program_id_index]
        if program_id != TOKEN_PROGRAM_ID:
            continue
        data = bytes(ix.data)
        if not data or data[0] != _TRANSFER_CHECKED_TAG:
            continue
        # TransferChecked 데이터: [tag(1)][amount u64 LE(8)][decimals(1)]
        amount = int.from_bytes(data[1:9], "little")
        acct_idx = list(ix.accounts)
        # accounts 순서: [source, mint, dest, owner]
        if len(acct_idx) < 4:
            continue
        dest = account_keys[acct_idx[2]]
        mint = account_keys[acct_idx[1]]
        owner = account_keys[acct_idx[3]]

        if mint != expected_mint:
            return False, f"민트 불일치: {mint}", amount
        if dest != expected_dst_ata:
            return False, f"수취 계정 불일치: {dest}", amount
        if amount < min_amount:
            return False, f"금액 부족: {amount} < {min_amount}", amount
        if expected_payer is not None and owner != expected_payer:
            return False, f"지불자 불일치: {owner}", amount
        return True, "검증 통과", amount

    return False, "TransferChecked instruction 없음", 0


# ---------- 네트워크 (devnet RPC 필요) ----------

async def get_client(rpc_url: str):
    """Confirmed 커밋먼트로 통일 — Finalized 기본값이면 방금 에어드랍/전송된
    자금이 preflight 시뮬레이션에 안 보여 AccountNotFound 가 난다."""
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.commitment import Confirmed
    return AsyncClient(rpc_url, commitment=Confirmed)


async def get_latest_blockhash(client) -> Hash:
    resp = await client.get_latest_blockhash()
    return resp.value.blockhash


async def request_airdrop(client, pubkey: Pubkey, sol: float = 1.0) -> str:
    from solana.rpc.commitment import Confirmed
    lamports = int(sol * 1_000_000_000)
    resp = await client.request_airdrop(pubkey, lamports)
    sig = resp.value
    await client.confirm_transaction(sig, commitment=Confirmed)
    return str(sig)


async def get_sol_balance(client, pubkey: Pubkey) -> float:
    """SOL 잔액 (수수료 확인용)."""
    resp = await client.get_balance(pubkey)
    return resp.value / 1_000_000_000


async def get_token_balance_ui(client, owner: Pubkey, mint: Pubkey) -> str:
    """소유자 ATA 의 토큰 잔액(UI 단위 문자열). ATA 미존재 시 '0'."""
    ata = get_associated_token_address(owner, mint)
    try:
        resp = await client.get_token_account_balance(ata)
        return resp.value.ui_amount_string
    except Exception:
        return "0"


async def submit_and_confirm(client, tx: Transaction) -> Tuple[str, bool]:
    """트랜잭션을 클러스터에 제출하고 컨펌될 때까지 대기. (서명 문자열, 성공여부).

    preflight_commitment 를 Confirmed 로 명시해야 한다 — 생략하면 preflight 가
    Finalized 뱅크(수십 슬롯 과거)에서 시뮬레이션돼 방금 받은 confirmed 블록해시를
    "Blockhash not found" 로 거부한다(localnet 에서 재현·확인됨).
    """
    from solana.rpc.commitment import Confirmed
    from solana.rpc.types import TxOpts
    resp = await client.send_raw_transaction(
        bytes(tx), opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed))
    sig = resp.value
    conf = await client.confirm_transaction(sig, commitment=Confirmed)
    ok = True
    try:
        ok = conf.value[0].err is None
    except Exception:
        pass
    return str(sig), ok
