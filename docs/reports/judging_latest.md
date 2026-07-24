# 402 Guard 심사 자가평가 리포트
> 생성: 2026-07-24 09:54 KST
> 근거 스냅샷: docs/reports/_evidence_20260724_183844.json
> 심사 기준 출처: 킥오프 전사(docs/hackathon_essentials_0721.md §1) + docs/FEATURES.md §5. 신청 페이지 원문 미확보(docs/handoff.md §4).

## 종합 요약
| 축 | 등급 | 한 줄 근거 |
|---|---|---|
| ① 혁신성·UX·상업성 | 중 | 402 Guard 재포지셔닝 서사·코드 결함 실증·red_team KPI(유출 0.00 USDC)는 강하나, 웹 첫 화면 KPI·README 정문·상업성 서사가 미반영 |
| ② AI 활용도 (Gemini) | 중 | Gemini 실 API 결선·구조화 프롬프트·견고 파서(테스트 11건)는 확인되나, 수익 기준 AI 우위 없음(낙폭 축소만)·devnet 라이브는 rule 판단 |
| ③ 기술·인프라 연동 | 강 | 실 SPL/USDC TransferChecked 정산·AP2 ed25519 서명·guard 서명 직전 결선·devnet 온체인 증빙·red_team 유출 0(HTTP 402만 미구현) |
| ④ 실제 구동 | 강 | devnet 10 tx settled + 교차검증 PASS·localnet 7건·테스트 6종 통과(단일 세션·배포 URL·데모 영상은 미확인) |

## 축① 혁신성·UX·상업성
**등급**: 중

**부합 근거**
- 재포지셔닝(402 Guard — 파는 쪽이 아닌 '사는 쪽' 결제 게이트)이 x402 스펙의 counterparty verification 부재 지적으로 명확히 정의 — `docs/differentiation.md:38-45, :33-34`
- 재포지셔닝 근거인 코드 결함 A~I가 file:line으로 실물 재현(무검증 서명, authorize pay_to 미검사, allowed_asset 죽은 필드, exact인데 `<` 비교, Memo·HTTP402 부재, 자기키 자기서명) — `docs/differentiation.md:15-25`
- 방어 로직 코드 실재: check_demand/check_delivery, 차단코드 6종(GUARD_AMOUNT_MISMATCH 등), exact 금액비교 `!=`, Memo 바인딩 `AT1:{order_id}` — `payments/guard.py:99,157, :37-42` · `payments/x402_solana.py:218,220, :39`
- 공격 콘솔이 CLI에 실재(심사위원이 악성 수취인 pubkey를 `--attacker`로 직접 입력, '자작극 아니냐' 방어 UX) — `scripts/red_team.py:16,55-59,311`
- red_team 실행 KPI 시도18·차단4·유출 0.00 USDC·오탐0 실측(정상거래 14건 오탐0 동시) — `_evidence_20260724_183844.json red_team.kpi / raw_tail`
- 당위성(왜 카드망이 아닌 블록체인·왜 사람 없는 자율결제)이 킥오프 3세션 근거로 문서화 — `docs/submission.md:68-83` · `docs/differentiation.md:180-197`

**갭**
- 핵심 UX 주장('첫 화면 10초 안에 봇→지출통제 인식 전환')이 웹에서 미구현 — data-card 11종에 guard/KPI 카드 없음, KPI는 터미널(`red_team --report`)에만 존재 (`web/static/index.html:38-172`). 배포 URL/대시보드를 열면 여전히 캔들차트+포지션이 먼저 보임
- README 정문이 'AutoTrader Agent — 조건 기반 자율 주식매매'로 시작하고 '402 Guard' 언급 0건 — differentiation §51 안티패턴('제목만으로 트레이딩 봇 서랍') (`README.md:1-6`, grep '402 Guard'=0)
- DEFAULT_LAYOUT이 수익률·포지션 중심 배치(price→session→position→budget→pnl→valuation…), differentiation §101 재배치 미반영 (`web/static/js/app.js:810-811`)
- 상업성 근거 부족 — 타겟 고객·수익모델·수수료율(0.3% vs 0.1% 미결정) 확정 서술이 문서·화면 어디에도 없음. `total_fees` 필드·주석만 존재, docs/pitch.md 부재 (`web/engine.py:86`, `docs/submission.md:85-90`)
- 웹 공격 콘솔/입력창 부재 — 공격 배율·악성 수취인 입력이 CLI(`--attacker`)에만 존재, 데모 영상 라이브 UX 어필 약화 가능

