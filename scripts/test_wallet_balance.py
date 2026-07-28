"""연결된 지갑의 온체인 잔액 표시(GET /api/wallet/balance) 회귀 테스트.

이 엔드포인트가 지켜야 하는 것 네 가지 — 전부 "잔액은 부가 정보다" 에서 나온다:

  1) **세션 쿠키의 지갑만** 조회한다. 주소를 파라미터로 받으면 이 서버가 남의 지갑을 훑는
     무료 RPC 프록시가 되고, 공용 devnet RPC 의 429 를 우리가 뒤집어쓴다.
  2) 미연결은 401 이 아니라 `connected=false` 다. 데모는 로그인 없이도 열려 있어야 하고,
     헤더가 401 을 만나 콘솔 오류를 뿌리면 안 된다.
  3) RPC 가 죽어도 **500 이 아니라** `error` 필드로 돌아온다. 잔액 조회 실패로 헤더가
     깨지거나 대시보드가 멈추면, 부가 정보가 본체를 무너뜨린 셈이 된다.
  4) 짧은 캐시가 있다. 헤더가 60초마다 물어보고 심사위원이 여러 탭을 열 수 있는데,
     매 요청이 공용 RPC 를 두드리면 그 자체가 장애 요인이다. **실패도 캐시**한다 —
     RPC 가 죽어 있을 때 폴링이 그대로 재시도 폭풍이 되는 것을 막는다.

검증 방법(네트워크 0): `_read_wallet_balance` 만 스텁으로 갈아끼운다. 세션 발급·쿠키
왕복·엔드포인트 분기는 전부 실제 코드가 그대로 돈다.

재현: python -m scripts.test_wallet_balance   (프로젝트 루트)
"""
from __future__ import annotations
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: F401,E402 — 임포트 시 콘솔 인코딩 안전화

from fastapi.testclient import TestClient  # noqa: E402
from solders.keypair import Keypair  # noqa: E402

from web import server as srv  # noqa: E402
from web.auth import SESSION_COOKIE  # noqa: E402

PASS, FAIL = "통과", "실패"
_results: list[tuple[str, bool, str]] = []


