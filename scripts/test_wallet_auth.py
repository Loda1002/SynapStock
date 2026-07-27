"""지갑 로그인(web/auth.py) 단위 테스트 — 서명 위조·재사용·변조를 실제로 막는지.

실행: python -m scripts.test_wallet_auth   (네트워크·지갑 확장 불필요)

브라우저의 Phantom 을 파이썬 Keypair 로 대신한다 — 둘 다 같은 ed25519 서명이므로
서버 검증 경로는 완전히 동일하다. 확인할 수 없는 것은 "Phantom 이 우리가 준 문자열을
그대로 서명하는가"뿐이고, 그건 사용자가 브라우저에서 1회 확인한다.
"""
from __future__ import annotations

import base64
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from solders.keypair import Keypair  # noqa: E402

from web.auth import (  # noqa: E402
    NONCE_TTL_SEC, SESSION_TTL_SEC, AuthError, WalletAuth,
)

DOMAIN = "402guard.example"
URI = "https://402guard.example"

ok = 0
fail = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    """통과·실패 표기는 다른 test_*.py 및 증거 수집기(collect_evidence.py 의 [OK/[FAIL 집계)와
    같은 형식을 쓴다."""
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK  ] {label}")
    else:
        fail += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def sign_b64(kp: Keypair, message: str) -> str:
    """지갑이 하는 일 — 메시지 UTF-8 바이트에 ed25519 서명 후 base64 로 실어 보낸다."""
    return base64.b64encode(bytes(kp.sign_message(message.encode("utf-8")))).decode()


def fresh() -> tuple[WalletAuth, Keypair]:
    return WalletAuth(chain_id="devnet"), Keypair()


def blocked(fn, *args) -> tuple[bool, str]:
    """AuthError 로 막히면 (True, 사유). 통과해 버리면 (False, "")."""
    try:
        fn(*args)
        return False, ""
    except AuthError as e:
        return True, str(e)


# ---------------------------------------------------------------- 1. 정상 흐름

def test_happy_path() -> None:
    print("\n[1] 정상 연결 — 챌린지 발급 → 서명 → 세션")
    auth, kp = fresh()
    pk = str(kp.pubkey())

    ch = auth.challenge(pk, DOMAIN, URI)
    check("챌린지에 nonce·message·유효시간이 있다",
          bool(ch["nonce"]) and bool(ch["message"]) and ch["expires_in"] == NONCE_TTL_SEC)
    check("메시지 첫 줄이 SIWS 서식이다",
          ch["message"].startswith(f"{DOMAIN} wants you to sign in with your Solana account:"))
    check("메시지에 서명자 주소가 박혀 있다", pk in ch["message"])
    check("메시지에 nonce 가 박혀 있다", f"Nonce: {ch['nonce']}" in ch["message"])
    check("체인 표기가 들어 있다", "Chain ID: devnet" in ch["message"])
    check("자금 이동이 아님을 사용자에게 고지한다", "자금이 이동하지 않습니다" in ch["message"])

    token = auth.verify(pk, ch["message"], sign_b64(kp, ch["message"]))
    check("서명 검증 후 세션 토큰이 발급된다", bool(token) and len(token) > 20)

    s = auth.session(token)
    check("세션이 서명한 지갑을 가리킨다", s is not None and s["pubkey"] == pk)
    check("세션 만료가 12시간 뒤다",
          s is not None and abs((s["expires_at"] - s["created_at"]) - SESSION_TTL_SEC) < 2)

    check("nonce 는 1회용 — 같은 챌린지가 남아 있지 않다", len(auth._challenges) == 0)


# ------------------------------------------------------- 2. 위조·변조·재사용 차단

def test_forged_signature() -> None:
    print("\n[2] 남의 지갑을 사칭 — 다른 키로 서명")
    auth, victim = fresh()
    attacker = Keypair()
    pk = str(victim.pubkey())
    ch = auth.challenge(pk, DOMAIN, URI)

    # 공격자가 피해자 주소를 주장하면서 자기 키로 서명한다.
    hit, why = blocked(auth.verify, pk, ch["message"], sign_b64(attacker, ch["message"]))
    check("다른 키의 서명은 거부된다", hit, "통과해 버림")
    check("사유가 서명 불일치임을 밝힌다", "서명" in why, why)


def test_tampered_message() -> None:
    print("\n[3] 메시지 변조 — 한 글자만 바꿔도 깨진다")
    auth, kp = fresh()
    pk = str(kp.pubkey())
    ch = auth.challenge(pk, DOMAIN, URI)

    # 지갑이 서명한 것은 '변조본'이라 서명 자체는 유효하다. 서버가 정본과 다름을 잡아야 한다.
    tampered = ch["message"].replace("자금이 이동하지 않습니다", "자금이 이동합니다")
    check("변조본이 원문과 실제로 다르다", tampered != ch["message"])
    hit, why = blocked(auth.verify, pk, tampered, sign_b64(kp, tampered))
    check("서버 발급 원문과 다르면 거부된다(서명 자체는 유효해도)", hit, "통과해 버림")
    check("사유가 원문 불일치를 밝힌다", "원문" in why or "만료" in why, why)


def test_replay() -> None:
    print("\n[4] 재사용(리플레이) — 같은 서명을 두 번")
    auth, kp = fresh()
    pk = str(kp.pubkey())
    ch = auth.challenge(pk, DOMAIN, URI)
    sig = sign_b64(kp, ch["message"])

    check("1회차는 통과한다", bool(auth.verify(pk, ch["message"], sig)))
    hit, why = blocked(auth.verify, pk, ch["message"], sig)
    check("2회차(같은 nonce 재사용)는 거부된다", hit, "재사용이 통과함")
    check("사유가 만료·사용됨을 밝힌다", "사용" in why or "만료" in why, why)


