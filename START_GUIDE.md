# Claude 데스크톱 앱(Code 탭) 이행 가이드

CLI(터미널) 없이 **Claude 데스크톱 앱의 Code 탭**으로 개발합니다. (Pro 이상 플랜 필요)

## 0. 이 패키지 적용
zip을 풀면 나오는 **내용물**(CLAUDE.md, docs/, prompts/, .gitignore, .env.example, START_GUIDE.md)을 `solana-agent` 폴더 **루트에 직접** 넣는다.
- 완료 기준: `CLAUDE.md`가 `run_demo.py`와 **같은 위치**에 있어야 함 (starter 폴더가 통째로 하위 폴더로 들어가면 안 됨)
- 기존 파일과 겹치면(특히 `.gitignore`, README) 덮어쓰지 말고 그대로 두기 — 병합은 첫 세션에서 Claude가 처리
- `START_GUIDE.md`와 `prompts/`는 커밋할 필요 없음(로컬 참고용)

## 1. 사전 준비 (1회)
- **Git for Windows 설치 권장**: https://git-scm.com/downloads/win (없으면 PowerShell로 동작하지만 Git Bash가 더 안정적)
- Node/npm/CLI 설치는 **불필요** — 데스크톱 앱은 자체 완결형

## 2. Code 탭에서 폴더 열기
1. Claude 데스크톱 앱 실행 → 상단 가운데 **Code 탭** 클릭
2. 환경 선택에서 **Local** 선택
3. **Select folder** → `solana-agent` 폴더 선택 (starter 폴더 아님!)
4. 모델 선택 후 대화 시작
- `CLAUDE.md`는 저장소 루트에 있으면 **자동으로 읽힘**
- 파일 수정·명령 실행은 기본적으로 승인(Manual) 모드 — 제안을 보고 Accept/Reject. 명령 실행(파이썬, 설치, 로컬 서버)도 승인 후 가능

## 3. 첫 프롬프트 붙여넣기
`prompts/first_session_prompt.txt` 내용을 복사해서 **Code 탭의 대화 입력창**에 붙여넣는다. (폴더에 넣는 게 아님 — 파일은 이미 폴더에 있고, 그 내용을 대화로 보내는 것)
→ Claude가 환경 점검 → 오프라인 데모 재현 → 라이브 tx 계획 제시 순서로 진행하고, 모호한 부분은 역으로 질문한다.

## 4. 내(사용자) 몫의 준비물 — 개발과 병렬로
- **디스코드에 Devnet SOL 미리 요청** (하루 0.5~5 SOL 제한 — 가장 급함)
- Gemini API 키 발급: https://aistudio.google.com (무료 티어) → `.env`에 입력
- GCP: 개인 Gmail 신규 계정으로 $300 크레딧 확인, 콘솔에서 Cloud Run·Secret Manager·Firestore API Enable, `gcloud auth login`(이건 Claude가 시키는 대로 승인하면 됨)
- GitHub 저장소 만들기(제출물 ②) — 첫 커밋 전에 `.gitignore` 적용 확인

## 5. Cowork(프로젝트 채팅)와의 역할 분담
- **Code 탭**: 코드·실행·검증·배포 전부
- **Cowork 프로젝트**: 소개서(PPT/PDF)·데모 영상 시나리오·리서치·진행상황 관리
- 동기화: Code 세션이 큰 진행을 마치면 `docs/progress_log.md`에 기록됨 → 그 내용을 Cowork 채팅에 붙여넣으면 프로젝트 진행상황 문서를 갱신해 줌

## 참고: localnet과 WSL
`solana-test-validator`(로컬넷)는 Windows 네이티브보다 **WSL(Ubuntu)이 수월**하다. localnet 단계에서 설치가 막히면 Claude에게 "WSL로 전환할지 판단해줘"라고 요청 (Code 탭은 WSL 환경도 지원).

## 자주 하는 실수 방지
- 지갑 키페어를 저장소에 커밋 (→ `secrets/` + .gitignore 확인)
- $300 크레딧으로 Gemini API를 쓰려다 막힘 (→ 무료 티어 키 사용)
- Devnet SOL을 마감 직전에 요청 (→ 지금)
- 목업/화면만 있는 데모 (→ 심사 제외. 반드시 tx 해시)
