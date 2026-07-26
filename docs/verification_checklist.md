# 검증 체크리스트 (결제 기능 완료 판정 기준)

0721 데모 세션의 검증 패턴(재고 32→31을 화면과 Firestore 양쪽에서 확인)을 우리 프로젝트에 이식한 것. **4단계를 모두 통과해야 "완료"로 간주한다.**

## 기능 검증 4단계

1. **로컬 전 과정 실행** — 매수(또는 매도) 시나리오를 처음부터 끝까지 실행하고 영수증(payment-completed) 확인
2. **양쪽 교차 확인** — 상태 변화를 두 곳에서 모두 확인:
   - 앱 상태: 포지션·잔액 표시 (추후 Firestore/대시보드)
   - 온체인: RPC 조회로 USDC·주식토큰 잔액 변화, 트랜잭션 해시 존재
3. **배포 환경 재검증** — Cloud Run 배포 후 배포 URL에서 1~2번 동일 반복
4. **증빙 아카이빙** — 트랜잭션 해시·실행 로그를 `artifacts/tx/YYYYMMDD_HHMM_설명.json`(또는 .log)으로 저장하고 커밋

## 시나리오별 필수 케이스

- 정상 매수 1회 → tx 해시 확보
- 정상 매도 1회 → tx 해시 확보
- 한도 초과 시 결제 **거부** (AP2 mandate)
- mandate 위변조 시 **거부**
- 잔액 부족 시 **거부**
- **HTTP 402 왕복 1회**(G5) → 402 청구서 → `X-PAYMENT` 재시도 → 200 정산 → 리플레이 402
- **판단 출처 기록 확인** — tx 아카이브의 `ai.by_source`·거래 행 `decision_source` 가 그 세션의
  두뇌(Gemini/규칙)와 일치하는가 (축② "온체인 세션이 AI 로 구동됐다"의 증빙)

## HTTP 402 레그 검증 (G5, 2026-07-26 추가)

```bash
python -m scripts.test_http402      # 53건 — 상태코드·헤더·왕복·리플레이·Guard·엔진 경로
python -m scripts.demo_http402      # 실제 TCP 왕복 + artifacts/x402_http/ 증빙
```

배포 URL 에서도 같은 402 가 나와야 한다(메인 앱에 마운트돼 있음):

```bash
curl -i -X POST https://<배포URL>/broker/orders -H "Content-Type: application/json" -d "{\"symbol\":\"AAPL\",\"spend_usdc\":\"10\",\"price_usdc\":\"200\"}"
```

기대: `HTTP/1.1 402 Payment Required` + 본문에 `x402Version`·`accepts[0].payTo`·`maxAmountRequired`.

## 데모데이(8/21) 폴백 준비

- 1순위: Devnet 라이브 시연 (사전에 Devnet SOL 충분히 확보 — 디스코드 선요청)
- 2순위: 행사장 네트워크 불안정 시 **로컬넷 재시연 경로** (solana-test-validator + 셋업 스크립트를 노트북에서 즉시 실행 가능하게 유지)
- 3순위: 사전 녹화한 데모 영상 (제출물 ③을 그대로 활용)
- 리허설: 제출 전에 "노트북 초기 상태 → 데모 완료"를 15분 안에 재현 가능한지 1회 이상 연습