def test_pubkey_swap() -> None:
    print("\n[5] 주소 바꿔치기 — 챌린지를 받은 지갑과 다른 지갑이 제출")
    auth, a = fresh()
    b = Keypair()
    ch = auth.challenge(str(a.pubkey()), DOMAIN, URI)
    hit, why = blocked(auth.verify, str(b.pubkey()), ch["message"], sign_b64(b, ch["message"]))
    check("챌린지 주인이 아닌 지갑은 거부된다", hit, "통과해 버림")
    check("사유가 지갑 불일치를 밝힌다", "지갑" in why, why)


def test_expiry() -> None:
    print("\n[6] 만료 — 챌린지·세션 시간 초과")
    auth, kp = fresh()
    pk = str(kp.pubkey())
    clock = {"t": 1_000_000.0}
    auth._now = lambda: clock["t"]

    ch = auth.challenge(pk, DOMAIN, URI)
    clock["t"] += NONCE_TTL_SEC + 1
    hit, _ = blocked(auth.verify, pk, ch["message"], sign_b64(kp, ch["message"]))
    check("유효시간이 지난 챌린지는 거부된다", hit, "만료본이 통과함")

    ch2 = auth.challenge(pk, DOMAIN, URI)
    token = auth.verify(pk, ch2["message"], sign_b64(kp, ch2["message"]))
    check("만료 직전 세션은 살아 있다", auth.session(token) is not None)
    clock["t"] += SESSION_TTL_SEC + 1
    check("만료된 세션은 None 이다", auth.session(token) is None)
    check("만료된 세션은 저장소에서도 지워진다", token not in auth._sessions)


# ---------------------------------------------------------- 3. 입력 형식 방어

def test_malformed_inputs() -> None:
    print("\n[7] 잘못된 입력 — 형식 검사")
    auth, kp = fresh()
    pk = str(kp.pubkey())

    hit, _ = blocked(auth.challenge, "not-a-solana-address", DOMAIN, URI)
    check("주소 형식이 아니면 챌린지를 안 준다", hit)

    ch = auth.challenge(pk, DOMAIN, URI)
    hit, why = blocked(auth.verify, pk, ch["message"], "!!!not base64!!!")
    check("base64 가 아닌 서명은 거부된다", hit, why)

    ch = auth.challenge(pk, DOMAIN, URI)
    short = base64.b64encode(b"\x01" * 32).decode()
    hit, why = blocked(auth.verify, pk, ch["message"], short)
    check("64바이트가 아닌 서명은 거부된다", hit, why)
    check("사유가 길이를 밝힌다", "길이" in why, why)

    hit, why = blocked(auth.verify, pk, "Nonce 줄이 없는 아무 문장", sign_b64(kp, "x"))
    check("Nonce 줄이 없는 메시지는 거부된다", hit, why)

    hit, _ = blocked(auth.verify, pk, "Nonce: deadbeefdeadbeef", sign_b64(kp, "x"))
    check("발급한 적 없는 nonce 는 거부된다", hit)


def test_sessions_and_logout() -> None:
    print("\n[8] 세션 관리 — 독립성·로그아웃·청소")
    auth, a = fresh()
    b = Keypair()

    def login(kp: Keypair) -> str:
        pk = str(kp.pubkey())
        ch = auth.challenge(pk, DOMAIN, URI)
        return auth.verify(pk, ch["message"], sign_b64(kp, ch["message"]))

    ta, tb = login(a), login(b)
    check("두 지갑의 세션 토큰이 다르다", ta != tb)
    check("각 세션이 자기 지갑을 가리킨다",
          auth.session(ta)["pubkey"] == str(a.pubkey())
          and auth.session(tb)["pubkey"] == str(b.pubkey()))

    check("로그아웃은 True 를 돌려준다", auth.logout(ta) is True)
    check("로그아웃한 세션은 무효다", auth.session(ta) is None)
    check("다른 세션은 살아 있다", auth.session(tb) is not None)
    check("이미 로그아웃한 토큰은 False", auth.logout(ta) is False)
    check("빈 토큰은 세션이 없다", auth.session(None) is None and auth.session("") is None)
    check("모르는 토큰은 세션이 없다", auth.session("aaaa") is None)


def test_challenge_flood() -> None:
    print("\n[9] 챌린지 무한 발급 — 메모리 상한")
    auth, kp = fresh()
    pk = str(kp.pubkey())
    for _ in range(400):
        auth.challenge(pk, DOMAIN, URI)
    check("미사용 챌린지가 상한 안에서 관리된다", len(auth._challenges) <= 300,
          f"{len(auth._challenges)}건 적재")

    # 상한에 부딪힌 뒤에도 새로 받은 챌린지로는 반드시 로그인이 된다.
    ch = auth.challenge(pk, DOMAIN, URI)
    check("가장 최근 챌린지로는 정상 로그인된다",
          bool(auth.verify(pk, ch["message"], sign_b64(kp, ch["message"]))))


def main() -> int:
    print("=" * 68)
    print(" 지갑 로그인 검증 — 서명 위조·변조·재사용 차단 (web/auth.py)")
    print("=" * 68)
    test_happy_path()
    test_forged_signature()
    test_tampered_message()
    test_replay()
    test_pubkey_swap()
    test_expiry()
    test_malformed_inputs()
    test_sessions_and_logout()
    test_challenge_flood()
    print("\n" + "-" * 68)
    print(f" 결과: 통과 {ok} · 실패 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