**개선 TODO**
- `data-card="guard"` KPI 카드 신설(시도·차단·유출 0.00 USDC·오탐 0) + DEFAULT_LAYOUT 최상단 배치, red_team/엔진 실측값을 SSE로 바인딩 (differentiation G8을 P2로 승격)
- DEFAULT_LAYOUT에서 pnl·valuation·trades·position을 배열 하위로 이동(배열 순서만 수정, `app.js:810-811`)
- README 첫 3줄을 differentiation §53 스펙('# 402 Guard — 에이전트 지출 승인 게이트' + curl 402 + red_team 명령)으로 교체, 제목 첫 단어에서 'AutoTrader' 제거
- 수익모델·타겟 고객을 docs/pitch.md(또는 submission §2)에 구체 수치로 확정, 수수료율 결정 + `total_fees` 코드와 연결해 '수익모델 증명' 서사 완성
- (여유 시) 웹에 공격 토글/입력창('가드 없는 일반 에이전트' 라벨, §107) 추가 — 심사위원이 화면으로 악성 수취인·금액 입력

## 축② AI 활용도 (Gemini)
**등급**: 중

**부합 근거**
- Gemini 실 API 호출로 매수/매도/보류 결정(지연 임포트 google.genai, generate_content, response_mime_type application/json, temperature 0.2) — `agents/gemini_decider.py:183-215,224-304`
- Gemini가 라이브 실행 경로(웹 엔진·run_demo)에 실결선, 키가 있으면 두뇌로 사용(dca는 미사용) — `web/engine.py:296-312,338` · `run_demo.py:113-125,153`
- AI 두 번째 활용점 — 데일리 브리핑을 Gemini가 한국어 리포트로 생성(실패 시 템플릿 폴백) — `web/briefing.py:49-72`
- 구조화 입력(MA5/MA20·최근5봉 등락률·변동성·평단 손익률·직전행동 회고·ta_mode 시 TA 신호블록) — `agents/gemini_decider.py:20-52,240-282`
- 3단계 견고 파서(원문 파싱→이스케이프 복구→정규식 추출) + 형식위반 1회 재요청 + 429 쿨다운(retryDelay 존중), 단위테스트 11건 전부 통과 — `agents/gemini_decider.py:100-180,217-222,286-290` · evidence tests.modules[test_gemini_parse].ok_count=11
- Gemini 호출 실패 시 동일 지표 규칙으로 폴백, source=rule-fallback 표기(데모 무중단) — `agents/trading_agent.py:192-205`
- Gemini가 실제 판단 주체였던 온체인 세션 존재(localnet, decision_source=gemini) — `artifacts/tx/20260722_1644…(gemini:3)` · `20260722_2125…(gemini:4,rule-fallback:1)` · `20260723_1355…(gemini:6)`
- 무료 티어 gemini-flash-lite-latest(개발자 키), Vertex/ADK 구조화 없음(제약 7 준수) — `config.py:88-92`

**갭**
- 수익률 기준 AI 우위 입증 증거 없음 — Gemini 정량기여가 낙폭(MDD) 축소에 국한, 수익 기여는 한계적/음수. strict-ta 4.95% vs 규칙 4.90%(+0.05%p), 재량 없는 strict 4.29%(규칙 대비 −0.61%p 열위) (evidence backtests)
- brain=gemini 백테스트가 전부 AAPL 단일 종목·36봉 규모뿐 — TSLA/NVDA는 규칙 백테스트만 존재(Gemini 정량 근거 부재)
- 심사 대상 devnet 라이브 런의 전 거래 decision_source=rule(Gemini 0건) — devnet에 '가장 강한 AI가 결제했다' 온체인 증빙이 아직 없음 (`artifacts/tx/20260724_1643…` rule:5, 스팟체크 재확인)
- AI 매매 재량 상한이 코드 불변식이 아니라 프롬프트 텍스트(MODE_RULES) 의존 — 실제 강제는 `_sanitize`의 매수액 클램프·무보유 매도차단뿐 (`gemini_decider.py:75-85` · `trading_agent.py:315-324`)

