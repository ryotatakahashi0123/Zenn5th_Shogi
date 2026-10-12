"""APIルータ(03の6章)。認証はクッキー。他人の対局は存在しないものとして404を返す。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import service
from ..agent.runner import run_report, run_suggestions
from ..domain.board_svg import render_board_svg
from ..domain.kif_parser import KifError
from ..infra import auth
from ..infra.pdf import build_pdf

log = logging.getLogger("furikaeri.api")
router = APIRouter()

FEELINGS = ("regret", "unsure", "ok")


# ---- 依存 ----
def settings(request: Request):
    return request.app.state.settings


def store(request: Request):
    return request.app.state.store


def user(request: Request) -> str:
    uid = auth.verify_token(request.app.state.settings, request.cookies.get(auth.COOKIE))
    if not uid:
        raise HTTPException(401, "ログインが必要")
    return uid


def own_game(request: Request, game_id: str, uid: str = Depends(user)) -> dict:
    g = request.app.state.store.get_game(game_id)
    if not g or g["ownerUid"] != uid:
        raise HTTPException(404, "対局が見つからない")
    return g


# ---- 入出力 ----
class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class GameIn(BaseModel):
    kif: str
    mySide: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=60)


class MarkIn(BaseModel):
    feeling: str = "regret"
    causeTags: list[str] = Field(default_factory=list, max_length=10)
    note: str = Field(default="", max_length=500)


# ---- 認証 ----
@router.post("/api/login")
def login(body: LoginIn, response: Response, request: Request):
    cfg = request.app.state.settings
    uid = auth.check_credentials(cfg, body.email, body.password)
    if not uid:
        raise HTTPException(401, "メールアドレスまたはパスワードが違う")
    response.set_cookie(auth.COOKIE, auth.make_token(cfg, uid), max_age=auth.MAX_AGE, httponly=True,
                        samesite="lax", secure=cfg.is_production, path="/")
    return {"uid": uid}


@router.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/api/me")
def me(uid: str = Depends(user)):
    return {"uid": uid}


@router.get("/api/config")
def config(cfg=Depends(settings)):
    return {"causeTags": cfg.cause_tags, "maxCandidates": cfg.max_candidates, "maxKifBytes": cfg.max_kif_bytes,
            "brain": cfg.brain, "lowRemainingSec": cfg.low_remaining_sec}


# ---- 対局 ----
@router.post("/api/games", status_code=201)
def create_game(body: GameIn, uid: str = Depends(user), st=Depends(store), cfg=Depends(settings)):
    if len(body.kif.encode("utf-8")) > cfg.max_kif_bytes:
        raise HTTPException(413, f"入力が大きすぎる(上限 {cfg.max_kif_bytes // 1024}KB)")
    if body.mySide not in (None, "b", "w"):
        raise HTTPException(422, "mySide は b か w")
    mine = [g for g in st.list_games(uid) if not g.get("sample")]
    if len(mine) >= cfg.max_games_per_user:
        raise HTTPException(429, f"保存できる対局数の上限({cfg.max_games_per_user})。不要な対局を削除する")
    try:
        doc = service.build_game_doc(body.kif, uid, cfg, my_side=body.mySide, title=body.title)
    except KifError as e:
        raise HTTPException(422, f"棋譜を解析できない: {e}")
    st.create_game(doc)
    return service.public_game(doc)


@router.get("/api/games")
def list_games(uid: str = Depends(user), st=Depends(store)):
    return [service.list_item(g) for g in st.list_games(uid)]


@router.delete("/api/games")
def delete_all(uid: str = Depends(user), st=Depends(store)):
    return {"deleted": st.delete_all_games(uid), "note": "サンプル局は削除しない"}


@router.get("/api/games/{game_id}")
def get_game(g: dict = Depends(own_game)):
    return service.public_game(g)


@router.delete("/api/games/{game_id}")
def delete_game(g: dict = Depends(own_game), st=Depends(store)):
    if g.get("sample"):
        raise HTTPException(403, "サンプル局は削除できない")
    st.delete_game(g["id"])
    return {"deleted": g["id"]}


@router.get("/api/games/{game_id}/positions")
def positions(g: dict = Depends(own_game)):
    pos, errs = service.positions_for(g)
    return {"positions": [p.to_dict() for p in pos], "errors": errs}


@router.get("/api/games/{game_id}/board/{n}.svg")
def board_svg(n: int, perspective: Optional[str] = None, g: dict = Depends(own_game)):
    pos, _ = service.positions_for(g)
    if not 0 <= n < len(pos):
        raise HTTPException(404, "その手の局面は再現できない")
    persp = perspective if perspective in ("b", "w") else g["mySide"]
    title = "開始局面" if n == 0 else f"{n}手目 {g['moves'][n - 1]['text']} の後"
    return Response(render_board_svg(pos[n], persp, title), media_type="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=300"})


# ---- 印 ----
@router.get("/api/games/{game_id}/marks")
def list_marks(g: dict = Depends(own_game), st=Depends(store)):
    return st.list_marks(g["id"])


@router.put("/api/games/{game_id}/marks/{move_no}")
def put_mark(move_no: int, body: MarkIn, g: dict = Depends(own_game), st=Depends(store), cfg=Depends(settings)):
    total = len(g["moves"])
    if not 1 <= move_no <= total:
        raise HTTPException(422, f"手番号は1〜{total}")
    if body.feeling not in FEELINGS:
        raise HTTPException(422, "feeling は regret / unsure / ok")
    bad = [t for t in body.causeTags if t not in cfg.cause_tags]
    if bad:
        raise HTTPException(422, f"原因タグが一覧にない: {bad}")
    mark = {"moveNo": move_no, "feeling": body.feeling, "causeTags": body.causeTags,
            "note": body.note, "source": "user", "updatedAt": datetime.now(timezone.utc).isoformat()}
    st.put_mark(g["id"], mark)
    return mark


@router.delete("/api/games/{game_id}/marks/{move_no}")
def delete_mark(move_no: int, g: dict = Depends(own_game), st=Depends(store)):
    if not st.delete_mark(g["id"], move_no):
        raise HTTPException(404, "その手に印はない")
    return {"deleted": move_no}


# ---- エージェント ----
def _quota(uid: str, st, cfg) -> None:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    if st.incr_quota(uid, day) > cfg.daily_run_limit:
        raise HTTPException(429, f"1日の実行回数の上限({cfg.daily_run_limit})に達した")


@router.post("/api/games/{game_id}/agent/questions")
def agent_questions(request: Request, g: dict = Depends(own_game), uid: str = Depends(user),
                    st=Depends(store), cfg=Depends(settings)):
    _quota(uid, st, cfg)
    pos, _ = service.positions_for(g)
    run = run_suggestions(g, pos, request.app.state.brain, cfg)
    st.add_run(g["id"], run)
    log.info("run_done", extra={"game_id": g["id"], "run_id": run["id"], "kind": "questions",
                                "degraded": run["guardrail"]["degraded"]})
    return run


@router.post("/api/games/{game_id}/agent/report")
def agent_report(request: Request, g: dict = Depends(own_game), uid: str = Depends(user),
                 st=Depends(store), cfg=Depends(settings)):
    _quota(uid, st, cfg)
    pos, _ = service.positions_for(g)
    run = run_report(g, pos, st.list_marks(g["id"]), st.list_cards(), request.app.state.brain, cfg)
    st.add_run(g["id"], run)
    log.info("run_done", extra={"game_id": g["id"], "run_id": run["id"], "kind": "report",
                                "degraded": run["guardrail"]["degraded"]})
    return run


def _latest(st, game_id: str, kind: str) -> Optional[dict]:
    rs = [r for r in st.list_runs(game_id) if r["kind"] == kind]
    return rs[-1] if rs else None


@router.get("/api/games/{game_id}/report")
def get_report(g: dict = Depends(own_game), st=Depends(store)):
    r = _latest(st, g["id"], "report")
    if not r:
        raise HTTPException(404, "レポートがまだない")
    return r


@router.get("/api/games/{game_id}/runs")
def runs(g: dict = Depends(own_game), st=Depends(store)):
    return st.list_runs(g["id"])


@router.get("/api/games/{game_id}/report.pdf")
def report_pdf(g: dict = Depends(own_game), st=Depends(store)):
    r = _latest(st, g["id"], "report")
    if not r:
        raise HTTPException(409, "先にレポートを作る")
    pdf = build_pdf(g, r["output"], st.list_marks(g["id"]))
    name = quote(f"furikaeri_{g['id']}.pdf")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})


# ---- 知識カード ----
@router.get("/api/knowledge")
def knowledge(uid: str = Depends(user), st=Depends(store)):
    return st.list_cards()
