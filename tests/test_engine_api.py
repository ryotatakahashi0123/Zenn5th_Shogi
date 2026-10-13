"""shogi-engine API のテスト（エンジン実行ファイルが ./engine にある前提）。

    python -m pytest tests/test_engine_api.py
"""
import cshogi
from fastapi.testclient import TestClient

from engine_service.main import app
from shogi_analyzer.kifu_analysis import load_kifu


def _body():
    k = load_kifu("sample.kif")
    return {"start_sfen": k.start_sfen, "moves": [cshogi.move_to_usi(m) for m in k.moves]}


def test_api():
    with TestClient(app) as c:
        assert c.get("/healthz").json()["ok"]
        g = c.post("/v1/game", json={**_body(), "ms": 100}).json()
        assert g["plies"] == 40
        d = c.post("/v1/position", json={**_body(), "ply": 15, "ms": 500}).json()
        assert d["actual"]["move"] == "▲６六角" and len(d["candidates"]) == 3
        assert d["loss_vs_best"] > 0.2
        cm = c.post("/v1/compare", json={**_body(), "ply": 15, "alt_move": "2d3d", "ms": 300}).json()
        assert cm["alternative"]["winrate_for_mover"] > cm["actual"]["winrate_for_mover"]
        # 不正な入力は 400 / 422
        assert c.post("/v1/compare", json={**_body(), "ply": 15, "alt_move": "5a5b"}).status_code == 400
        assert c.post("/v1/game", json={"start_sfen": "x", "moves": ["7g7f"] * 401}).status_code == 422
        assert c.post("/v1/game", json={**_body(), "moves": ["7g7f", "7g7f"]}).status_code == 400
