"""shogi-engine: やねうら王を HTTP API で提供する Cloud Run サービス。

- 認証は Cloud Run の IAM に任せる（--no-allow-unauthenticated でデプロイし、
  web 側のサービスアカウントにだけ roles/run.invoker を付ける）。
- 1インスタンス1エンジン。Cloud Run の同時実行数は 1 にして、負荷はインスタンス数で捌く。

    uvicorn engine_service.main:app --port 8081
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from shogi_analyzer.usi_engine import UsiEngine
from shogi_analyzer import service

ENGINE_PATH = os.environ.get("ENGINE_PATH", "./engine/YaneuraOu")
ENGINE_OPTIONS = {
    "FV_SCALE": 24, "USI_Hash": int(os.environ.get("ENGINE_HASH_MB", "256")), "BookFile": "no_book",
    "Threads": int(os.environ.get("ENGINE_THREADS", "2")),
}

# Cloud Logging が構造化ログとして読める JSON を標準出力に出す
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
log = logging.getLogger("shogi-engine")


def jlog(**kw):
    log.info(json.dumps({"severity": "INFO", "service": "shogi-engine", **kw}, ensure_ascii=False))


engine: UsiEngine | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global engine
    engine = UsiEngine(ENGINE_PATH, ENGINE_OPTIONS)
    jlog(event="engine_ready", engine=engine.name)
    yield
    engine.close()


app = FastAPI(title="shogi-engine", lifespan=lifespan)


class GameIn(BaseModel):
    start_sfen: str = Field(max_length=200)
    moves: list[str] = Field(max_length=service.MAX_PLIES)
    ms: int = Field(400, ge=100, le=3000)


class PositionIn(GameIn):
    ply: int = Field(ge=1, le=service.MAX_PLIES)
    multipv: int = Field(service.DEEP_MULTIPV, ge=1, le=5)
    ms: int = Field(service.DEEP_MS, ge=100, le=5000)


class CompareIn(GameIn):
    ply: int = Field(ge=1, le=service.MAX_PLIES)
    alt_move: str = Field(max_length=8)
    ms: int = Field(1500, ge=100, le=5000)


def _run(name: str, request: Request, fn):
    trace = request.headers.get("x-cloud-trace-context", "").split("/")[0]
    t0 = time.time()
    try:
        out = fn()
        jlog(event=name, ok=True, ms=int((time.time() - t0) * 1000),
             **({"logging.googleapis.com/trace": trace} if trace else {}))
        return out
    except ValueError as e:  # 入力の誤り（不正手など）
        jlog(event=name, ok=False, error=str(e))
        raise HTTPException(400, str(e))


@app.get("/healthz")
def healthz():
    return {"ok": engine is not None and engine.proc.poll() is None, "engine": engine.name if engine else None}


@app.post("/v1/game")
def game(body: GameIn, request: Request):
    return _run("analyze_game", request, lambda: service.analyze_game(
        engine, service.kifu_from_usi(body.start_sfen, body.moves), body.ms))


@app.post("/v1/position")
def position(body: PositionIn, request: Request):
    return _run("deep_dive", request, lambda: service.deep_dive(
        engine, service.kifu_from_usi(body.start_sfen, body.moves), body.ply, body.ms, body.multipv))


@app.post("/v1/compare")
def compare(body: CompareIn, request: Request):
    return _run("compare_move", request, lambda: service.compare_move(
        engine, service.kifu_from_usi(body.start_sfen, body.moves), body.ply, body.alt_move, body.ms))
