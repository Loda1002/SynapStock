# Cloud Run 배포 런북 — AutoTrader Agent

> **사용자가 직접 실행하는 문서다** (GCP 인증·과금 명령은 Claude 가 대행하지 않는다 — CLAUDE.md 규칙).
> 명령은 전부 **PowerShell 기준**이고 위에서 아래로 순서대로 실행하면 된다.
> 소요: 처음부터 끝까지 약 30~40분 (대부분 대기 시간).
>
> 구성 요약: **Cloud Run**(FastAPI 서버, 상시 1 인스턴스) + **Firestore**(세션·거래·브리핑 영속)
> + **Secret Manager**(지갑키 2개 파일 마운트, Gemini API 키 환경변수 주입).
> 로컬에 Docker 가 없어도 된다 — `--source .` 배포가 Cloud Build 에서 원격 빌드한다.

---

## 0. 사전 준비 (1회)

### 0-1. Google Cloud SDK(gcloud) 설치 — 현재 이 PC에 없음

https://cloud.google.com/sdk/docs/install 에서 **Windows 64bit 설치 관리자**를 받아 실행.
설치 마지막에 "Run gcloud init" 체크는 꺼도 된다(아래에서 직접 로그인).
설치 후 **PowerShell 을 새로 열고** 확인:

```powershell
gcloud --version
```

### 0-2. 로그인 + 프로젝트 만들기

```powershell
gcloud auth login
```

브라우저가 열리면 GCP $300 크레딧을 받은 구글 계정으로 로그인.

```powershell
# 프로젝트 ID 는 전역 유일해야 한다 — 뒤에 숫자 등을 붙여 조정
$PROJECT_ID = "autotrader-agent-2026"
$REGION = "asia-northeast3"   # 서울

gcloud projects create $PROJECT_ID
gcloud config set project $PROJECT_ID
```

**결제 계정 연결(필수)**: https://console.cloud.google.com/billing 에서 새 프로젝트에
결제 계정을 연결한다($300 무료 크레딧이 걸린 계정). 연결 없이는 API 활성화가 막힌다.

### 0-3. 필요한 API 켜기 (몇 분 걸릴 수 있음)

```powershell
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com secretmanager.googleapis.com
```

---

## 1. Firestore 데이터베이스 생성 (1회)

```powershell
gcloud firestore databases create --location=$REGION
```

- Native 모드 기본값 그대로. 컬렉션은 서버가 알아서 만든다
  (`autotrader_sessions` / `autotrader_trades` / `autotrader_briefings` / `autotrader_state`).
- 무료 한도(일 읽기 5만·쓰기 2만)로 해커톤 사용량은 충분히 덮는다.

## 2. Secret Manager — 지갑키·Gemini 키 등록 (1회)

지갑키 **3개**를 **로컬 `secrets/` 파일 그대로** 올린다(로컬·배포가 같은 지갑 = devnet 검증 일관성).

> ⚠ **`cd` 는 반드시 `-LiteralPath` 로** 쓴다. 이 저장소 경로에는 `[Google Cloud X Solana]` 처럼
> 대괄호가 들어 있고, PowerShell 의 `Set-Location -Path` 는 대괄호를 **와일드카드(문자 집합)** 로
> 해석해 "경로를 찾을 수 없음"으로 실패한다(2026-07-26 실측). 따옴표만으로는 해결되지 않는다.

```powershell
Set-Location -LiteralPath "C:\Users\Tedd\Desktop\홍익대학교\프로젝트\[Google Cloud X Solana] AI 에이전틱 해커톤\solana-agent"

gcloud secrets create autotrader-trading-wallet --data-file=secrets\trading.json
gcloud secrets create autotrader-broker-wallet --data-file=secrets\broker.json
gcloud secrets create autotrader-user-wallet --data-file=secrets\user.json
```

