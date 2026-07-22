# README 뼈대 (제출용)

> 이 파일은 제출용 README.md의 목차 뼈대입니다. 기존 README가 있으면 Claude Code 첫 세션에서 병합하고, 완성되면 저장소 루트의 README.md로 둡니다. 심사 기준: "재현 가능한 코드 + README" — **심사위원이 이 문서만 보고 클론→실행→트랜잭션 확인까지 갈 수 있어야 합니다.**

---

# AutoTrader Agent

> 조건 기반 자율 주식매매 멀티에이전트 — 사람은 규칙과 한도만 정하고, 에이전트가 협상하고 USDC로 온체인 정산한다.

[데모 영상 링크] · [라이브 배포 URL] · [트랜잭션 증빙(artifacts/tx/)]

## 1. 문제와 당위성 (Why blockchain / Why autonomous)
- 타겟 고객과 문제:
- 왜 카드망·기존 증권 인프라가 아닌 블록체인인가: 에이전트는 계좌를 개설할 수 없지만 지갑은 코드 한 줄로 생성 / 무인증 결제 / 저수수료·빠른 확정(~400ms) / 24/7 국경 무관
- 왜 사람 개입 없는 자율 결제인가: (AP2 mandate로 한도를 온체인 강제하므로 "무섭지 않은 자율")

## 2. 아키텍처
- 다이어그램 + 3줄 설명 (A2A 협의 / AP2 한도 / x402 정산 on Solana)
- 프로토콜 계층: A2A · AP2 · x402 · USDC(SPL) · Gemini API

## 3. 빠른 시작 (Reproduce)
```bash
# 요구사항: Python 3.11+, Solana CLI, (localnet) solana-test-validator
git clone <repo>
cp .env.example .env   # 값 채우기
pip install -r requirements.txt
# localnet 데모
python setup_devnet.py       # 지갑·USDC·주식토큰 발행
python run_demo.py --live    # 매수→매도 전 과정 + tx 해시 출력
```

## 4. 데모 시나리오
- 규칙 설정 → Gemini 판단 → A2A 견적 → AP2 한도 체크 → 온체인 정산 → 영수증
- 거부 케이스: 한도 초과 / mandate 위변조 / 잔액 부족

## 5. 트랜잭션 증빙
- `artifacts/tx/`에 실행 로그·해시 아카이브, Solana Explorer(devnet) 링크

## 6. 수익 모델
- (소개서와 일치시킬 것)

## 7. 기술 스택 & 한계
- Python · Solana devnet · Gemini API(무료 티어) · Cloud Run · Firestore
- 한계와 다음 단계: (정직하게 1문단)
