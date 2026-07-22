"""Gemini API 연결 확인 스크립트 — 키 값은 절대 출력하지 않는다.

developer(AIza 키)와 vertex express(AQ. 등 새 형식 키) 두 모드를 순서대로
시도해, 어느 모드로 연결되는지 알려준다.

실행:  python scripts/check_gemini.py
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CFG  # .env 로드


def main() -> int:
    key = CFG.gemini_api_key
    if not key:
        print("FAIL: .env 에 GEMINI_API_KEY 가 비어 있습니다.")
        return 1
    try:
        from google import genai
    except ImportError:
        print("FAIL: google-genai 미설치 — pip install google-genai")
        return 1

    kind = "developer 형식(AIza…)" if key.startswith("AIza") else "새 형식(AQ.… 등)"
    print(f"키 형식: {kind} / 시험 모델: {CFG.gemini_model}")

    modes = [("developer", {"api_key": key}), ("vertex", {"vertexai": True, "api_key": key})]
    preferred = CFG.gemini_mode or ("developer" if key.startswith("AIza") else "vertex")
    if preferred == "vertex":
        modes.reverse()

    for name, kwargs in modes:
        try:
            client = genai.Client(**kwargs)
            resp = client.models.generate_content(
                model=CFG.gemini_model, contents="한 단어로만 답해: 연결됨",
            )
            text = (resp.text or "").strip().replace("\n", " ")
            print(f"OK: '{name}' 모드 연결 성공 — 모델 응답: {text[:40]}")
            print(f"MODE={name}")
            return 0
        except Exception as e:
            print(f"'{name}' 모드 실패: {type(e).__name__}: {str(e)[:160]}")

    print("FAIL: 두 모드 모두 연결 실패 — 키를 다시 발급받아야 할 수 있습니다.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
