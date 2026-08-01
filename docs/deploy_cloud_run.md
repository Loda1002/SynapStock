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

⚠ **이 프로젝트는 이미 만들어져 있다 — 아래 `gcloud projects create` 는 처음 세팅할 때만이다.**
실제 값은 **프로젝트·서비스 모두 `synapstock`**, 리전 `asia-northeast3` 이다(2026-07-31 실측).
재배포만 할 것이라면 §4 로 바로 간다. 예전 문서에는 여기가 `autotrader-agent-2026` 으로
적혀 있었는데 **실재하지 않는 값**이라, 그대로 따라 하면 엉뚱한 새 프로젝트를 만들게 된다.

```powershell
# 프로젝트 ID 는 전역 유일해야 한다 — 새로 만들 때만 다른 이름을 쓴다
$PROJECT_ID = "synapstock"
$REGION = "asia-northeast3"   # 서울

# 이미 있는 프로젝트를 쓰는 경우엔 create 를 건너뛰고 아래 한 줄만 실행한다
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
if (-not (Test-Path -LiteralPath ".\Dockerfile")) { "중단: 저장소 루트가 아닙니다. 현재 위치: $(Get-Location)" } else { gcloud run deploy synapstock --project $PROJECT_ID --source . --region $REGION --allow-unauthenticated --min-instances 1 --max-instances 1 --no-cpu-throttling --concurrency 300 --cpu 1 --memory 1Gi --timeout 3600 --set-env-vars "FIRESTORE_ENABLED=1,SOLANA_NETWORK=solana-devnet,SOLANA_RPC_URL=https://api.devnet.solana.com,ALLOW_LIVE_FROM_WEB=0,MAX_BUDGET_USDC=1000,GEMINI_MODE=developer,GEMINI_MODEL=gemini-flash-lite-latest,STOCK_MINT=37HxYLozTuzRDi1bqkqY1gKghzxbZHG8cWH1pwvAfJRG,STOCK_SYMBOL=tAAPL,BUDGET_USDC=100,PER_TRADE_MAX_USDC=50" --set-secrets "TRADING_KEYPAIR_JSON=autotrader-trading-wallet:latest,BROKER_KEYPAIR_JSON=autotrader-broker-wallet:latest,USER_KEYPAIR_JSON=autotrader-user-wallet:latest,GEMINI_API_KEY=autotrader-gemini-key:latest,CONTROL_TOKEN=autotrader-control-token:latest" }
```

> ⚠ **`STOCK_MINT` 이하 4개는 2026-07-31 에 추가됐다 — 빼면 안 된다.** 그 전까지 이 명령에는
> 없었는데 **배포본에는 있었다**(devnet 재실증·라이브 세션 라운드에서 주입). `--set-env-vars` 는
> 없는 변수를 지우므로, 옛 명령을 그대로 복붙하면 **`STOCK_MINT` 이 사라진다.**
> `STOCK_SYMBOL`·`BUDGET_USDC`·`PER_TRADE_MAX_USDC` 는 `config.py` 기본값과 같아(`tAAPL`·100·50)
> 지워져도 값이 안 바뀌지만, **`STOCK_MINT` 만 기본값이 `""`** 라 실제로 달라진다.
> ⚠ 이 4개는 **배포 현행 상태를 그대로 적어 둔 것**이다 — devnet 민트를 다시 발행하거나
> 예산 기본값을 바꾸면 이 줄도 함께 고쳐야 한다(안 고치면 다음 재배포가 옛 값으로 되돌린다).

끝나면 `Service URL: https://synapstock-....run.app` 이 출력된다 — **이게 라이브 배포 URL(가산점 항목)**.

> ⚠ **`GEMINI_MODE=developer` 를 빠뜨리지 말 것** (2026-07-26 발견). `gemini_decider.__init__` 은
> 모드가 비어 있으면 **키가 `AIza` 로 시작하는지로 developer/vertex 를 자동 판별**하는데, 이 프로젝트의
> 키는 `AIza` 로 시작하지 않아 **Vertex 경로로 잡히고 개발자 키로는 동작하지 않는다.** 로컬은 `.env` 에
> 이 값이 있어 드러나지 않는다. 빠뜨리면 조건형(Gemini) 세션에서만 뒤늦게 터진다.

