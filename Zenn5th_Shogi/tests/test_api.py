import pytest
from fastapi.testclient import TestClient

from app.agent.brain import FakeBrain
from app.infra.config import Settings
from app.infra.store import MemoryStore
from app.main import create_app
from .conftest import read_sample


def make_client(brain=None, **kw):
    cfg = Settings(**kw)
    cfg.validate()
    app = create_app(cfg, MemoryStore(), brain if brain is not None else FakeBrain())
    c = TestClient(app)
    return c, app


def login(c):
    r = c.post("/api/login", json={"email": "demo@example.com", "password": "demo-pass"})
    assert r.status_code == 200
    return c


@pytest.fixture
def client():
    c, app = make_client()
    return login(c)


def test_healthz_and_index():
    c, _ = make_client()
    assert c.get("/healthz").json() == {"status": "ok"}
    r = c.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_auth_required():
    c, _ = make_client()
    for path in ("/api/games", "/api/me", "/api/knowledge"):
        assert c.get(path).status_code == 401
    assert c.post("/api/login", json={"email": "demo@example.com", "password": "wrong"}).status_code == 401
    assert c.get("/api/games/sample-sample01").status_code == 401


def test_samples_visible_after_login(client):
    items = client.get("/api/games").json()
    ids = {i["id"] for i in items}
    assert {"sample-sample01", "sample-sample02"} <= ids
    s1 = client.get("/api/games/sample-sample01").json()
    assert s1["totalMoves"] == 100 and s1["positionsAvailable"] is True
    assert "kifRaw" not in s1 and "ownerUid" not in s1


def test_paste_game_and_errors(client):
    r = client.post("/api/games", json={"kif": read_sample("sample02.kif"), "title": "試し"})
    assert r.status_code == 201 and r.json()["totalMoves"] == 85
    assert client.post("/api/games", json={"kif": ""}).status_code == 422
    assert client.post("/api/games", json={"kif": "ただの文章"}).status_code == 422
    assert client.post("/api/games", json={"kif": "x" * (200 * 1024 + 1)}).status_code == 413
    assert client.post("/api/games", json={"kif": read_sample("sample01.kif"), "mySide": "x"}).status_code == 422


def test_other_users_game_is_404():
    c, app = make_client()
    login(c)
    app.state.store.create_game({"id": "other", "ownerUid": "someone", "createdAt": "x", "moves": [], "title": "t"})
    assert c.get("/api/games/other").status_code == 404
    assert c.delete("/api/games/other").status_code == 404


def test_board_svg_and_positions(client):
    r = client.get("/api/games/sample-sample01/board/57.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.text.startswith("<svg")
    assert client.get("/api/games/sample-sample01/board/101.svg").status_code == 404
    p = client.get("/api/games/sample-sample01/positions").json()
    assert len(p["positions"]) == 101 and p["errors"] == []


def test_marks_crud_and_validation(client):
    base = "/api/games/sample-sample01/marks"
    assert client.put(f"{base}/57", json={"feeling": "regret", "causeTags": ["読み抜け"], "note": "m"}).status_code == 200
    assert client.put(f"{base}/0", json={}).status_code == 422
    assert client.put(f"{base}/101", json={}).status_code == 422        # 範囲外(T-21)
    assert client.put(f"{base}/5", json={"feeling": "bad"}).status_code == 422
    assert client.put(f"{base}/5", json={"causeTags": ["存在しないタグ"]}).status_code == 422
    marks = client.get(base).json()
    assert [m["moveNo"] for m in marks] == [57] and marks[0]["causeTags"] == ["読み抜け"]
    assert client.delete(f"{base}/57").status_code == 200
    assert client.delete(f"{base}/57").status_code == 404
    assert client.get(base).json() == []


def test_agent_flow_and_pdf(client):
    gid = "sample-sample01"
    q = client.post(f"/api/games/{gid}/agent/questions").json()
    marks = q["output"]["marks"]
    assert 1 <= len(marks) <= 5 and q["output"]["degraded"] is False
    assert marks[0]["n"] == 57          # 自分の手で最長(97秒)が先頭
    assert all(m["source"] == "agent" for m in marks)
    assert any(s["tool"] == "get_time_outliers" for s in q["steps"])

    client.put(f"/api/games/{gid}/marks/57", json={"feeling": "regret", "causeTags": ["時間不足"]})
    client.put(f"/api/games/{gid}/marks/30", json={"feeling": "unsure", "causeTags": []})
    assert client.get(f"/api/games/{gid}/report").status_code == 404
    assert client.get(f"/api/games/{gid}/report.pdf").status_code == 409

    rep = client.post(f"/api/games/{gid}/agent/report").json()
    out = rep["output"]
    assert [x["n"] for x in out["compare"]["coincide"]] == [57]
    assert [x["n"] for x in out["compare"]["feelingOnly"]] == [30]
    assert 55 in [x["n"] for x in out["compare"]["dataOnly"]] and 77 in [x["n"] for x in out["compare"]["dataOnly"]]
    assert [p["n"] for p in out["problems"]][:2] == [57, 30]
    assert len(out["problems"]) <= 5 and all(p["bestMove"] is None for p in out["problems"])
    assert out["narration"]["source"] == "ai" and out["noMarks"] is False

    runs = client.get(f"/api/games/{gid}/runs").json()
    assert [r["kind"] for r in runs] == ["questions", "report"]
    pdf = client.get(f"/api/games/{gid}/report.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"


def test_report_without_marks(client):
    out = client.post("/api/games/sample-sample02/agent/report").json()["output"]
    assert out["noMarks"] is True and out["compare"]["markCount"] == 0
    assert all(p["origin"] == "candidate" for p in out["problems"])
    assert client.get("/api/games/sample-sample02/report.pdf").status_code == 200


def test_delete_game_removes_related(client):
    gid = client.post("/api/games", json={"kif": read_sample("sample02.kif")}).json()["id"]
    client.put(f"/api/games/{gid}/marks/10", json={})
    client.post(f"/api/games/{gid}/agent/questions")
    assert client.delete(f"/api/games/{gid}").status_code == 200
    assert client.get(f"/api/games/{gid}").status_code == 404
    c_store = client.app.state.store
    assert c_store.list_marks(gid) == [] and c_store.list_runs(gid) == []


def test_samples_cannot_be_deleted(client):
    assert client.delete("/api/games/sample-sample01").status_code == 403
    client.post("/api/games", json={"kif": read_sample("sample02.kif")})
    assert client.delete("/api/games").json()["deleted"] == 1
    assert {i["id"] for i in client.get("/api/games").json()} == {"sample-sample01", "sample-sample02"}


def test_daily_run_limit():
    c, _ = make_client(daily_run_limit=2)
    login(c)
    for _ in range(2):
        assert c.post("/api/games/sample-sample01/agent/questions").status_code == 200
    assert c.post("/api/games/sample-sample01/agent/questions").status_code == 429


def test_unhandled_error_is_500_json_not_traceback():
    c, app = make_client()
    login(c)

    def boom(*a, **k):
        raise RuntimeError("x")
    app.state.store.list_games = boom
    r = c.get("/api/games")
    assert r.status_code == 500 and r.json() == {"detail": "サーバーでエラーが起きた"}