**개선 TODO**
- devnet 라이브 재현을 Gemini 판단 주체로 1회 실행해 decision_source=gemini 온체인 증빙을 devnet에 남긴다(②+④ 동시 강화)
- AI 정량기여를 '수익률'이 아니라 '낙폭 축소'로 프레이밍: rule MDD 7.31 vs gemini-trend 4.84~5.00(약 34% 축소) 비교표를 소개서·데모에 명시
- brain=gemini 백테스트를 TSLA·NVDA로 확장 실행(순차, 429 오염 방지)해 정량 근거를 3종목으로 확대
- 데모 영상·타임라인에 Gemini가 만든 판단 reason 한국어 한 문장이 실제로 찍히는 장면을 넣어 AI 기여를 정성적으로 가시화
- 손절·트레일링·Gemini 사이징 재량 추가(win_rate 100% 착시 해소, AI가 수익 축에서도 기여할 여지 — 사용자 승인된 다음 작업과 정합)

## 축③ 기술·인프라 연동
**등급**: 강

**부합 근거**
- 결제 레이어가 실 Solana SPL TransferChecked 트랜잭션을 생성·서명·오프라인 검증(USDC를 정산 자산으로 실제 전송) — `payments/x402_solana.py:81-126,146-225`
- x402 exact 스킴 금액 검증이 정수 오차 0으로 초과·부족 모두 거부(amount != expected → 차단) — `payments/x402_solana.py:218-220`
- AP2 mandate가 실 ed25519 서명(solders)으로 사용자 위임 한도 표현·검증(per-trade·budget 한도, allowed_asset 실검증) — `payments/ap2_mandate.py:51-64,115-140(asset 122)`
- A2A + x402 3단계 메시지(payment-required/submitted/completed)가 exact·base units·payTo로 결제 협의 흐름 구성 — `shared/a2a_messages.py:19-107`
- 402 Guard가 AP2 authorize '앞'에 결선돼 서명 직전 청구서 4항목(금액·수취인·자산·주문번호)을 대조하고 위반 시 서명 자체 차단(유출 0) — `agents/trading_agent.py:338-346` · `payments/guard.py:99-146`
- 온체인 Memo `AT1:{order_id}:{sig8}` 바인딩으로 대사 키+리플레이 방어, verify_payment에서 Memo 검증 — `payments/x402_solana.py:39-40,103-104,182-193`
- red_team 적대적 검증 KPI 시도18·차단4·유출 0.00 USDC·오탐0(정상 14건 온체인 재조회 통과) — evidence red_team.kpi
- devnet 라이브 온체인 증빙 실재(매수 payment_tx 3MvY…·delivery 45WH… + explorer cluster=devnet, cross_check usdc_ok/stock_ok=true) — `artifacts/tx/20260724_1643_solana-devnet_live_buy.json`(스팟체크 재확인)
- 단위·통합 테스트 6종 전부 통과(settlement 11·guard 13 등), guard 차단코드 6종 구현 — evidence tests.all_pass=true

**갭**
- HTTP 402 서비스 미구현: http_402_status_code=0. 코드의 '402'는 web/server.py의 HTTP 401/404/400/409와 프로토콜명 'x402'뿐이며, 결제 레이어는 내부 Python dict(A2A) 흐름으로 실 HTTP 402 응답을 서빙하지 않음. 제품명 '402 Guard'가 HTTP 402 시맨틱을 차용(CLAUDE.md도 G5 브로커 HTTP 402를 '여유 시'로 미완 표기)
- pay.sh 미연동 — 공식 기준상 필수 아니나 3축 가점 여지 미회수(differentiation P1/G6)
- devnet 증빙이 1세션에 국한(아티팩트 devnet 1·localnet 7). ※단, 해당 devnet 아티팩트 내부에 매도 tx(side=sell, order ord_409cae1604)가 존재해 매수4+매도1은 확인됨 — verdict는 이 gaps를 오히려 과소 기술로 평가
- 토큰화 주식·USDC 민트가 devnet 자체 발행분(usdc 8L9f…, stock EC3z…)이라 실물 자산 아님 — 축③(결제 레이어 실사용)엔 무관하나 '실물 증권 결제' 오인 방지 로드맵 표기 필요