> ⚠ **`--set-env-vars` 는 기존 env 를 전부 덮어쓴다**(2026-07-28 반영). 위 명령에 없는 변수는
> 다음 배포에서 **사라진다** — 그래서 `GEMINI_MODEL` 을 명령에 직접 넣어 두었다.
> 예전 명령에는 이 줄이 없어서, 재배포할 때마다 §7-1 에서 따로 넣어 둔 모델이 지워지고
> **일일 쿼터가 소진된 기본 모델로 되돌아갔다.** env 를 하나만 바꾸고 싶을 때는
> `--set-env-vars` 가 아니라 `--update-env-vars` 를 쓴다(그쪽은 지정한 것만 덧씌운다).

> ⚠ **모델 값은 `gemini-flash-lite-latest` 다 — `gemini-flash-latest` 로 되돌리지 말 것**
> (2026-08-01 정정). 무료 티어 일일 한도가 **`gemini-flash-latest` 는 20건/일,
> `gemini-flash-lite-latest` 는 500건/일**이라, 전자로 배포하면 심사위원이 조건형(AI 판단)
> 세션을 **한 번 돌리는 것만으로 한도가 소진된다**(60~80봉 세션은 애초에 완주가 불가능하다).
> 배포 현행값도 lite 이며, 이 명령을 그대로 복붙해야 그 상태가 유지된다.

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
- ⚠ **`GEMINI_MODEL` 도 env 에 넣는 것을 권장**(2026-07-27). 무료 티어 일일 한도는 **모델별로
  따로** 계산된다(`quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier`). 기본 모델이
  소진되면 배포본의 조건형(Gemini) 세션이 **매수를 전부 보류**한다 — 설계된 fail-closed 동작이라
  엔진은 계속 돌지만 화면에는 체결이 안 나온다. 소진 시 재배포 없이 갈아타는 방법:
  ```powershell
  gcloud run services update synapstock --region $REGION --update-env-vars "GEMINI_MODEL=<쿼터가 남은 모델>"
  ```
  ⚠ **`gemini-flash-latest` 로는 갈아타지 않는다** — 일일 20건이라 세션 하나를 못 채운다.
  배포 현행값 `gemini-flash-lite-latest`(500건/일)가 지금까지 확인된 유일한 실용 선택지다.
- **환경변수만 수정**: `gcloud run services update synapstock --region $REGION --update-env-vars KEY=VALUE`
- **비용**: 상시 1 인스턴스(CPU 상시 할당) ≈ **월 $50 안팎** → 심사 기간 2~3주면 $25~40,
  $300 크레딧으로 충분. 심사 끝나면 삭제: `gcloud run services delete synapstock --region $REGION`
- **Gemini 무료 티어**: 배포해도 호출량은 로컬과 동일(틱 8초·세션 단위). 한도 초과가 보이면
  디스코드에 사전 문의해 크레딧을 요청(CLAUDE.md 규칙)
- 무료 SSE 유의: 브라우저 탭이 열려 있는 동안만 스트림 유지 — 탭을 닫아도 세션은 서버에서 계속 돈다
- ⚠ **세션이 도는 중에는 어떤 `gcloud run services update` 도 하지 않는다.** 새 리비전 =
  구 인스턴스 종료(유예 10초)이고, SSE 가 열려 있으면 lifespan 의 세션 저장 코드에 도달조차
  못 한다. env 주입은 **세션 시작 전에** 끝낸다. 되돌리기:
  ```powershell
  gcloud run services update-traffic synapstock --region $REGION --to-revisions <이전리비전>=100
  ```
