"""FastAPIアプリ。依存を create_app に注入できる(試験でMemoryStore/FakeBrainに差し替える)。"""
from __future__ import annotations

import logging
import pathlib
import time
import uuid
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import service
from .agent.brain import Brain, make_brain
from .api.routes import router
from .infra import auth
from .infra.config import Settings
from .infra.logging_setup import setup_logging
from .infra.store import Store, load_cards_file, make_store

STATIC = pathlib.Path(__file__).parent / "static"
log = logging.getLogger("furikaeri")
NOBRAIN = object()


def create_app(settings: Optional[Settings] = None, store: Optional[Store] = None,
               brain=NOBRAIN) -> FastAPI:
    cfg = settings or Settings.from_env()
    st = store or make_store(cfg.store, cfg.gcp_project, cfg.gcs_bucket)
    br: Optional[Brain] = make_brain(cfg.brain, cfg.gemini_model, cfg.gcp_project, cfg.gcp_location) \
        if brain is NOBRAIN else brain

    app = FastAPI(title="ふりかえり将棋", docs_url="/api/docs" if not cfg.is_production else None, redoc_url=None)
    app.state.settings, app.state.store, app.state.brain = cfg, st, br

    # 知識カード(knowledge/cards.json)を取り込む。"_"始まりのIDは見本として除外される
    if hasattr(st, "put_card") and not st.list_cards():
        for c in load_cards_file():
            st.put_card(c)
    if cfg.seed_demo:
        service.seed_samples(st, cfg, auth.DEMO_UID)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        rid = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        try:
            resp = await call_next(request)
        except Exception:
            log.exception("unhandled", extra={"request_id": rid, "path": request.url.path})
            return JSONResponse({"detail": "サーバーでエラーが起きた"}, status_code=500)
        resp.headers["X-Request-Id"] = rid
        if request.url.path != "/healthz":
            log.info("request", extra={"request_id": rid, "path": request.url.path, "method": request.method,
                                       "status": resp.status_code,
                                       "ms": round((time.perf_counter() - t0) * 1000, 1)})
        return resp

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def app_factory() -> FastAPI:
    setup_logging()
    return create_app()
