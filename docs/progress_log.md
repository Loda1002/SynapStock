# 진행 로그

굵직한 진행(기능 완료, 결정 변경)마다 날짜와 함께 2~3줄 append. Cowork(claude.ai 프로젝트) 진행상황 문서와 수동 동기화.

---

## 2026-07-23
- 기능 선택 확정(사용자): **A 전부(A1~A8) + B1·B2·B4·B6·B7** (B3·B5 제외). 필수군 A1~A5 / 어필군 A6~A8·B전체 구분. B4는 "알림만/알림+자동정지" 토글 설계로 고민 해소, B6은 mandate 우회 불가·규칙 재서명 연결 원칙. 구체 설계 `docs/feature_spec.md`(P1→P2→P3) — 구현은 새 대화(웹 서비스화)에서 시작.
- CLAUDE.md 세션 운영에 **대화 전환 안내 규칙** 추가(큰 단계 완료 시 Claude가 먼저 새 대화 전환을 안내하고 문서 갱신 + 스타터 프롬프트 제공).

## 2026-07-21
- MVP 코어 완료(오프라인 검증 통과): A2A+x402 3단계 결제, AP2 mandate 한도, x402 결제 코어, 규칙 기반 판단, 목 시세 피드. devnet 라이브 경로(setup_devnet.py + run_demo.py --live) 준비됨.

## 2026-07-22
- 0721 킥오프 세션 분석 반영: Gemini는 무료 티어 API 호출로 확정(ADK/Vertex 불필요), 평가 네트워크 Devnet/Localnet, 목업 심사 제외, 검증 4단계 루틴 채택.
- Claude Code 로컬 개발 체제로 전환. 시작 패키지(CLAUDE.md, docs, .gitignore, .env.example) 적용.
- Claude Code 첫 세션: 로컬 환경(Win11·Python 3.10.8) 검증 통과 — 오프라인 데모 전 과정 재현 OK. 수정: requirements.txt 한글 주석이 한국어 Windows(cp949)에서 pip 파싱을 깨는 버그 → ASCII화, solana-py를 3.10 호환 0.38로 조정, 지갑 경로 `.wallets/`→`secrets/` 통일, .env.example 정비(WALLET_DIR 추가·Gemini 무료티어 키로 교체), .gitignore 보강(secrets/·*-keypair.json·test-ledger/), architecture.md §6 실제 구조로 갱신. localnet 라이브 tx 계획 수립, 승인 대기.
- GitHub 원격 연결·푸시 완료: https://github.com/Loda1002/SolanaAgent (private — 제출 전 public 전환). 바탕화면에 검증기 켜기/끄기 바로가기 생성.
- 결정·확인(사용자): 최종 산출물은 **웹 애플리케이션**(대시보드 → Cloud Run). 진행자 가이드 재확인 — localnet 위주 개발, devnet은 마지막 시연·제출 검증. GitHub 저장소 `SolanaAgent` 생성(원격 연결 예정). 비개발자용 검증기 온/오프 더블클릭 스크립트(scripts/start·stop_localnet.bat) 추가. 브랜치 main 개명.
- 결정(사용자): 디스코드 Devnet SOL 요청 완료. devnet 착수는 **로컬에서 UI/UX 적용 테스트까지 끝난 뒤**로 확정(진행자 가이드의 "localnet 99% → 마지막 devnet"과 일치). 허용 창 사전 설명 규칙이 CLAUDE.md 세션 운영에 명문화됨.
- **매도 사이클 + 거부 케이스 완료**: x402 역방향(주식 전송→브로커 온체인 검증→USDC 지급) 구현, 매도 대금은 AP2 예산에 환입(예산=순투입 한도). localnet 라이브 풀사이클 검증 — 매수 4·매도 1 전부 온체인 확정, 순변화 교차검증 PASS(+7 USDC 수익 시나리오). `scripts/demo_rejections.py`: 건별 한도 초과·mandate 위변조·금액 부족·미허용 종목 4종 정상 차단(로그 아카이브). 특기: 라이브 중 Gemini 무료 티어 rate limit로 3틱이 규칙 폴백 처리 — 폴백 설계 실전 검증. CLAUDE.md 현재 상태·다음 단계 갱신(1=웹 서비스화, 2=Cloud Run, 3=디자인, 4=devnet, 5=제출물).
- **다음 단계 2번(Gemini 교체) 완료**: `agents/gemini_decider.py` 신설 — Gemini API(무료 티어, `gemini-flash-latest`, developer 모드)가 시세 흐름·규칙·예산을 보고 매수/매도/보류 판단 + 한국어 이유 생성. 호출 실패 시 규칙 기반 자동 폴백(데모데이 네트워크 대비). localnet 라이브 재검증: **Gemini 판단 매수 3건 온체인 확정**, 교차검증 PASS, 아카이브에 판단 주체·이유 기록. 참고: 신규 계정 키는 `AQ.` 새 형식이고 gemini-2.5 계열이 신규 사용자에게 차단돼 `gemini-flash-latest` 별칭 사용. 연결 확인은 `scripts/check_gemini.py`.
- **다음 단계 1번(라이브 tx) localnet 완료**: WSL Ubuntu에 Agave 4.1.1 설치 → solana-test-validator 기동 → setup → `run_demo.py --live` 성공. 매수 3건 = 온체인 tx 6건(USDC 결제+주식 전달) 확정, 전후 잔액 RPC 교차검증 PASS, `artifacts/tx/20260722_1547_solana-localnet_live_buy.json` 아카이브. 수정 2건: 클라이언트 커밋먼트 Confirmed 통일(에어드랍 자금 미인식 해결), `preflight_commitment=Confirmed` 명시(Finalized 뱅크 preflight의 "Blockhash not found" 해결). 증빙 보강: 주식 전달 tx 서명 수집(기존엔 버려짐), 라이브 미확정 시 status=failed 처리, 전후 잔액 스냅샷·교차검증·JSON 아카이빙 추가. git 저장소 초기화. devnet은 SOL 확보(디스코드 요청) 후 .env 전환만 하면 됨.
