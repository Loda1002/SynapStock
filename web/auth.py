"""지갑 연결 = 로그인 (Phantom / Solana Wallet).

이메일·비밀번호 계정을 만들지 않는다. 사용자는 **자기 지갑으로 소유를 증명**하고, 그게
곧 로그인이다. 이 제품이 self-custody 를 주장하는 이상 계정 체계도 self-custody 여야
말이 맞는다 — 비밀번호를 우리가 보관하면 "우리는 당신 자금에 손대지 않는다"는 문장과
어긋난다.

흐름(2회 왕복):

  1) POST /api/auth/challenge {pubkey}
     서버가 **메시지 전문을 만들어** 내려준다. 클라이언트는 이 문자열을 한 글자도 바꾸지
     않고 그대로 Phantom 에 서명 요청한다.
  2) POST /api/auth/verify {pubkey, message, signature}
     서버는 ①발급했던 메시지와 **바이트 단위로 같은지** ②그 바이트에 대한 ed25519 서명이
     그 pubkey 로 검증되는지 ③nonce 가 미사용·미만료인지를 본다. 통과하면 세션 토큰을
     httpOnly 쿠키로 심는다.

**왜 SIWS `signIn()` 이 아니라 `signMessage` 인가.** Phantom 의 `signIn()`(Sign In With
Solana)은 지갑이 EIP-4361 ABNF 서식으로 메시지를 **스스로 조립**하므로, 서버는 그 서식을
재구성해 파싱·대조해야 한다(공식 헬퍼 `verifySignIn` 은 JS 전용). 우리는 파이썬이고, 서버가
메시지를 직접 만들어 내려주면 검증이 **문자열 완전 일치 + 서명 검증** 두 줄로 끝난다 —
파서를 안 쓰므로 파서 취약점도 없다. 대신 메시지 본문은 SIWS 서식으로 써서 Phantom
팝업에는 표준 로그인처럼 보이게 한다.

**보관 위치**: nonce·세션 모두 프로세스 메모리다. 엔진이 전역 싱글턴이고 배포가
`--max-instances 1` 이라 인스턴스 간 공유 문제가 없다. 재시작하면 로그아웃된다(재연결
1회로 복구). 영속 세션이 필요해지면 web/store.py(Firestore)로 옮기면 된다.

**이 서명이 하지 않는 일**: 자금 이동 권한을 주지 않는다. 서명 대상은 사람이 읽는
로그인 문장이고 트랜잭션이 아니다. 거래 권한은 별도로 AP2 mandate 가 정한다.
"""
from __future__ import annotations

import base64
import binascii
import secrets
import time
from dataclasses import dataclass
from typing import Dict, Optional

from solders.pubkey import Pubkey
from solders.signature import Signature

# 챌린지(nonce) 유효시간 — 사용자가 Phantom 팝업을 확인하는 데 걸리는 시간 + 여유.
NONCE_TTL_SEC = 300
# 세션 유효시간. 심사·시연 한 세션을 넉넉히 덮되 무기한은 아니게.
SESSION_TTL_SEC = 12 * 3600
# 동시에 살아 있을 수 있는 미사용 챌린지 수 — 무한 발급으로 메모리를 밀어내는 것을 막는다.
MAX_PENDING_CHALLENGES = 256

SESSION_COOKIE = "guard_session"

# 서명 요청 팝업에 뜨는 안내 문장. 무엇에 서명하는지 사용자가 읽고 판단할 수 있어야 한다.
STATEMENT = ("402 Guard 에 이 지갑으로 로그인합니다. "
             "이 서명은 지갑 소유 증명일 뿐이며 자금이 이동하지 않습니다.")


class AuthError(Exception):
    """인증 실패 — 호출측(서버 라우트)이 401 로 표면화한다."""


@dataclass
class _Challenge:
    pubkey: str
    message: str
    expires_at: float


@dataclass
class _Session:
    pubkey: str
    expires_at: float
    created_at: float


