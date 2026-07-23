# AutoTrader Agent — Cloud Run 컨테이너 (docs/deploy_cloud_run.md)
#
# 로컬에 Docker 가 없어도 된다: `gcloud run deploy --source .` 가 Cloud Build 에서
# 이 Dockerfile 로 원격 빌드한다. 로컬 개발은 기존 그대로 `python -m web.server`.
#
# 주의: 지갑키·.env 는 이미지에 넣지 않는다(.dockerignore 차단) — Secret Manager 가
# 런타임에 /secrets 파일·환경변수로 주입한다(WALLET_DIR=/secrets).

FROM python:3.11-slim

# tzdata: DAILY_BRIEFING_TIME(장 마감 자동 브리핑)이 서버 로컬 시간을 쓰므로 KST 고정
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Seoul \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_HOST=0.0.0.0

WORKDIR /app

# 의존성 레이어 분리 — 코드만 바뀌면 pip 설치 캐시 재사용
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run 이 PORT 환경변수를 주입한다(web/server.py 가 읽음). EXPOSE 는 문서용.
# 로컬 확인용: docker run -e PORT=8080 -p 8080:8080 <image>
EXPOSE 8080
CMD ["python", "-m", "web.server"]