- ⚠ **데모에서 `BROKER_HTTP_URL` 은 켜지 않는다.** 이 값이 없으면 `/broker/orders` 는
  결제 경로가 아니라 `curl -i` 로 402 를 보여 주는 전시물이다(§5-1). 켜면 매수 레그가 그 경로로
  가는데, 해당 엔드포인트는 무인증 POST 라 외부인이 `_pending` 200개 상한을 채워
  정상 결제를 `unknown_order` 로 만들 수 있다.

## 6-1. 심사용 링크(#token) 운영 규칙 (2026-07-28)

조작 API(POST `/api/*`)는 `CONTROL_TOKEN` 게이트다. 그래서 **랜딩의 "지갑 연결하고
시작하기" 를 누른 심사위원이 세션을 시작하면 401** 이 된다(지갑 연결 여부와 무관 —
`web/server.py` `require_control` 은 헤더 토큰만 본다).

**해법은 링크다(코드 변경 없음).** `web/static/js/app.js` 가 주소의 `#token=…` 을
localStorage 로 옮기고 주소창에서 지운다.

| 어디에 | 어떤 URL |
|---|---|
| **제출 폼의 라이브 URL** | `https://<서비스URL>/app#token=<CONTROL_TOKEN 값>` |
| README · 공개 저장소 · 소개서 PDF · 영상 | **토큰 없는 순수 URL** |

- **촬영 규칙**: 촬영용 브라우저에 토큰 URL 로 **한 번만** 접속해 심어 두고, 본 촬영은
  순수 URL 로 연다. 편집 때 주소창이 보이는 프레임을 전수 확인한다.
- **심사 종료 후 `CONTROL_TOKEN` 1회 로테이션** — 토큰이 제출 폼에 실려 나간 결과다.
- 토큰이 없는 방문자에게도 **관전(GET·SSE)은 열려 있다.** 랜딩 히어로에 "단일 공용
  인스턴스" 고지가 있고, 401 알림도 "조작은 심사용 링크에서만, 관전은 그대로"를 말한다.
- ⚠ **`require_control` 을 드라이런 시작에 한해 여는 안은 채택하지 않는다.**
  `/api/engine/stop` 은 여전히 토큰 게이트라, 외부인이 세션 하나만 켜 두면 심사위원은
  "이미 실행 중"으로 막히고 **아무도 풀 수 없다.** 토큰을 도입한 이유 자체를 되돌리는 안이다.

## 6-2. 시연·촬영 운영 규칙 (2026-07-28, P1-5)

코드를 고치지 않고 **운영 결정으로 닫는 것들**이다. 배포 직전에 한 번 읽는다.

- **`BROKER_HTTP_URL` 은 배포 env 에 넣지 않는다** (`OPS-06`). 값이 있으면 매수 레그가
  원격 HTTP 로 나가고, 그 순간 무인증 `POST /broker/orders` 로 대기열(상한 200)을 채워
  정상 결제를 `unknown_order` 로 만드는 경로가 열린다. **402 응답을 보여 주는 것 자체는
  값 없이도 된다** — `curl -i <배포URL>/broker/orders` 로 확인된다(§5-1). 지금 배포본에는
  이 키가 없고, 앞으로도 넣지 않는다.
- **촬영 세션은 60~120봉으로 잡는다** (`OPS-07`). SSE 히스토리는 1000건까지 보관하지만
  **클라이언트 상한이 더 낮다**(`app.js` MAX_FEED 100 · MAX_LOG 200). 서버 상한을 올려도
  화면은 그대로이므로, 긴 세션을 찍으면 초반 구간이 화면에서 사라진 채로 촬영된다.
  481봉 같은 대표본은 백테스트 리포트로 보여 주고, 영상은 짧은 세션으로 찍는다.
- **촬영은 라이트 모드로 고정한다** (`UX-09`). 다크 모드에서 판단 출처 배지의 명암비가
  1.86:1 이라 영상에서 읽히지 않는다. 8/2 디자인 시안이 `theme.css` 를 덮어쓰므로 코드로
  고치지 않고, 디자이너에게 **"다크 `--badge-*` 는 흰 글자 대비 4.5:1 이상"** 제약만 전달한다.

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
