import io

import pytest
from pypdf import PdfReader

from app import service
from app.agent.brain import FakeBrain
from app.agent.runner import run_report
from app.infra.config import Settings
from app.infra.pdf import build_pdf
from app.infra.store import GcsStore, MemoryStore
from .fake_gcs import FakeBucket


def doc(i, owner="u1"):
    return {"id": i, "ownerUid": owner, "title": i, "moves": [], "sample": False, "createdAt": "2026-10-11T00:00:00+00:00"}


@pytest.fixture(params=["memory", "gcs"])
def any_store(request):
    return MemoryStore() if request.param == "memory" else GcsStore(FakeBucket())


def test_store_contract(any_store):
    s = any_store
    assert s.ping()
    s.create_game(doc("a"))
    s.create_game(doc("b", "u2"))
    assert s.get_game("a")["id"] == "a" and s.get_game("zzz") is None
    assert [g["id"] for g in s.list_games("u1")] == ["a"]
    s.put_mark("a", {"moveNo": 3, "feeling": "regret"})
    s.put_mark("a", {"moveNo": 3, "feeling": "unsure"})  # 同じ手は上書き
    assert [m["feeling"] for m in s.list_marks("a")] == ["unsure"]
    assert s.delete_mark("a", 3) and not s.delete_mark("a", 3)
    s.add_run("a", {"id": "r1", "kind": "report", "startedAt": "2026-10-12T00:00:00+00:00"})
    assert s.list_runs("a")[0]["id"] == "r1"
    assert s.incr_quota("u1", "20261011") == 1 and s.incr_quota("u1", "20261011") == 2
    assert s.incr_quota("u1", "20261012") == 1
    assert s.get_cost("d") == 0 and s.add_cost("d", 0.25) == 0.25 and s.add_cost("d", 0.5) == 0.75
    assert s.delete_game("a") and s.get_game("a") is None
    assert s.list_marks("a") == [] and s.list_runs("a") == []  # 子データも消える
    assert s.delete_all_games("u2") == 1


def test_cards_roundtrip(any_store):
    s = any_store
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


def test_gcs_layout_and_orphan_tolerance():
    b = FakeBucket()
    s = GcsStore(b)
    s.create_game(doc("a"))
    s.put_mark("a", {"moveNo": 3, "feeling": "regret"})
    assert {"games/a/game.json", "games/a/marks/3.json", "owners/u1/a"} <= set(b.data)
    del b.data["games/a/game.json"]          # 30日の自動削除で本体だけ先に消えた状況
    assert s.list_games("u1") == [] and s.get_game("a") is None


def test_gcs_counter_retries_on_conflict():
    b = FakeBucket()
    s = GcsStore(b)
    assert s.incr_quota("u1", "d") == 1
    orig, hit = b.get_blob, {"n": 0}

    def racy(name):                            # 読んだ直後に他の更新が入った状況を作る
        blob = orig(name)
        if name.startswith("quota/") and blob is not None and hit["n"] == 0:
            hit["n"] = 1
            b.blob(name).upload_from_string('{"v": 5}')
        return blob
    b.get_blob = racy
    assert s.incr_quota("u1", "d") == 6        # 競合を検知してやり直し、5+1
