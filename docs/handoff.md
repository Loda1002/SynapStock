# 새 대화 인수인계 (2026-07-24 기준)

> 대화가 길어져 새 세션으로 넘긴다. **새 대화는 이 문서 → [`docs/differentiation.md`](differentiation.md) →
> [`docs/preflight_review.md`](preflight_review.md) 순으로 읽고 시작할 것.** CLAUDE.md 는 항상 먼저 읽는다.
> 기능 현황·변경 이력 전체는 [`docs/FEATURES.md`](FEATURES.md)(주요 기능·이전/현재 대비·심사축 매핑).

## 0. 최신 상태 (2026-07-24 저녁 — 다음 대화가 먼저 볼 것)

- **402 Guard P0 전부 완료 — G0~G4, 전부 푸시됨.** 커밋: G0 `329885f` · G1 `89cde31` · G2 `9701d90` · G4 `9523d19` · G3 `7e2f3b8`. (의존성 때문에 **G4 를 G3 앞에** 구현 — 이중청구 방어=Memo+dedup, dedup 은 Memo 로 tx 가 유일해져야 드라이런에서 오탐이 안 난다.)
  - **G1** `payments/guard.py`: check_demand(하드 검사 8종: 주문번호·자산·종목·수취인·단위·금액·의도상한·건별한도, 차단코드 8종) + check_delivery(정산 후 온체인 잔액 재조회, balance_reader 주입, 미확인=pending 보류). 방어 위치 런타임 생성(guard.py:L{n}).
  - **G2** 결선: build_payment 가 authorize 앞에서 guard, AP2 authorize 가 asset 검증(결함 C), release/settle 예약추적으로 실패 시 한도 원복(결함 H), 엔진 GuardError→GUARD_BLOCKED + 라이브 배송 재조회 partial(결함 I).
  - **G4** Memo 바인딩(AT1:order_id:sig8) + verify_payment `<`→`!=`(exact, 결함 D) + expected_order_id Memo 대사(결함 E) + 브로커 used_signatures dedup + expires_at.
  - **G3** `scripts/red_team.py --report`: 공격 3종(청구위조 2변형/이중청구/정산미이행) 실제 경로 태움 + 매트릭스 + 정상 14건 오탐 0 동시 산출, `--attacker` 로 심사위원 악성주소 입력.
  - **검증**: 단위 test_guard 13·test_settlement 12 + 회귀 전종 + 엔진 스모크(예약회계 일치) + **localnet 풀사이클 라이브 PASS**(Memo 실린 매수6·매도1 온체인 확정, 교차검증 USDC 86.95==86.95/주식 0.5129==0.5129). red_team 실측 시도18·차단4·유출0.00·오탐0.
  - **부수 수정**: setup_devnet write_env cp949 크래시(.env UTF-8) 재현 차단 + 브로커 USDC 운용자본 500 지급(없으면 익절 매도 대금 부족으로 정산 실패 — 사전 미검증 결함).
- **킥오프 발표자료 반영 완료**: `docs/differentiation.md §7`(킥오프 심사 프레임 정합 — 검증·가드레일 레이어·당위성·자율성 축·김채린 4대 관전포인트) + `docs/submission.md §2-3`(당위성 소개서 블록) 신설.
- **다음 작업 후보(우선순위 순)**: ① **devnet 최종 실증** + explorer 증빙(현 증빙 전부 localhost) ② **제출물 초안**(README 재작성·`docs/pitch.md` 소개서·데모 대본) ③ G5(브로커 HTTP 402 분리, 매수 경로만 — 무거우면 스펙 대응표로 대체) ④ Cloud Run 배포 실행(사용자 gcloud). **디자인 무관 작업 우선(시안 ≈07-28 도착).** 상세는 §5-2, `docs/differentiation.md §2`.
- **세션 규칙(불변)**: 존댓말 하드 · 커밋마다 즉시 푸시 · 사용자 보고 버그 최우선 · 커밋 메시지 백틱 금지.

---

## 1. 지금 저장소 상태

