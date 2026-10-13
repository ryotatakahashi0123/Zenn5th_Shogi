# 1サービスにまとめた簡易版（エンジンとAIコーチを同じコンテナで動かす）。2サービス構成は deploy/setup.sh を使う。
# nn.bin はリポジトリに含めない（評価関数ファイルに明示的な再配布許諾がないため）。
# 注意: このイメージを公開レジストリで配布すると GPL の「頒布」になる。自分の Cloud Run で動かすだけにする。

# ---- 1. エンジンのビルド ----
FROM ubuntu:24.04 AS engine
ARG YANEURAOU_COMMIT=c1b80eaa09fe13d5f12b1599d1ae4d53c224de30
ARG TARGET_CPU=AVX2
RUN apt-get update && apt-get install -y --no-install-recommends g++ make git ca-certificates curl p7zip-full \
 && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/yaneurao/YaneuraOu.git /src && cd /src && git checkout ${YANEURAOU_COMMIT}
RUN cd /src/source && make -j"$(nproc)" normal COMPILER=g++ TARGET_CPU=${TARGET_CPU} \
      YANEURAOU_EDITION=YANEURAOU_ENGINE_NNUE
RUN mkdir -p /engine/eval && cp /src/source/YaneuraOu-by-gcc /engine/YaneuraOu \
 && curl -sSL -o /tmp/Suisho5.7z https://github.com/yaneurao/YaneuraOu/releases/download/suisho5/Suisho5.7z \
 && 7z x -o/engine/eval /tmp/Suisho5.7z && ls -la /engine/eval

# ---- 2. アプリ ----
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=engine /engine ./engine
COPY . .
ENV ENGINE_PATH=/app/engine/YaneuraOu ENGINE_THREADS=2 PORT=8080 GOOGLE_GENAI_USE_VERTEXAI=TRUE GOOGLE_CLOUD_LOCATION=global
CMD streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true