> **`user.json` 도 올리는 이유**: 이건 AP2 위임장(mandate)에 서명하는 **사용자 키**다. 등록하지
> 않으면 컨테이너가 재시작마다 새 임의 키를 만들어 쓰므로(`engine._load_or_create_user_key`),
> "사용자가 서명한 위임장"의 주체가 재시작마다 바뀌어 로컬 증빙과 어긋난다.

> **왜 파일 마운트가 아니라 환경변수로 주입하는가**: Cloud Run 의 시크릿 볼륨은 *디렉터리*
> 단위라, 시크릿 2개를 같은 `/secrets` 에 얹는 구성은 리비전 생성이 거부되거나 한쪽만
> 실물화될 수 있다. 코드는 `WALLET_DIR` 하나만 보므로 디렉터리를 쪼갤 수도 없다.
> 그래서 `TRADING_KEYPAIR_JSON` / `BROKER_KEYPAIR_JSON` 환경변수 경로를 함께 지원한다
> (§4 배포 명령이 이 방식을 쓴다). 라이브 세션은 키가 없으면 **즉시 실패**하므로,
> 예전처럼 랜덤 지갑으로 조용히 진행되다 잔고 부족으로 실패하는 일은 없다.

**조작 API 접근 토큰**도 만들어 둔다(공개 URL 보호 — 아래 §4에서 주입):

```powershell
$TOKEN = -join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
Set-Content -Path control.token -Value $TOKEN -NoNewline
gcloud secrets create autotrader-control-token --data-file=control.token
Remove-Item control.token
Write-Host "대시보드 접속 주소 뒤에 붙일 값: #token=$TOKEN"   # 이 값을 따로 저장해 둘 것
```

Gemini API 키는 **줄바꿈 없는 파일**로 만들어 올리고 바로 지운다
(`echo` 파이프는 줄바꿈이 붙어서 키가 오염된다 — 주의):

```powershell
Set-Content -Path gemini.key -Value "여기에_발급받은_키" -NoNewline
gcloud secrets create autotrader-gemini-key --data-file=gemini.key
Remove-Item gemini.key
```

## 3. 서비스 계정 권한 (1회)

Cloud Run 이 쓰는 기본 컴퓨트 서비스 계정에 **역할 3개**를 준다 — Firestore 읽기쓰기, 시크릿 접근,
그리고 **소스 빌드 실행**이다:

```powershell
$PN = gcloud projects describe $PROJECT_ID --format="value(projectNumber)"
$SA = "$PN-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA" --role="roles/datastore.user" --condition=None --format=none
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor" --condition=None --format=none
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA" --role="roles/cloudbuild.builds.builder" --condition=None --format=none

# 확인 — 위 3개 역할이 보이면 통과
gcloud projects get-iam-policy $PROJECT_ID --flatten="bindings[].members" --filter="bindings.members:$SA" --format="value(bindings.role)"
```

> **`cloudbuild.builds.builder` 를 빠뜨리면 §4 배포가 시작도 못 한다** (2026-07-26 실측):
> `INVALID_ARGUMENT: Invalid build request. could not resolve source: googleapi: Error 403`.
> Google 이 소스 배포의 기본 빌드 실행 계정을 컴퓨트 기본 계정으로 바꾼 뒤로, 이 역할이 없으면
> 업로드된 소스를 읽지 못한다. 이 역할에 소스 버킷 읽기·빌드 로그 쓰기·이미지 푸시가 함께 들어 있다.
>
> `--condition=None` 은 "조건을 지정할까요?" 대화형 질문을 막고, `--format=none` 은 정책 YAML
> 전문이 수십 줄 쏟아지는 것을 막는다. IAM 전파에 최대 1분이 걸리니, 직후 배포가 같은 403 이면
> 1분 뒤 한 번 더 시도한다.

## 4. 배포

저장소 루트에서 (첫 배포는 빌드 포함 5~10분, "Artifact Registry 저장소를 만들까요?" 물으면 `Y`).

