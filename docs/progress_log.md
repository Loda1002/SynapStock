# 진행 로그

굵직한 진행(기능 완료, 결정 변경)마다 날짜와 함께 2~3줄 append. Cowork(claude.ai 프로젝트) 진행상황 문서와 수동 동기화.

---

## 2026-07-23
- **P2 엔진 확장 완료(localnet 라이브 검증 통과, 기능당 1커밋 × 5)**: ①A8 수수료 투명화 — `BROKER_FEE_BPS=30`(0.3%), 매수 `총액=소계+수수료`·매도 `수령액=소계−수수료` 양방향(사용자 확정), AP2 검사는 수수료 포함 총액 기준, 평단=실효단가 → 실현손익=순손익, 대시보드에 견적 분리 표기·수수료 컬럼·누적 수수료(브로커 수익) 카드. **0.1% 인하안은 최종 제출 전 재검토(소개서 수익모델 수치 확정 시 사용자에게 질문할 것)**. ②A3 한도 설정 — `/api/mandate`로 새 mandate 재서명(spent 이월), 실행 중엔 긴급정지 상태에서만 적용, 대기 중엔 다음 세션 적용, 변경 이력 이벤트+아카이브. ③A4 알림 — 토스트+Web Notification(백그라운드 탭), SSE 히스토리 재생분 억제. ④B7 적립식 DCA — 세션 전략 선택(조건형/적립형), N틱마다 정액 매수(Gemini 미사용 라벨, `dca` 배지), mandate 동일 경로. ⑤B2 브리핑 — Gemini 한국어 리포트(실패 시 템플릿 폴백), 수동 버튼+세션 종료 자동+장 마감 시각(`DAILY_BRIEFING_TIME`) 3경로, `artifacts/briefings/` 저장. 검증: 오프라인 스모크(수수료 9건·브리핑 8건 PASS) → 웹 드라이런(기능별) → **localnet 라이브 2세션**: 조건형 매수4·매도1 + DCA 정액 3건 전부 온체인 확정, **교차검증 USDC/주식 PASS×2**(수수료 반영 순변화 54.22 일치), 증빙 `artifacts/tx/20260723_1355·1357_*_web_session.json`. 시나리오 실증: 한도 20으로 낮추자 수수료 포함 총액 29.98>20 AP2 거부 → 정지 중 100/40 재서명 → 재개 후 통과.
- 기능 선택 확정(사용자): **A 전부(A1~A8) + B1·B2·B4·B6·B7** (B3·B5 제외). 필수군 A1~A5 / 어필군 A6~A8·B전체 구분. B4는 "알림만/알림+자동정지" 토글 설계로 고민 해소, B6은 mandate 우회 불가·규칙 재서명 연결 원칙. 구체 설계 `docs/feature_spec.md`(P1→P2→P3) — 구현은 새 대화(웹 서비스화)에서 시작.
- CLAUDE.md 세션 운영에 **대화 전환 안내 규칙** 추가(큰 단계 완료 시 Claude가 먼저 새 대화 전환을 안내하고 문서 갱신 + 스타터 프롬프트 제공).
- **P1 웹 서비스화 완료(라이브 검증 통과)**: FastAPI+SSE `web/` 신설(engine/events/server + 무디자인 대시보드, 기존 코어 무수정 재사용). A1 대시보드·A2 긴급정지(주체 기록, 시세는 계속)·A5 거래 테이블(explorer 링크)·A6 판단 타임라인(gemini/rule-fallback 배지)·A7 실현손익 전부 동작. 엔진은 대시보드에서 세션 시작/종료(연속 루프, 기본 8초 틱 — 사용자 선택). localnet 라이브: 매수4+매도1 온체인 확정, +7 USDC 실현손익 온체인 반영, 교차검증 PASS, `artifacts/tx/20260723_1220_..._web_session.json`. 드라이런에서 새로고침 피드 복원(SSE 재전송)·rate limit 규칙 폴백도 실증. 디자인 시안은 `web/static/css/theme.css` 변수 교체로 적용 예정.
- **버그 수정 라운드(사용자 보고 3건, 당일 해결·재검증)**: ①"Gemini 계속 호출 실패" 원인 = `gemini-flash-latest`가 gemini-3.6-flash로 연결되고 이 모델 무료 티어가 **하루 20회**뿐 → 소진. 모델을 `gemini-flash-lite-latest`로 교체(한도 넉넉, 판단 품질 충분 — 재검증에서 판단 전건 gemini 배지), 429 시 retryDelay 존중 쿨다운 추가(틱마다 헛호출 방지), 타임라인에 실제 원인 문구 표면화, `check_gemini.py` cp949 크래시 수정. ②스크롤 부자연 = 피드·테이블이 무한히 자라 페이지가 밀림 + 스크롤 체이닝 → 고정 높이+내부 스크롤+`overscroll-behavior: contain`+sticky 테이블 헤더+`scrollbar-gutter`. ③"버튼 빼고 어두움" = `color-scheme` 미선언 상태에서 다크 모드 브라우저가 페이지를 강제 반전 → 라이트·다크 팔레트 정식 지원(theme.css 변수, 다크 렌더링 검증 완료). CLAUDE.md에 **작업 사이클 규칙** 명문화(기능 단위 구현→즉시 검증·버그 수정→커밋, 사용자 보고 버그 최우선, 폴리시는 디자인 시안 후 일괄).

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