def _p(s: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    try:
        s.encode(enc)
    except (UnicodeEncodeError, LookupError):
        s = s.encode(enc, "replace").decode(enc, "replace")
    print(s)


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    _p(f"  [{PASS if cond else FAIL}] {name}" + (f" - {detail}" if detail else ""))


def _login(client: TestClient) -> str:
    """실제 지갑 로그인 흐름(challenge → ed25519 서명 → verify)으로 세션 쿠키를 얻는다."""
    kp = Keypair()
    pubkey = str(kp.pubkey())
    ch = client.post("/api/auth/challenge", json={"pubkey": pubkey}).json()
    import base64
    sig = base64.b64encode(bytes(kp.sign_message(ch["message"].encode("utf-8")))).decode()
    r = client.post("/api/auth/verify",
                    json={"pubkey": pubkey, "message": ch["message"], "signature": sig})
    assert r.status_code == 200, r.text
    return pubkey


def _stub(result: dict = None, raises: Exception = None):
    """_read_wallet_balance 를 갈아끼우고 호출 횟수를 센다(캐시 검증용)."""
    calls = {"n": 0}

    async def fake(pubkey: str) -> dict:
        calls["n"] += 1
        if raises is not None:
            raise raises
        return dict(result or {})

    srv._read_wallet_balance = fake
    srv._bal_cache.clear()
    return calls


def main() -> int:
    orig = srv._read_wallet_balance
    try:
        # ---------- 1) 미연결 ----------
        _p("\n[1] 미연결 - 401 이 아니라 connected=false")
        with TestClient(srv.app) as client:
            calls = _stub({"sol": "1.0000", "usdc": "0"})
            r = client.get("/api/wallet/balance")
            check("HTTP 200 (데모는 로그인 없이도 열려 있다)", r.status_code == 200, str(r.status_code))
            check("connected=false", r.json().get("connected") is False, str(r.json()))
            check("★미연결이면 RPC 를 아예 부르지 않는다", calls["n"] == 0, f"호출 {calls['n']}회")

        # ---------- 2) 연결 후 정상 조회 ----------
        _p("\n[2] 연결 후 - 세션 쿠키의 지갑 잔액을 돌려준다")
        with TestClient(srv.app) as client:
            calls = _stub({"sol": "1.2345", "usdc": "42.5",
                           "usdc_mint": "MINT", "network": "solana-devnet"})
            pubkey = _login(client)
            r = client.get("/api/wallet/balance")
            j = r.json()
            check("HTTP 200", r.status_code == 200, str(r.status_code))
            check("connected=true", j.get("connected") is True, str(j))
            check("★조회 대상이 세션의 지갑", j.get("pubkey") == pubkey, str(j.get("pubkey")))
            check("SOL 잔액 방출", j.get("sol") == "1.2345", str(j.get("sol")))
            check("USDC 잔액 방출", j.get("usdc") == "42.5", str(j.get("usdc")))
            check("네트워크·민트 표기 동봉(무엇을 세는 숫자인지)",
                  j.get("network") == "solana-devnet" and j.get("usdc_mint") == "MINT", str(j))
            check("error 필드 없음", "error" not in j, str(j.get("error")))

        # ---------- 3) 주소 파라미터로 남의 지갑을 못 조회한다 ----------
        _p("\n[3] 임의 주소 조회 차단 - 무료 RPC 프록시가 되지 않는다")
        with TestClient(srv.app) as client:
            calls = _stub({"sol": "9.9999", "usdc": "0"})
            mine = _login(client)
            other = str(Keypair().pubkey())
            r = client.get(f"/api/wallet/balance?pubkey={other}")
            j = r.json()
            check("★쿼리로 준 남의 주소가 무시되고 세션 지갑이 조회된다",
                  j.get("pubkey") == mine, f"{j.get('pubkey')} / 요청 {other}")

        # ---------- 4) RPC 실패는 500 이 아니라 error 필드 ----------
        _p("\n[4] RPC 실패 - 헤더를 깨뜨리지 않는다")
        with TestClient(srv.app) as client:
            calls = _stub(raises=RuntimeError("RPC 재시도 소진"))
            _login(client)
            r = client.get("/api/wallet/balance")
            j = r.json()
            check("★HTTP 500 이 아니다", r.status_code == 200, str(r.status_code))
            check("connected 은 여전히 true", j.get("connected") is True, str(j))
            check("error 필드로 사유 전달", "RuntimeError" in str(j.get("error")), str(j.get("error")))
            check("잔액 필드는 없다(0 으로 지어내지 않는다)", "sol" not in j, str(j))

        # ---------- 5) 캐시 ----------
        _p("\n[5] 캐시 - 폴링이 공용 RPC 를 두드리지 않는다")
        with TestClient(srv.app) as client:
            calls = _stub({"sol": "1.0000", "usdc": "0"})
            _login(client)
            first = client.get("/api/wallet/balance").json()
            second = client.get("/api/wallet/balance").json()
            third = client.get("/api/wallet/balance").json()
            check("★3회 요청에 RPC 는 1회", calls["n"] == 1, f"호출 {calls['n']}회")
            check("첫 응답은 cached=false", first.get("cached") is False, str(first.get("cached")))
            check("이후 응답은 cached=true", second.get("cached") is True and third.get("cached") is True,
                  f"{second.get('cached')}/{third.get('cached')}")
            check("캐시된 값도 동일", second.get("sol") == "1.0000", str(second.get("sol")))

        _p("\n[6] 실패도 캐시된다 - RPC 장애가 재시도 폭풍이 되지 않는다")
        with TestClient(srv.app) as client:
            calls = _stub(raises=RuntimeError("죽은 RPC"))
            _login(client)
            client.get("/api/wallet/balance")
            client.get("/api/wallet/balance")
            client.get("/api/wallet/balance")
            check("★3회 요청에 RPC 는 1회(실패도 캐시)", calls["n"] == 1, f"호출 {calls['n']}회")
    finally:
        srv._read_wallet_balance = orig
        srv._bal_cache.clear()

    ok = sum(1 for _, c, _ in _results if c)
    total = len(_results)
    _p(f"\n결과: {ok}/{total} 통과")
    for name, cond, detail in _results:
        if not cond:
            _p(f"  실패: {name} - {detail}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
