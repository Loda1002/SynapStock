"""Gemini 응답 파서 회귀 테스트 — 실제로 발생했던 형식 오류를 모아 둔 것.

실행: python -m scripts.test_gemini_parse   (API 호출 없음, 순수 파싱만 검사)

배경: 사용자 리포트 `JSONDecodeError: Invalid \\uXXXX escape` — 모델이 한국어 이유
안에 잘못된 이스케이프를 넣어 json.loads 가 깨졌고, 매 틱 규칙 폴백으로 밀렸다.
parse_decision_json 이 이런 응답을 정화·추출해 살려내는지 확인한다.
"""
from agents.gemini_decider import parse_decision_json

CASES = [
    # (설명, 원문, 기대 action)
    ("정상 JSON",
     '{"action":"hold","reason":"조건 미충족","spend_usdc":0}', "hold"),
    ("잘못된 \\u 이스케이프 (보고된 오류)",
     '{"action":"hold","reason":"현재가가 기준에 못 미쳐 \\uac 보류합니다","spend_usdc":0}', "hold"),
    ("\\u 뒤에 한글이 붙은 경우",
     '{"action":"buy","reason":"하락 추세라 \\u 매수 적기","spend_usdc":30}', "buy"),
    ("알 수 없는 이스케이프(\\!)",
     '{"action":"sell","reason":"목표가 도달\\! 매도","spend_usdc":0}', "sell"),
    ("코드펜스로 감싼 응답",
     '```json\n{"action":"sell","reason":"목표가 도달","spend_usdc":0}\n```', "sell"),
    ("앞뒤 설명이 붙은 응답",
     '판단 결과: {"action":"hold","reason":"조건 미충족","spend_usdc":0} 이상.', "hold"),
    ("문자열 안 줄바꿈(제어문자)",
     '{"action":"hold","reason":"가격 흐름이\n애매합니다","spend_usdc":0}', "hold"),
    ("정상 유니코드 이스케이프는 보존",
     '{"action":"buy","reason":"\\uac00\\uaca9 하락","spend_usdc":30}', "buy"),
    ("따옴표가 깨져 JSON 복구 불가 → 정규식 추출",
     '{"action":"buy","reason":"가격이 "싸다"고 판단","spend_usdc":30}', "buy"),
]

FAIL_CASES = [
    ("빈 응답", ""),
    ("JSON 아님", "죄송합니다. 판단할 수 없습니다."),
]


def main() -> int:
    bad = 0
    for name, raw, expected in CASES:
        try:
            data = parse_decision_json(raw)
        except Exception as e:
            print(f"[FAIL] {name} → {type(e).__name__}: {e}")
            bad += 1
            continue
        action = str(data.get("action", "")).lower()
        ok = action == expected
        bad += 0 if ok else 1
        print(f"[{'OK  ' if ok else 'FAIL'}] {name} → action={action!r} reason={data.get('reason')!r}")

    # 정상 이스케이프가 한글로 복원되는지 (가격 = '가격')
    data = parse_decision_json(CASES[7][1])
    if not data["reason"].startswith("가격"):
        print(f"[FAIL] 유니코드 복원 → {data['reason']!r}")
        bad += 1

    for name, raw in FAIL_CASES:
        try:
            parse_decision_json(raw)
        except ValueError as e:
            print(f"[OK  ] {name} → ValueError 로 규칙 폴백 위임 ({e})")
        else:
            print(f"[FAIL] {name} → 실패해야 하는데 통과함")
            bad += 1

    print("\n결과:", "전부 통과" if bad == 0 else f"{bad}건 실패")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