class WalletAuth:
    """지갑 서명 기반 로그인 상태. 프로세스 메모리에만 산다."""

    def __init__(self, chain_id: str = "devnet"):
        self.chain_id = chain_id
        self._challenges: Dict[str, _Challenge] = {}
        self._sessions: Dict[str, _Session] = {}
        self._now = time.time          # 테스트에서 가짜 시계로 교체 가능

    # ---- 1) 챌린지 발급 ----

    def build_message(self, pubkey: str, nonce: str, domain: str, uri: str,
                      issued_at: str) -> str:
        """SIWS(EIP-4361) 서식의 로그인 메시지. 서버가 만드는 유일한 정본이다."""
        return (
            f"{domain} wants you to sign in with your Solana account:\n"
            f"{pubkey}\n"
            f"\n"
            f"{STATEMENT}\n"
            f"\n"
            f"URI: {uri}\n"
            f"Version: 1\n"
            f"Chain ID: {self.chain_id}\n"
            f"Nonce: {nonce}\n"
            f"Issued At: {issued_at}"
        )

    def challenge(self, pubkey: str, domain: str, uri: str) -> dict:
        """서명할 메시지를 만들어 돌려준다. pubkey 형식이 아니면 AuthError."""
        pk = _normalize_pubkey(pubkey)
        self._sweep()
        if len(self._challenges) >= MAX_PENDING_CHALLENGES:
            # 가장 오래된 것부터 버린다 — 정상 사용자는 곧바로 서명하므로 영향이 없다.
            oldest = sorted(self._challenges.items(), key=lambda kv: kv[1].expires_at)
            for nonce, _ in oldest[: len(oldest) // 2 or 1]:
                self._challenges.pop(nonce, None)

        now = self._now()
        nonce = secrets.token_hex(16)          # 32자 영숫자 (SIWS 최소 8자 요건 충족)
        issued_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        message = self.build_message(pk, nonce, domain, uri, issued_at)
        self._challenges[nonce] = _Challenge(pk, message, now + NONCE_TTL_SEC)
        return {"nonce": nonce, "message": message, "expires_in": NONCE_TTL_SEC}

    # ---- 2) 서명 검증 + 세션 발급 ----

    def verify(self, pubkey: str, message: str, signature_b64: str) -> str:
        """서명을 검증하고 세션 토큰을 돌려준다. 실패는 전부 AuthError.

        signature_b64: Phantom 이 돌려준 64바이트 서명을 base64 로 인코딩한 문자열.
        (브라우저에 base58 구현이 없어 base64 로 받고, 서버가 표시·기록용 base58 로 바꾼다.)
        """
        pk = _normalize_pubkey(pubkey)
        nonce = _nonce_of(message)
        if not nonce:
            raise AuthError("로그인 메시지에 Nonce 가 없습니다.")

        ch = self._challenges.pop(nonce, None)   # 1회용 — 꺼내는 즉시 소모한다
        if ch is None:
            raise AuthError("만료되었거나 이미 사용된 로그인 요청입니다. 다시 시도해 주세요.")
        if self._now() > ch.expires_at:
            raise AuthError("로그인 요청이 만료되었습니다. 다시 시도해 주세요.")
        if ch.pubkey != pk:
            raise AuthError("서명한 지갑이 로그인을 요청한 지갑과 다릅니다.")
        # 서버가 발급한 정본과 한 글자라도 다르면 거부한다(부분 일치·부분 문자열 검사 없음).
        if message != ch.message:
            raise AuthError("서명 대상 메시지가 서버가 발급한 원문과 다릅니다.")

        try:
            raw = base64.b64decode(signature_b64, validate=True)
        except (binascii.Error, ValueError):
            raise AuthError("서명 형식이 올바르지 않습니다 (base64 아님).")
        if len(raw) != 64:
            raise AuthError(f"서명 길이가 올바르지 않습니다 ({len(raw)}바이트, 64 필요).")

        try:
            sig = Signature.from_bytes(raw)
            ok = sig.verify(Pubkey.from_string(pk), message.encode("utf-8"))
        except Exception:
            raise AuthError("서명을 검증할 수 없습니다.")
        if not ok:
            raise AuthError("서명이 이 지갑의 것이 아닙니다.")

        now = self._now()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = _Session(pk, now + SESSION_TTL_SEC, now)
        return token

    # ---- 3) 세션 조회·해제 ----

    def session(self, token: Optional[str]) -> Optional[dict]:
        """유효한 세션이면 {pubkey, expires_at}, 아니면 None."""
        if not token:
            return None
        s = self._sessions.get(token)
        if s is None:
            return None
        if self._now() > s.expires_at:
            self._sessions.pop(token, None)
            return None
        return {"pubkey": s.pubkey, "expires_at": s.expires_at, "created_at": s.created_at}

    def logout(self, token: Optional[str]) -> bool:
        return self._sessions.pop(token, None) is not None if token else False

    def _sweep(self) -> None:
        """만료된 챌린지·세션 청소 — 장시간 구동에서 메모리가 자라지 않게."""
        now = self._now()
        for k in [k for k, v in self._challenges.items() if v.expires_at < now]:
            self._challenges.pop(k, None)
        for k in [k for k, v in self._sessions.items() if v.expires_at < now]:
            self._sessions.pop(k, None)


def _normalize_pubkey(pubkey: str) -> str:
    """base58 Solana 주소인지 확인하고 정규화된 문자열을 돌려준다."""
    try:
        return str(Pubkey.from_string(str(pubkey).strip()))
    except Exception:
        raise AuthError("지갑 주소 형식이 올바르지 않습니다.")


def _nonce_of(message: str) -> str:
    """메시지에서 Nonce 줄을 뽑는다 — 어떤 챌린지를 소모할지 찾는 열쇠일 뿐이다.

    이 값으로 진위를 판단하지 않는다. 진위는 저장된 원문과의 완전 일치 + 서명 검증이 본다.
    """
    for line in str(message).splitlines():
        if line.startswith("Nonce: "):
            return line[len("Nonce: "):].strip()
    return ""