- 브랜치 `main`, 워킹트리 clean, 원격 동기화됨 (https://github.com/Loda1002/SynapStock, **private**)
  ⚠ 저장소 이름은 2026-07-31 에 `SolanaAgent` → `SynapStock` 으로 바뀌었다(옛 URL 은 GitHub 가 리다이렉트한다).
- 테스트 4종 전부 통과: `scripts/test_dca_schedule.py` · `test_gemini_parse.py` ·
  `test_indicators.py` · `test_store.py` (실행: `.venv/Scripts/python.exe -m scripts.<이름>`)
- `python run_demo.py` (드라이런) 정상 · 웹 대시보드 정상(브라우저 검증: 콘솔 에러 0, 카드 11개,
  세션 시작→6틱→종료 회귀 통과)
- **Cloud Run 배포는 코드·런북 완료, 실행만 사용자 대기** ([`docs/deploy_cloud_run.md`](deploy_cloud_run.md))

### 2026-07-24 에 끝낸 것 (커밋 6개)

| 커밋 | 내용 |
|---|---|
| `4ed0581` | 심사 재현 차단 2건 — README 대표 명령 cp949 크래시, 백테스트 날짜 불일치 |
| `2ffcf13` | 백테스트 **매수후보유 벤치마크** + 시장노출 지표, 3종목 실측 |
| `0787d33` | [`docs/preflight_review.md`](preflight_review.md) — 종합 검토(조사 7축·감사 64건·검증 32건) |
| `b0a3280` | **공개 배포 차단 5건**(보안) — 공격 시나리오 12종 차단 확인 |
| `006c8bf` | CLAUDE.md 현재 상태·진행 로그 반영 |

## 2. 반드시 알고 시작해야 할 사실 (실측·검증 완료)

1. **전략이 벤치마크에 진다** (직접 실행한 수치, 80봉·예산 100 USDC)
   | 종목 | 전략 | 매수후보유 | 초과 | 노출 |
   |---|---|---|---|---|
   | AAPL | +4.90% | +30.19% | **−25.29%p** | 21% |
   | TSLA | +11.22% | +2.73% | **+8.49%p** | 65% |
   | NVDA | +20.15% | +25.82% | −5.67%p | 44% |

   → **수익률로 경쟁하면 진다.** 데모 대표 종목은 TSLA 로.
2. **손절 코드가 없다** → 모든 백테스트 승률 100%(진 거래는 포지션에 잠김). 승률 단독 인용 금지.
3. **Gemini 가 구조적으로 수익을 만들 수 없다** — 규칙 신호가 있어야 매매가 개시되고 AI 는 그 위에서
   보류만 가능(`agents/trading_agent.py`). 심사 2축(AI 활용도) 정량 근거가 현재 0.
4. **토큰화 주식은 메인넷에 실재하나 devnet 에는 없다** — xStocks AAPLx 민트
   `XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp`(Token-2022) 온체인 확인, Jupiter 견적이 Pyth
   가격과 0.4% 이내 일치. 같은 민트를 devnet 조회하면 `null`. → **현 자체발행 토큰 설계 유지가 정답.**
   단 permanentDelegate·pausable 등 발행사 사후 통제권 상주 → **"퍼미션리스" 단정 금지.**
5. ~~**직전 Solana x402 해커톤 수상작 16개 중 AI 트레이딩 봇 0건.**~~ **⚠ 이 명제는 2026-07-27
   조사에서 반증됐다 — 출처 미확인 + 반례 3건**(Peaks·Clawpump·Agent Arc). **제출물 어디에도
   인용하지 않는다**([`docs/reports/competition_research_reconcile.md`](reports/competition_research_reconcile.md)).
   정확한 명제는 *"단일 전략 봇 언어로 말하면 떨어진다"* 이고, **매매 기능을 숨길 필요는 없다.**
   포지셔닝 전환 결론 자체는 유지된다 ([`docs/differentiation.md`](differentiation.md)).
6. 규제: 토큰화 주식=증권(SEC 2026-01-28), 한국 제도 시행 2027-02-04, 자동매매는 반드시
   **self-custody 도구**(사용자 지갑·사용자 한도·즉시 정지)로 설명. "우리가 돈을 굴린다" 금지.

## 3. 사용자 결정 사항 (2026-07-24)

- ✅ 배포 차단 버그 5건 먼저 → **완료**
- ✅ 전략은 **손절+트레일링 + Gemini 사이징 재량**까지 (전면 개편은 안 함)
- ✅ **pay.sh 연동을 지금 준비**한다
- 디자인 시안 ≈7/28 도착 → 그때까지 디자인 무관 작업만. 시안은 `web/static/css/theme.css` 변수 +
  `web/static/js/app.js` `DEFAULT_LAYOUT` 두 창구로만 흡수

## 4. ⚠ 사용자가 직접 확인해야 할 것 (미해결)

1. **대회 공식 심사기준·제출물 규격 원문** — 공식 사이트가 JS 렌더링이라 조사 실패.
   현재 근거는 킥오프 세션 메모뿐이다. 브라우저로 공식 사이트·디스코드 공지를 열어 캡처 필요.
2. **"공식 과제 = x402 로 클라우드 API 결제, 지정 인프라 pay.sh"** 보도의 사실 여부.
   CLAUDE.md 제약 6(자체 x402 허용)과 충돌하고, 이 항목만 적대적 검증이 실행되지 못했다.

## 5. 다음 대화에서 할 일 (우선순위)

**방향이 바뀌었다. [`docs/differentiation.md`](differentiation.md) 를 반드시 먼저 읽을 것.**
"AI가 알아서 매매한다"는 흔한 봇으로 분류돼 탈락하므로, **"402 Guard — 에이전트 지출 승인 게이트"**
로 재포지셔닝한다. 근거는 우리 코드의 실증된 취약점이다(아래 §5-1).

### 5-1. 재포지셔닝의 근거 — 직접 확인한 결함

| 결함 | 위치 |
|---|---|
| 구매자가 브로커 청구서를 **한 글자도 검증하지 않음** | `agents/trading_agent.py:330-346` |
| AP2 `authorize()` 가 `pay_to` 를 받고도 **검사하지 않음** | `payments/ap2_mandate.py:112-129` |
| `allowed_asset` 이 **한 번도 읽히지 않는 죽은 필드** | `ap2_mandate.py:33` |
| `exact` 스킴인데 금액 비교가 `<` (초과지불 통과) | `x402_solana.py:180` |
| Memo 없음 → 대사 키·리플레이 방어 부재 | grep 0건 |
| **HTTP 402 가 코드에 한 줄도 없음** | `engine.py:750` 인프로세스 호출 |
| **에이전트가 자기 허가서를 자기 키로 서명** (CLAUDE.md 규칙 10 위반) | `engine.py:308,313,314` |

→ **공격 시나리오**: 악성 브로커가 한도 안쪽 금액(45 한도에 44.94)으로 청구하며 수취인만
바꾸면 통과된다. **한도는 지켜지고 전액을 잃는다.** 이게 데모의 심장이다.

### 5-2. 작업 순서

| 순위 | 작업 | 시간 | 근거 |
|---|---|---|---|
| ✅ | **G0 사용자 키 분리** (`secrets/user.json`) — **완료 (커밋 329885f)** | 1.5h | 가장 싸고 가장 치명적. 코드 읽는 심사위원에게 위임 서사가 무너진다 |
| 2 | **G1 `payments/guard.py`** + **G2 결선** | 9h | 차별화의 본체 |
| 3 | **G3 `scripts/red_team.py`** (공격 3종 + 매트릭스) | 4h | "공격해보라"로 증명 |
| 4 | **G4 Memo 바인딩 + exact 정합** | 5h | 대사 키·리플레이 방어 |
| 5 | **G5 브로커 HTTP 402 분리 (매수만)** | 6~12h | 기술 심사 1순위. 무거우면 스펙 대응표로 정직하게 대체 |
| 6 | **devnet 실증** + explorer 증빙 | 3h | 증빙이 전부 localhost |
| 7 | **제출물** — `docs/pitch.md`, README 재작성, 데모 대본 | 8h+ | 제출물 3종이 아직 0개 |
| 8 | **7/31 public 전환** + LICENSE + 시크릿 스캔 | 1h | 마감 당일 전환은 "링크 404" 리스크 |

**잘라낸 것**: 전략 고도화(손절·트레일링·Gemini 사이징 — 수익률로는 어차피 벤치마크에 지므로
차별화 축을 바꾸는 게 우선), 멀티 종목, Firebase Auth, P3 에이전트 챗, 설정·리플레이 페이지,
가드 UI 카드(P2 — 터미널+기존 대시보드로 촬영 가능), 프롬프트 인젝션 데모(자작극이라 역효과).

> ⚠ **공수 주의**: 1차 명세 32h → 저장소 대조 재산정 **44~50h**. P0(1~4번, 22h)만으로도
> 제출물 작업과 충돌한다. 5번 이하는 P0 완료 후 남은 시간으로만 판단할 것.

## 6. 작업 규칙 (CLAUDE.md 발췌 — 새 대화에서도 동일)

- **사이클**: 기능 구현 → 즉시 검증 → 커밋 → `git push origin main` (푸시는 상시 승인, 묻지 않음)
- 사용자가 보고한 버그는 새 기능보다 항상 우선
- 디자인·폴리시는 기능마다 하지 않는다 (시안 도착 후 일괄)
- 승인 창이 뜨는 도구 실행 전, 쉬운 한국어로 무엇을·왜 먼저 설명
- 커밋 메시지에 백틱(`` ` ``)을 쓰지 말 것 — bash 명령 치환으로 내용이 잘린다.
  여러 줄 메시지는 `git commit -F -` + heredoc 사용

## 7. 개발 환경 함정 (재확인됨)

- Python **3.10.8** 고정(solana-py 0.38). 배포 컨테이너는 3.11 — 로컬 검증이 3.11 을 안 거친다
- 콘솔 인코딩: `config.py` 의 `enable_console_safe_output()` 이 모든 진입점에서 자동 적용된다.
  새 스크립트가 `config` 를 임포트하지 않으면 cp949 크래시가 재발하므로 `import config` 를 넣을 것
- 백테스트 Gemini 런은 **순차 실행**(동시 실행 시 429 로 폴백 오염)
- localnet 검증기는 WSL 에서 실행 (`scripts/start_localnet.bat`)
