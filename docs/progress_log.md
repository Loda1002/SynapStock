# 진행 로그

굵직한 진행(기능 완료, 결정 변경)마다 날짜와 함께 2~3줄 append. Cowork(claude.ai 프로젝트) 진행상황 문서와 수동 동기화.

---

## 2026-07-21
- MVP 코어 완료(오프라인 검증 통과): A2A+x402 3단계 결제, AP2 mandate 한도, x402 결제 코어, 규칙 기반 판단, 목 시세 피드. devnet 라이브 경로(setup_devnet.py + run_demo.py --live) 준비됨.

## 2026-07-22
- 0721 킥오프 세션 분석 반영: Gemini는 무료 티어 API 호출로 확정(ADK/Vertex 불필요), 평가 네트워크 Devnet/Localnet, 목업 심사 제외, 검증 4단계 루틴 채택.
- Claude Code 로컬 개발 체제로 전환. 시작 패키지(CLAUDE.md, docs, .gitignore, .env.example) 적용.
- Claude Code 첫 세션: 로컬 환경(Win11·Python 3.10.8) 검증 통과 — 오프라인 데모 전 과정 재현 OK. 수정: requirements.txt 한글 주석이 한국어 Windows(cp949)에서 pip 파싱을 깨는 버그 → ASCII화, solana-py를 3.10 호환 0.38로 조정, 지갑 경로 `.wallets/`→`secrets/` 통일, .env.example 정비(WALLET_DIR 추가·Gemini 무료티어 키로 교체), .gitignore 보강(secrets/·*-keypair.json·test-ledger/), architecture.md §6 실제 구조로 갱신. localnet 라이브 tx 계획 수립, 승인 대기.
- 결정·확인(사용자): 최종 산출물은 **웹 애플리케이션**(대시보드 → Cloud Run). 진행자 가이드 재확인 — localnet 위주 개발, devnet은 마지막 시연·제출 검증. GitHub 저장소 `SolanaAgent` 생성(원격 연결 예정). 비개발자용 검증기 온/오프 더블클릭 스크립트(scripts/start·stop_localnet.bat) 추가. 브랜치 main 개명.
- **다음 단계 1번(라이브 tx) localnet 완료**: WSL Ubuntu에 Agave 4.1.1 설치 → solana-test-validator 기동 → setup → `run_demo.py --live` 성공. 매수 3건 = 온체인 tx 6건(USDC 결제+주식 전달) 확정, 전후 잔액 RPC 교차검증 PASS, `artifacts/tx/20260722_1547_solana-localnet_live_buy.json` 아카이브. 수정 2건: 클라이언트 커밋먼트 Confirmed 통일(에어드랍 자금 미인식 해결), `preflight_commitment=Confirmed` 명시(Finalized 뱅크 preflight의 "Blockhash not found" 해결). 증빙 보강: 주식 전달 tx 서명 수집(기존엔 버려짐), 라이브 미확정 시 status=failed 처리, 전후 잔액 스냅샷·교차검증·JSON 아카이빙 추가. git 저장소 초기화. devnet은 SOL 확보(디스코드 요청) 후 .env 전환만 하면 됨.
