# shogi-web 用（Streamlit + ADK エージェント）。エンジンは含まず、shogi-engine を ENGINE_URL で呼ぶ。
#   docker build -f web.Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shogi_analyzer ./shogi_analyzer
COPY shogi_coach ./shogi_coach
COPY assets ./assets
COPY app.py THIRD_PARTY_LICENSES.md ./
ENV PORT=8080 GOOGLE_GENAI_USE_VERTEXAI=TRUE GOOGLE_CLOUD_LOCATION=global
CMD streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true \
    --server.maxUploadSize=1 --browser.gatherUsageStats=false
