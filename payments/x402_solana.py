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
import asyncio
import base64
import json
import os
from typing import Optional, Tuple

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.instruction import Instruction, AccountMeta
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

# SPL Memo 프로그램 — 주문번호를 온체인 로그에 박아 대사(reconciliation) 키로 쓴다.
MEMO_PROGRAM_ID = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")
# 결제 메모 접두 규약: "AT1:{order_id}:{mandate_sig8}" — explorer 에서 육안 대사 + 리플레이 방어
MEMO_PREFIX = "AT1"


def build_memo_instruction(memo: str, signer: Pubkey) -> Instruction:
    """SPL Memo instruction — signer 를 서명 계정으로 넣어 메모를 그 지갑에 귀속시킨다."""
    return Instruction(
        program_id=MEMO_PROGRAM_ID,
        data=memo.encode("utf-8"),
        accounts=[AccountMeta(pubkey=signer, is_signer=True, is_writable=False)],
    )


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
    memo: Optional[str] = None,
) -> Transaction:
    """payer → dest_owner 로 `amount`(base units) SPL 토큰 전송 트랜잭션을 생성·서명한다.

    ensure_dest_ata=True 이면 수취인 ATA 가 없을 경우를 대비해 생성 명령을 앞에 넣는다
    (idempotent — 이미 있으면 무시). payer 가 수수료/생성비를 부담.

    memo 가 주어지면 SPL Memo instruction 을 최앞단에 넣어 주문번호를 온체인에 박는다
    (대사 키 + 서명 dedupe 로 리플레이 방어 — 같은 주문이라도 tx 가 유일해진다).
    """
    src_ata = get_associated_token_address(payer.pubkey(), mint)
    dst_ata = get_associated_token_address(dest_owner, mint)

    instructions = []
    if memo:
        instructions.append(build_memo_instruction(memo, payer.pubkey()))
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
    expected_amount: int,
    expected_payer: Optional[Pubkey] = None,
    expected_order_id: Optional[str] = None,
) -> Tuple[bool, str, int]:
    """제출된 트랜잭션이 올바른 결제인지 구조적으로 검증한다 (오프라인 가능).

    확인 항목:
      - SPL Token 프로그램의 TransferChecked instruction 존재
      - 수취 ATA == expected_dest_owner 의 expected_mint ATA
      - 금액 == expected_amount  (exact 스킴 — 초과지불도 부족도 거부. 결함 D)
      - (옵션) 서명자/지불자 == expected_payer
      - (옵션) Memo 에 주문번호(AT1:{order_id})가 박혀 있는지 — 대사 키 (결함 E)
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

    # Memo 대사 — expected_order_id 가 주어지면 온체인 Memo 에 그 주문번호가 박혀 있어야 한다
    if expected_order_id is not None:
        needle = f"{MEMO_PREFIX}:{expected_order_id}"
        found = False
        for ix in msg.instructions:
            if account_keys[ix.program_id_index] != MEMO_PROGRAM_ID:
                continue
            text = bytes(ix.data).decode("utf-8", "replace")
            if text.startswith(needle):
                found = True
                break
        if not found:
            return False, f"주문번호 Memo 불일치/부재: {expected_order_id}", 0

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
        if amount != expected_amount:
            # exact 스킴: 부족도 초과도 안 된다 (한도 안쪽 초과지불 위조 차단)
            return False, f"금액 불일치(exact): {amount} != {expected_amount}", amount
        if expected_payer is not None and owner != expected_payer:
            return False, f"지불자 불일치: {owner}", amount
        return True, "검증 통과", amount

    return False, "TransferChecked instruction 없음", 0


# ---------- 네트워크 (devnet RPC 필요) ----------

def _exc_chain_text(e: Exception) -> str:
    """예외 체인(__cause__/__context__ 래핑 포함)의 타입명·메시지를 이어붙인다."""
    cur, parts = e, []
    for _ in range(6):
        if cur is None:
            break
        parts.append(type(cur).__name__)
        parts.append(str(cur))
        cur = cur.__cause__ or cur.__context__
    return " ".join(parts)


def _is_transient(e: Exception) -> bool:
    """예외 체인(래핑된 원인 포함)에서 일시적 RPC 오류 신호를 찾는다.
    solana-py 는 429 를 SolanaRpcException 으로 감싸 str() 에 '429' 가 안 보이므로
    __cause__/__context__ 를 따라가며 확인한다."""
    text = _exc_chain_text(e)
    return any(s in text for s in ("429", "Too Many", "Internal error",
                                   "SolanaRpcException", "timed out", "Timeout"))


def _is_account_not_found(e: Exception) -> bool:
    """ATA 미존재(=진짜 잔액 0) 신호만 True. 429/타임아웃/연결 실패 등 '불명'은 False.

    토큰 계정이 없으면 RPC 는 -32602 'Invalid param: could not find account' 로 응답한다.
    이 신호만 0 으로 취급하고, 그 외 예외는 상위로 전파해 기준선(before) 오염을 막는다(BUG-01)."""
    text = _exc_chain_text(e).lower()
    return ("could not find account" in text
            or "-32602" in text
            or "accountnotfound" in text
            or "invalid param" in text)


async def rpc_retry(factory, retries: int = 6, label: str = ""):
    """공용 devnet RPC(api.devnet.solana.com)는 요청이 몰리면 429 를 준다. 429·일시적
    오류를 지수 백오프로 재시도한다 — 조회·confirm 은 idempotent 하고 재제출도 동일
    blockhash·서명이라 중복 tx 가 생기지 않아 안전하다. (setup_devnet 과 동일 전략)"""
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


async def get_client(rpc_url: str):
    """Confirmed 커밋먼트로 통일 — Finalized 기본값이면 방금 에어드랍/전송된
    자금이 preflight 시뮬레이션에 안 보여 AccountNotFound 가 난다."""
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.commitment import Confirmed
    return AsyncClient(rpc_url, commitment=Confirmed)


async def get_latest_blockhash(client) -> Hash:
    resp = await rpc_retry(lambda: client.get_latest_blockhash(), label="blockhash")
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
    resp = await rpc_retry(lambda: client.get_balance(pubkey), label="잔액")
    return resp.value / 1_000_000_000


async def get_token_balance_ui(client, owner: Pubkey, mint: Pubkey) -> str:
    """소유자 ATA 의 토큰 잔액(UI 단위 문자열). ATA 미존재 시 '0'."""
    ata = get_associated_token_address(owner, mint)
    try:
        resp = await rpc_retry(lambda: client.get_token_account_balance(ata), label="토큰잔액")
        return resp.value.ui_amount_string
    except Exception:
        return "0"


async def get_token_balance_base(client, owner: Pubkey, mint: Pubkey) -> int:
    """소유자 ATA 의 토큰 잔액(base units 정수). ATA 미존재(진짜 0)일 때만 0 을 반환한다.

    Guard.check_delivery 의 온체인 재조회 + 정산 전 기준선(before) 읽기용 — 정산 전후 잔액
    증가분을 정수로 비교한다. 429/타임아웃/연결 실패 등 '불명' 오류를 0 으로 삼키면 기준선이
    오염돼 미배송이 도착으로 오탐되므로(BUG-01), 계정-미존재가 아닌 예외는 상위로 전파한다."""
    ata = get_associated_token_address(owner, mint)
    try:
        resp = await rpc_retry(lambda: client.get_token_account_balance(ata), label="토큰잔액")
        return int(resp.value.amount)
    except Exception as e:
        if _is_account_not_found(e):
            return 0        # ATA 미존재 = 진짜 잔액 0
        raise               # 불명 실패 → 전파(호출측이 pending/보류로 처리)


async def submit_and_confirm(client, tx: Transaction) -> Tuple[str, bool]:
    """트랜잭션을 클러스터에 제출하고 컨펌될 때까지 대기. (서명 문자열, 성공여부).

    preflight_commitment 를 Confirmed 로 명시해야 한다 — 생략하면 preflight 가
    Finalized 뱅크(수십 슬롯 과거)에서 시뮬레이션돼 방금 받은 confirmed 블록해시를
    "Blockhash not found" 로 거부한다(localnet 에서 재현·확인됨).
    """
    from solana.rpc.commitment import Confirmed
    from solana.rpc.types import TxOpts
    resp = await rpc_retry(lambda: client.send_raw_transaction(
        bytes(tx), opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed)), label="제출")
    sig = resp.value
    conf = await rpc_retry(lambda: client.confirm_transaction(sig, commitment=Confirmed), label="confirm")
    ok = True
    try:
        ok = conf.value[0].err is None
    except Exception:
        pass
    return str(sig), ok