**개선 TODO**
- 브로커 측 실 HTTP 402(status 402 + X-PAYMENT/WWW-Authenticate 헤더 + PaymentRequirements JSON 바디) 서빙 + 구매 에이전트 402 수신·재요청 경로를 web 레이어에 추가(G5)해 'x402' 명칭과 HTTP 402 시맨틱 일치
- devnet 매수·매도 풀사이클을 1회 더 실행해 매도 devnet tx·explorer 링크·교차검증까지 아티팩트로 아카이빙
- pay.sh 최소 연동(디스코드 지원 채널)을 여유 시 붙이거나, 붙이지 않으면 소개서에 '자체 x402+AP2+A2A 스택이 공식 병렬 예시에 정합'을 명시해 미연동을 설계 선택으로 방어
- README/소개서 아키텍처 절에 온체인 증빙 3점(devnet explorer 링크·red_team KPI·교차검증 PASS)을 심사 4축 ③에 직접 매핑

## 축④ 실제 구동
**등급**: 강

**부합 근거**
- devnet 라이브 풀사이클: 5거래(매수4+매도1) 각 payment_tx+delivery_tx = 온체인 10 tx 전부 confirmed=true/status=settled, explorer cluster=devnet, rpc api.devnet.solana.com(목업 아님) — `artifacts/tx/20260724_1643_solana-devnet_live_buy.json`
- devnet 온체인 교차검증 PASS: usdc_net_out_onchain −4.9 == expected −4.90, usdc_ok/stock_ok=true(체인 잔액 차분 검증) — 동 아티팩트 cross_check(스팟체크 재확인)
- localnet 라이브 증빙 7건, 구조 동일(rpc 127.0.0.1:8899, tx서명·explorer·settled, cross_check ok, usdc 90 실지출·주식 0.5146 실수취) — `artifacts/tx/20260722_1547_solana-localnet_live_buy.json` + evidence by_network.solana-localnet:7
- 정산 검증이 온체인 실조회(confirm_transaction + get_token_account_balance) — `payments/x402_solana.py:281,295,325`
- red_team이 실 결제 경로를 그대로 태워 공격4 전원 차단·정상14 온체인 재조회 통과, KPI 유출 0 — `scripts/red_team.py:298-303` + evidence red_team.kpi
- 테스트 6종 전부 통과(rc=0): guard13·settlement11·indicators44·gemini_parse11·dca7·store18 — evidence sections.tests

**갭**
- devnet 라이브가 단일 세션(1 아티팩트, 07-24 하루)·단일 종목(tAAPL)에 그침 — 재현성 증빙이 localnet 7건 대비 얇음(다른 날짜/종목 반복 미확인)
- 라이브 배포 URL 없음(Cloud Run 미배포) — 배포 환경 실 tx 재현 미확인, '라이브 배포 URL 가산점' 미충족
- 데모 영상(제출물 ③, 'AI 에이전트 결제 전 과정 시연')이 저장소에 없음 — 영상 산출물 실물 미확인
- devnet 라이브 전 거래 decision_source=rule(Gemini 0건) — 라이브 devnet에서 AI 두뇌가 실제 매매를 구동한 증빙 없음(규칙엔진 실행)
- 심사자 대면 문서 FEATURES.md §5 stale: line145 '④=devnet 증빙 필요(현재 전부 localhost)', line121 '증빙 7건 전부 localhost' — 실제(devnet 1건 완료)보다 과소 기술(문서-실물 불일치)

**개선 TODO**
- devnet 라이브 세션을 다른 날짜·다른 종목으로 2~3회 더 실행하고 각 explorer(cluster=devnet) 링크 생존 확인(재현성 두껍게)
- FEATURES.md §5 line145·line121 갱신: 'devnet 증빙 필요/전부 localhost' → devnet 라이브 1건 완료(artifacts/tx/20260724_1643…, 온체인 10 tx·교차검증 PASS) 반영
- devnet 라이브를 Gemini brain 모드로 최소 1회 실행해 ④(라이브)와 ②(AI 활용도)를 동시 뒷받침
- Cloud Run 배포(docs/deploy_cloud_run.md) 후 배포 URL에서 실 tx 1건 재현·아카이빙('배포 URL 가산점' + 데모 백업 경로)
- 데모 영상 대본에 devnet explorer 링크 육안 대사(payment/delivery tx) 장면을 넣어 '진짜 트랜잭션' 심사 포인트를 화면으로 증명

