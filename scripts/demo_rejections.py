"""거부 케이스 데모 — "한도 밖 결제는 돈이 나가기 전에 차단된다" 증빙용.

verification_checklist.md 의 필수 시나리오를 재현한다 (네트워크 불필요):
  1) 건별 한도 초과      → AP2 mandate 거부
  2) mandate 위변조      → 서명 검증 실패로 거부
  3) 결제 금액 부족      → x402 구조 검증 거부
  4) 미허용 종목         → AP2 mandate 거부

실행:  python scripts/demo_rejections.py
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

from solders.hash import Hash
from solders.keypair import Keypair

from payments.ap2_mandate import OpenPaymentMandate, PaymentAuthorizer, MandateError
from payments import x402_solana as x


def case(title: str) -> None:
    print("\n" + "─" * 60)
    print(f"  {title}")
    print("─" * 60)


def main() -> int:
    user = Keypair()
    broker = Keypair()
    usdc_mint = Keypair().pubkey()  # 데모용 민트 주소 (구조 검증엔 충분)
    failures = 0

    mandate = OpenPaymentMandate(
        user_pubkey=str(user.pubkey()),
        allowed_asset=str(usdc_mint),
        budget_total_usdc=Decimal(100),
        per_trade_max_usdc=Decimal(50),
        allowed_symbols=["tAAPL"],
    ).sign(user)
    auth = PaymentAuthorizer(mandate, agent_kp=user)
    print(f"정상 mandate 서명 검증: {mandate.verify()} (예산 100 / 건별 50 / tAAPL 만 허용)")

    case("케이스 1 · 건별 한도 초과 (60 > 50) → AP2 거부")
    try:
        auth.authorize("ord_over", "tAAPL", Decimal(60), str(broker.pubkey()))
        print("  [문제!] 거부되지 않았습니다")
        failures += 1
    except MandateError as e:
        print(f"  [정상 거부] {e}")

    case("케이스 2 · mandate 위변조 (예산 100 → 10000 조작) → 거부")
    tampered = OpenPaymentMandate(
        user_pubkey=str(user.pubkey()),
        allowed_asset=str(usdc_mint),
        budget_total_usdc=Decimal(10000),      # 조작된 값
        per_trade_max_usdc=Decimal(5000),
        allowed_symbols=["tAAPL"],
        signature=mandate.signature,           # 원본 서명 도용
        created_at=mandate.created_at,
    )
    print(f"  위조 mandate 서명 검증: {tampered.verify()}")
    try:
        PaymentAuthorizer(tampered, agent_kp=user)
        print("  [문제!] 거부되지 않았습니다")
        failures += 1
    except MandateError as e:
        print(f"  [정상 거부] {e}")

    case("케이스 3 · 결제 금액 부족 (30 요구, 5 지불) → x402 검증 거부")
    tx = x.build_transfer_transaction(
        payer=user, mint=usdc_mint, dest_owner=broker.pubkey(),
        amount=5_000_000, decimals=6, blockhash=Hash.default(),
    )
    ok, reason, amount = x.verify_payment(
        tx, expected_mint=usdc_mint, expected_dest_owner=broker.pubkey(),
        min_amount=30_000_000,
    )
    if ok:
        print("  [문제!] 통과되면 안 되는 결제가 통과했습니다")
        failures += 1
    else:
        print(f"  [정상 거부] {reason} (검출 금액 {amount} base units)")

    case("케이스 4 · 미허용 종목 (tTSLA) → AP2 거부")
    try:
        auth.authorize("ord_sym", "tTSLA", Decimal(10), str(broker.pubkey()))
        print("  [문제!] 거부되지 않았습니다")
        failures += 1
    except MandateError as e:
        print(f"  [정상 거부] {e}")

    print("\n" + ("모든 거부 케이스가 의도대로 차단되었습니다 — 돈이 나가기 전에 막힙니다."
                  if failures == 0 else f"경고: {failures}개 케이스가 차단되지 않았습니다!"))
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
