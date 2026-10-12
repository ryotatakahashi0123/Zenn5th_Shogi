import io

import pytest
from pypdf import PdfReader

from app import service
from app.agent.brain import FakeBrain
from app.agent.runner import run_report
from app.infra.config import Settings
from app.infra.pdf import build_pdf
from app.infra.store import MemoryStore


def doc(i, owner="u1"):
    return {"id": i, "ownerUid": owner, "title": i, "moves": [], "sample": False, "createdAt": "2026-10-11T00:00:00+00:00"}


def test_store_contract():
    s = MemoryStore()
    assert s.ping()
    s.create_game(doc("a"))
    s.create_game(doc("b", "u2"))
    assert s.get_game("a")["id"] == "a" and s.get_game("zzz") is None
    assert [g["id"] for g in s.list_games("u1")] == ["a"]
    s.put_mark("a", {"moveNo": 3, "feeling": "regret"})
    s.put_mark("a", {"moveNo": 3, "feeling": "unsure"})  # 同じ手は上書き
    assert [m["feeling"] for m in s.list_marks("a")] == ["unsure"]
    assert s.delete_mark("a", 3) and not s.delete_mark("a", 3)
    s.add_run("a", {"id": "r1", "kind": "report"})
    assert s.list_runs("a")[0]["id"] == "r1"
    assert s.incr_quota("u1", "20261011") == 1 and s.incr_quota("u1", "20261011") == 2
    assert s.incr_quota("u1", "20261012") == 1
    assert s.delete_game("a") and s.get_game("a") is None
    assert s.list_marks("a") == [] and s.list_runs("a") == []  # 子データも消える
    assert s.delete_all_games("u2") == 1


def test_cards_roundtrip():
    s = MemoryStore()
    s.put_card({"id": "c1", "title": "t", "tags": ["x"], "body": "b", "sourceUrl": "https://example.invalid"})
    assert [c["id"] for c in s.list_cards()] == ["c1"]


def test_pdf_builds_and_has_text(s01):
    cfg = Settings()
    cfg.validate()
    g = service.build_game_doc(s01, "demo", cfg, my_side="b", doc_id="p1")
    pos = service.positions_for(g)[0]
    marks = [{"moveNo": 55, "feeling": "regret", "causeTags": ["焦り・早指し"], "note": "メモ"}]
    run = run_report(g, pos, marks, [], FakeBrain(), cfg)
    pdf = build_pdf(g, run["output"], marks)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 3000
    r = PdfReader(io.BytesIO(pdf))
    assert len(r.pages) >= 1