## 기준 불일치·미검증
- **공식 신청 페이지 원문 미확보**(docs/handoff.md §4 — 사이트 JS 렌더링으로 조사 실패). 아래 모든 축의 기준 해석은 킥오프 전사(hackathon_essentials_0721.md §1) + FEATURES.md §5 기반이며 공식 페이지와 다를 수 있어 단정 불가.
- **축① 명칭**: 전사·CLAUDE.md 확보 공식기준 모두 '①혁신성·UX'로 적어 '상업성'이 이 축에 명시 포함되는지 불확실. 본 평가는 상업성을 축① 하위로 포함해 채점 — 공식 기준이 상업성을 제외하면 등급이 '강'에 근접할 수 있음.
- **트랙 라벨 미대조**: Track C(Multi-Agent Commerce) 및 A/B/D 명칭이 저장소에만 있고 공식 폼 원문과 미대조(docs/submission.md:34). README 정문도 'Track C'로 자칭하나 공식 트랙 정의와 미확인.
- **'재현 데모 심사, 목업 제외' 해석**: 첫 화면 KPI가 화면 산출물이어야 가점인지, 터미널(red_team --report) 산출물로 충분한지 공식 해석 미확정.
- **축② Google Cloud AI 스택/Vertex/ADK**: 전사는 '호출 수준 충분·ADK/Vertex 가산점 아님'(hackathon_essentials §24, 제약 7)이나 CLAUDE.md 공식 문구는 ②에 'Google Cloud AI 스택·에이전트 프레임워크'를 명시 — 공식 루브릭이 Vertex/ADK를 실제 가중하면 의도적으로 최소화한 현 구성(개발자 키·무 Vertex·무 ADK)이 낮게 채점될 여지. 'AI 기여 정량화' 요구 강도도 미확인.
- **축③ 인프라 예시 가중치**: 예시(USDC/Solana Pay/pay.sh · AP2/A2A/x402)의 상대 가중치와 Solana Pay/pay.sh 특정 요구 가능성 미검증. 심사자가 x402를 'HTTP 402 결제 프로토콜'로 엄격 해석하면 내부 dict 기반 x402 흐름이 감점 소지 — 전사 기준상으론 자체 스택이 정합.
- **축④ 라이브 요건**: '라이브 배포 URL = 가산점 vs 필수', 데모 영상 규격/길이, devnet 라이브 필수 여부 미검증(현재 devnet+localnet 양쪽 증빙 보유해 어느 해석이든 최소 요건 충족).
- **제출 규격 일반**: 제출물 세부 규격·데모 영상 길이 상한·마감 시각은 디스코드 공지로 재확인 필요(등급엔 무영향, 제출 정합성 확인용).

## 적대 검증 메모
- **반려 0건 — 모든 근거가 실물로 확인됨.** 4개 축 전부 verdict.unsupported=[], fabrication_found=false. 검증 증거 총 38건(①10·②12·③9·④7)이 전부 원본 file:line / JSON 키 / 아티팩트로 재확인 통과(증거 JSON 의존이 아니라 원본 소스 직접 대조).
- **검증자 부기(불일치 아님, 참고)**: ① guard_block_codes.count=22는 grep 라인 매치 수이고 '차단코드 6종'은 DEMAND_CODES 정의 개수로 정확 — 상충 아님. ③·④ 일부 gaps가 'devnet 매도 증빙 미확인'이라 서술했으나 실제 devnet 아티팩트에 매도 tx(side=sell)가 존재 — gaps가 오히려 강점을 과소 기술한 것이며 근거 과장은 없음. ④ claim7의 "'402'가 guard.py 도크스트링에만 등장"은 loose(x402_solana.py 다수 라인에도 프로토콜명 등장)하나, HTTP 402 상태코드 0건은 정확하고 이는 결함을 인정하는 방향이라 ④ 등급을 부풀리지 않음.
- **종합 작성관 자체 스팟체크(본 리포트 작성 시 재확인)**: `README.md:1` 'AutoTrader Agent — 조건 기반 자율 주식매매'·'402 Guard' grep=0, `app.js:810-811` DEFAULT_LAYOUT이 price→session→position→budget→pnl→valuation 순(수익·포지션 우선), `artifacts/tx/20260724_1643…` 5거래 전부 status=settled·decision_source=rule·network=solana-devnet·cross_check usdc_ok/stock_ok=true 재확인. 부정형(미구현·미반영) 주장까지 실물로 일치.

## 증거 스냅샷
docs/reports/_evidence_20260724_183844.json — Bash 로 열어 대조: cat "docs/reports/_evidence_20260724_183844.json"
