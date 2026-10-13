# shogi-engine 用。ビルドはリポジトリのルートで行う:
#   docker build -f engine_service/Dockerfile .
# やねうら王はソースから無改変でビルドし、水匠5は公式Releasesからビルド時に取得する（リポジトリには含めない）。
# このイメージを公開レジストリで配布すると GPL の「頒布」になるので、自分の Artifact Registry でのみ使う。

FROM ubuntu:24.04 AS engine
ARG YANEURAOU_COMMIT=c1b80eaa09fe13d5f12b1599d1ae4d53c224de30
# Cloud Run の CPU は AVX2 対応。動かない場合は SSE42 にする
ARG TARGET_CPU=AVX2
RUN apt-get update && apt-get install -y --no-install-recommends g++ make git ca-certificates curl p7zip-full \
 && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/yaneurao/YaneuraOu.git /src && cd /src && git checkout ${YANEURAOU_COMMIT}
RUN cd /src/source && make -j"$(nproc)" normal COMPILER=g++ TARGET_CPU=${TARGET_CPU} \
      YANEURAOU_EDITION=YANEURAOU_ENGINE_NNUE
RUN mkdir -p /engine/eval && cp /src/source/YaneuraOu-by-gcc /engine/YaneuraOu \
 && curl -sSL -o /tmp/Suisho5.7z https://github.com/yaneurao/YaneuraOu/releases/download/suisho5/Suisho5.7z \
 && 7z x -o/engine/eval /tmp/Suisho5.7z && test -f /engine/eval/nn.bin

FROM python:3.12-slim
WORKDIR /app
COPY engine_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=engine /engine ./engine
COPY shogi_analyzer ./shogi_analyzer
COPY engine_service ./engine_service
ENV ENGINE_PATH=/app/engine/YaneuraOu ENGINE_THREADS=2 PORT=8080
CMD uvicorn engine_service.main:app --host 0.0.0.0 --port ${PORT}