**서비스 이름은 `synapstock`** — 배포 URL 에 그대로 들어가고, 나중에 바꾸면 새 URL 이 생긴다
(2026-07-26 확정. 심사 기준에 이름 항목은 없고, 제품명 `402 Guard` 는 화면·소개서·README 가 담당).

아래는 **한 줄 명령**이다. 여러 줄 백틱(`` ` ``) 형태는 백틱 뒤에 공백이 하나만 붙어도 깨지므로
복붙 경로에서는 한 줄이 안전하다. 맨 앞 `Test-Path` 는 **작업 위치가 저장소 루트가 아닐 때
`--source .` 이 엉뚱한 폴더를 통째로 업로드하는 사고**를 막는 안전장치다.

```powershell
if (-not (Test-Path -LiteralPath ".\Dockerfile")) { "중단: 저장소 루트가 아닙니다. 현재 위치: $(Get-Location)" } else { gcloud run deploy synapstock --project $PROJECT_ID --source . --region $REGION --allow-unauthenticated --min-instances 1 --max-instances 1 --no-cpu-throttling --concurrency 300 --cpu 1 --memory 1Gi --timeout 3600 --set-env-vars "FIRESTORE_ENABLED=1,SOLANA_NETWORK=solana-devnet,SOLANA_RPC_URL=https://api.devnet.solana.com,ALLOW_LIVE_FROM_WEB=0,MAX_BUDGET_USDC=1000,GEMINI_MODE=developer" --set-secrets "TRADING_KEYPAIR_JSON=autotrader-trading-wallet:latest,BROKER_KEYPAIR_JSON=autotrader-broker-wallet:latest,USER_KEYPAIR_JSON=autotrader-user-wallet:latest,GEMINI_API_KEY=autotrader-gemini-key:latest,CONTROL_TOKEN=autotrader-control-token:latest" }
```

끝나면 `Service URL: https://synapstock-....run.app` 이 출력된다 — **이게 라이브 배포 URL(가산점 항목)**.

> ⚠ **`GEMINI_MODE=developer` 를 빠뜨리지 말 것** (2026-07-26 발견). `gemini_decider.__init__` 은
> 모드가 비어 있으면 **키가 `AIza` 로 시작하는지로 developer/vertex 를 자동 판별**하는데, 이 프로젝트의
> 키는 `AIza` 로 시작하지 않아 **Vertex 경로로 잡히고 개발자 키로는 동작하지 않는다.** 로컬은 `.env` 에
> 이 값이 있어 드러나지 않는다. 빠뜨리면 조건형(Gemini) 세션에서만 뒤늦게 터진다.

### 플래그가 이 값인 이유 (줄이면 안 되는 것들)

| 플래그 | 왜 |
|---|---|
| `--min-instances 1` | 항상 켜 둔다 — 세션·자동 브리핑이 살아 있고 콜드스타트가 없다 |
| `--max-instances 1` | **엔진이 전역 싱글턴 1개**라 인스턴스가 2개면 상태가 갈라진다(로그인 라운드에서 사용자별 분리 예정, 그때도 단일 인스턴스 전제) |
| `--no-cpu-throttling` | 요청이 없어도 **백그라운드 매매 틱 루프**가 돌아야 한다(기본값은 요청 처리 중에만 CPU 할당) |
| `--timeout 3600` | SSE 실시간 스트림을 최장 1시간 유지(끊기면 프론트가 Last-Event-ID 로 자동 복원) |
| `--concurrency 300` | 대시보드 탭마다 SSE 를 1개씩 최장 1시간 점유한다. 기본값 80 이면 탭 80개에 인스턴스가 포화되고, `max-instances 1` 이라 스케일아웃으로 흡수할 수도 없다 — **심사위원이 페이지를 못 여는** 최악의 실패를 막는 플래그 |
| `--memory 1Gi` | 컨테이너 쓰기 파일시스템이 메모리(tmpfs)다. 브리핑·아카이브 파일이 메모리를 갉아먹으므로 여유를 둔다 |
| `--allow-unauthenticated` | 심사자가 URL 만으로 **관전**(GET·SSE)할 수 있게 한다. 상태를 바꾸는 POST 는 아래 `CONTROL_TOKEN` 이 막는다 |
| `ALLOW_LIVE_FROM_WEB=0` | 실제 온체인 전송을 웹에서 시작하지 못하게 잠근다. **시연 직전에만** `--update-env-vars ALLOW_LIVE_FROM_WEB=1` 로 열고 촬영 후 되돌린다 |
| `CONTROL_TOKEN` | 세션 시작·정지·한도 변경·브리핑을 토큰 보유자로 제한. 없으면 URL 을 아는 누구나 시연 중 세션을 정지시키거나 AP2 한도를 바꿀 수 있다 |
| `MAX_BUDGET_USDC=1000` | 토큰이 새더라도 예산을 무한대로 올리지 못하게 하는 서버측 상한 |

- 시크릿·`.env` 는 이미지에 들어가지 않는다(`.dockerignore` + 소스 업로드는 `.gcloudignore` 존중).
  지갑키는 **환경변수(`TRADING_KEYPAIR_JSON` 등)로 주입**되고 `_load_or_new(env_json=…)` 가 그걸
  읽는다 — 파일 마운트가 아니다(위 "왜 환경변수인가" 참고. `WALLET_DIR` 은 로컬 개발 경로일 뿐이다).
- 서버 시간대는 컨테이너에서 KST 로 고정(TZ=Asia/Seoul) — 장 마감 자동 브리핑(16:00) 그대로 동작.

## 5. 배포 후 검증 (verification_checklist 의 배포판)

> **첫 배포 성공 기록 (2026-07-26)** — 프로젝트 `synapstock`, 리전 `asia-northeast3`,
> URL `https://synapstock-766888967498.asia-northeast3.run.app`.
> 아래 1~5 와 §5-1 을 전부 통과했다: `persistence.enabled=true`(backend firestore) ·
> 드라이런 세션 체결 발생 · Firestore 세션 문서 기록 · **새 리비전 교체 후에도 세션 이력 생존** ·
> `POST /broker/orders` → **402** + `accepts[0].payTo` · `/.well-known/x402` 200.

0. **접근 토큰 등록(최초 1회)**: `https://<URL>/#token=<§2에서 저장한 값>` 으로 접속한다.
   토큰이 브라우저에 저장되고 주소에서 지워진다. 이후에는 그냥 `https://<URL>` 로 들어가면 된다.
   (토큰 없이 열면 화면은 보이지만 세션 시작 버튼이 401 을 돌려준다 — 의도된 동작)
1. **대시보드**: Service URL 을 브라우저로 열기 → 대시보드가 뜨는지
2. **영속화 활성 확인**: `https://<URL>/api/state` 열기 → `"persistence": {"enabled": true, "backend": "firestore", …}`
3. **드라이런 세션**: 대시보드에서 `실데이터 재생(AAPL)` + 드라이런으로 세션 시작 → 체결 발생 → 세션 종료
4. **Firestore 증빙**: https://console.cloud.google.com/firestore → `autotrader_sessions`(세션 요약)·
   `autotrader_trades`(체결)·`autotrader_briefings`(브리핑) 문서 생겼는지
5. **재시작 영속 증명(핵심)**: 강제로 새 리비전을 만들어 인스턴스를 갈아치운 뒤에도 데이터가 남는지

   ```powershell
   gcloud run services update synapstock --region $REGION --update-env-vars RESTART_MARKER=1
   ```

   완료 후 대시보드 새로고침 → ①최근 브리핑이 복원돼 있고 ②`https://<URL>/api/history/sessions` 에
   방금 세션이 보이면 통과. (이 화면·JSON 을 스크린샷으로 남겨 두면 제출 증빙이 된다)
6. **서버 로그**:

   ```powershell
   gcloud run services logs read synapstock --region $REGION --limit 50
   ```

   부팅 시 `[store] Firestore 영속화 활성`, `[store] 부팅 복원 완료` 라인이 보이면 정상.

## 5-1. HTTP 402 레그 확인 (G5, 2026-07-26 추가)

브로커 x402 자원 서버가 **메인 앱에 함께 마운트**돼 있다. Cloud Run 은 컨테이너당 포트를
하나(`$PORT`)만 외부에 노출하므로, 별도 포트로 띄우면 배포 URL 에서 확인할 수 없기 때문이다.
(로컬 시연은 여전히 별도 프로세스 `python -m web.broker_service --port 8402` 로 띄운다 —
같은 코드·같은 브로커 지갑이다.)

```powershell
curl -i -X POST "https://<URL>/broker/orders" -H "Content-Type: application/json" -d '{\"symbol\":\"AAPL\",\"spend_usdc\":\"10\",\"price_usdc\":\"200\"}'
curl -s "https://<URL>/.well-known/x402"
```

기대: 첫 명령이 `HTTP/1.1 402 Payment Required` + `accepts[0].payTo`. **이 화면이 심사에서
"x402라면서 HTTP 402는 어디 있나"에 대한 답**이므로 스크린샷을 남겨 둔다.

관련 환경변수(둘 다 기본 꺼짐 — 켤 필요 없으면 그대로 둔다):

| 변수 | 기본 | 뜻 |
|---|---|---|
| `BROKER_SERVICE_ALLOW_LIVE` | `0` | 브로커 서비스가 **온체인 정산**까지 수행할지. 꺼져 있으면 청구서는 발행하되 정산은 403 |
| `BROKER_HTTP_URL` | 빈값 | 엔진의 **매수 레그**를 HTTP 402 왕복으로 보낼지. 빈값이면 기존 인프로세스 A2A. 배포본에서 켜려면 자기 자신을 가리킨다(`http://127.0.0.1:8080`) — 데모용 옵션이고 기본은 끄는 쪽을 권한다 |

## 6. 운영 메모

- **재배포(코드 수정 후)**: 4번의 `gcloud run deploy …` 를 그대로 다시 실행 (같은 URL 유지)
- **환경변수만 수정**: `gcloud run services update synapstock --region $REGION --update-env-vars KEY=VALUE`
- **비용**: 상시 1 인스턴스(CPU 상시 할당) ≈ **월 $50 안팎** → 심사 기간 2~3주면 $25~40,
  $300 크레딧으로 충분. 심사 끝나면 삭제: `gcloud run services delete synapstock --region $REGION`
- **Gemini 무료 티어**: 배포해도 호출량은 로컬과 동일(틱 8초·세션 단위). 한도 초과가 보이면
  디스코드에 사전 문의해 크레딧을 요청(CLAUDE.md 규칙)
- 무료 SSE 유의: 브라우저 탭이 열려 있는 동안만 스트림 유지 — 탭을 닫아도 세션은 서버에서 계속 돈다

## 7. (다음 단계) devnet 라이브 모드 전환

배포 직후 기본은 **드라이런 데모**다. 온체인 라이브까지 켜려면:

1. 로컬 `.env` 를 devnet 으로 두고 `python scripts/setup_devnet.py` 실행(민트 2개 생성·양 지갑 ATA 준비)
   — devnet SOL 필요(퍼셋 하루 0.5~5 SOL, 부족하면 미리 디스코드 요청)
2. 출력된 민트 주소를 배포에 반영:

   ```powershell
   gcloud run services update synapstock --region $REGION --update-env-vars "USDC_MINT=<출력값>,STOCK_MINT=<출력값>"
   ```

3. 대시보드에서 라이브 모드 세션 → explorer 링크(devnet)로 트랜잭션 확인 → `artifacts/tx/` 증빙 아카이브

## 7-1. 배포하면 백엔드 수정이 어려워지는가 (2026-07-26 확인)

**코드 수정 자체는 전혀 어려워지지 않는다.** 개발·테스트는 계속 로컬에서 하고
(`python -m web.server`, `python -m scripts.test_*`), 배포는 그 결과물을 올리는 별개 단계다.
다만 아래 네 가지 제약이 새로 붙으므로 **기능 개발을 끝낸 뒤 마지막에 배포**하는 순서가 맞다.

| 제약 | 내용 | 대응 |
|---|---|---|
| **재배포 지연** | 배포 URL 에 반영하려면 `gcloud run deploy` 재빌드 **5~10분/회** | 로컬에서 다 검증하고 배포는 마지막에 1~2회. 환경변수만 바꾸는 건 `--update-env-vars` 로 수십 초 |
| **파일 산출물 비영속** | 컨테이너 파일시스템은 tmpfs — `artifacts/tx/*.json`·브리핑 파일이 **재시작하면 사라진다** | 증빙은 **로컬 실행분을 커밋**한다. 배포본의 영속 데이터는 Firestore(`/api/history`)로 남는다 |
| **포트 1개·인스턴스 1개** | 컨테이너당 `$PORT` 하나만 외부 노출, 엔진이 전역 싱글턴이라 `max-instances 1` | 새 HTTP 서비스는 **별도 포트가 아니라 메인 앱에 router 로 마운트**한다(§5-1 의 브로커 402 가 그 예). 별도 프로세스가 꼭 필요하면 Cloud Run 서비스를 하나 더 만들어야 한다 |
| **조작 API 토큰** | `CONTROL_TOKEN` 이 설정돼 있으면 POST `/api/*` 는 `X-Control-Token` 필요 | 새 조작 API 를 추가하면 `dependencies=[Depends(require_control)]` 를 반드시 붙인다. 조회(GET)·판매자 자원(`/broker/orders`)은 열어 둔다 |

정리하면 **"배포 후에도 코드는 똑같이 고친다. 느려지는 건 배포 URL 검증 루프뿐"** 이다.

## 8. 문제가 나면

| 증상 | 조치 |
|---|---|
| **`could not resolve source: googleapi: Error 403`** (배포 시작 직후) | **§3 의 `roles/cloudbuild.builds.builder` 를 안 준 것**이다. 빌드 이전 단계라 `gcloud builds list` 에는 아무것도 안 남는다. §3 을 실행하고 1분 뒤 재시도 (2026-07-26 실제 발생) |
| `Cannot find path ... because it does not exist` (경로가 분명히 있는데) | PowerShell 이 경로의 대괄호를 와일드카드로 해석한 것. `Set-Location -LiteralPath` 로 바꾼다 (2026-07-26 실제 발생) |
| 조건형 세션에서 Gemini 가 안 먹고 규칙으로만 돌아감 | 서비스 env 에 `GEMINI_MODE=developer` 가 있는지 확인. 없으면 키 형식으로 vertex 가 자동 선택돼 실패한다 |
| 매 틱 `stock_mint 미설정 — 매도 견적 불가` | 2026-07-26 수정됨(드라이런 자리표시 민트). 옛 리비전이면 재배포한다 |
| `PERMISSION_DENIED: secretmanager` | 3번 권한 세 줄을 다시 실행(프로젝트 번호 확인) |
| `NOT_FOUND: database (default)` | 1번 Firestore 생성을 안 했거나 다른 프로젝트에 만듦 |
| 빌드 실패 | `gcloud builds list` → 실패 빌드 ID → `gcloud builds log <ID>` |
| 페이지 500/빈 화면 | 5-6 로그 명령으로 파이썬 트레이스백 확인 |
| `allow-unauthenticated` 거부(조직 정책) | 개인 계정 프로젝트인지 확인(학교·회사 조직 계정은 정책이 막을 수 있음) |
| 배포 후 persistence.enabled=false | 서비스 env 에 FIRESTORE_ENABLED=1 이 있는지 `gcloud run services describe synapstock --region $REGION` 로 확인 |
