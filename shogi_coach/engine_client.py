"""エンジンへの窓口。ENGINE_URL があれば Cloud Run の shogi-engine を IAM 認証付きで呼び、
なければ同じプロセス内でエンジンを動かす（ローカル開発・1サービス構成用）。
"""
from __future__ import annotations

import os
import threading
import time

import cshogi

from shogi_analyzer.kifu_analysis import Kifu

ENGINE_URL = os.environ.get("ENGINE_URL", "").rstrip("/")


def _body(kifu: Kifu) -> dict:
    return {"start_sfen": kifu.start_sfen, "moves": [cshogi.move_to_usi(m) for m in kifu.moves]}


class EngineError(RuntimeError):
    pass


class HttpEngineClient:
    """Cloud Run 間の呼び出し。web 側サービスアカウントの ID トークンを付ける。"""

    def __init__(self, url: str):
        import httpx
        self.url = url
        self.http = httpx.Client(base_url=url, timeout=httpx.Timeout(600, connect=10))
        self._token, self._exp = "", 0.0
        self._lock = threading.Lock()

    def _auth(self) -> dict:
        if not self.url.startswith("https://"):
            return {}  # ローカルの http://localhost では認証しない
        with self._lock:
            if time.time() > self._exp:
                import google.auth.transport.requests
                import google.oauth2.id_token
                self._token = google.oauth2.id_token.fetch_id_token(
                    google.auth.transport.requests.Request(), self.url)
                self._exp = time.time() + 45 * 60  # IDトークンは1時間有効。余裕をみて更新
        return {"Authorization": f"Bearer {self._token}"}

    def _post(self, path: str, body: dict) -> dict:
        r = self.http.post(path, json=body, headers=self._auth())
        if r.status_code == 400:
            raise ValueError(r.json().get("detail", "入力エラー"))
        if r.status_code >= 300:
            raise EngineError(f"engine {r.status_code}: {r.text[:200]}")
        return r.json()

    def analyze_game(self, kifu: Kifu, ms: int = 400) -> dict:
        return self._post("/v1/game", {**_body(kifu), "ms": ms})

    def deep_dive(self, kifu: Kifu, ply: int) -> dict:
        return self._post("/v1/position", {**_body(kifu), "ply": ply})

    def compare_move(self, kifu: Kifu, ply: int, alt_move_usi: str) -> dict:
        return self._post("/v1/compare", {**_body(kifu), "ply": ply, "alt_move": alt_move_usi})


class LocalEngineClient:
    def __init__(self):
        from shogi_analyzer.usi_engine import UsiEngine
        self.engine = UsiEngine(os.environ.get("ENGINE_PATH", "./engine/YaneuraOu"), {
            "FV_SCALE": 24, "USI_Hash": 256, "BookFile": "no_book",
            "Threads": int(os.environ.get("ENGINE_THREADS", "2")),
        })

    def analyze_game(self, kifu: Kifu, ms: int = 400) -> dict:
        from shogi_analyzer import service
        return service.analyze_game(self.engine, kifu, ms)

    def deep_dive(self, kifu: Kifu, ply: int) -> dict:
        from shogi_analyzer import service
        return service.deep_dive(self.engine, kifu, ply)

    def compare_move(self, kifu: Kifu, ply: int, alt_move_usi: str) -> dict:
        from shogi_analyzer import service
        return service.compare_move(self.engine, kifu, ply, alt_move_usi)


_client = None
_client_lock = threading.Lock()


def get_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = HttpEngineClient(ENGINE_URL) if ENGINE_URL else LocalEngineClient()
        return _client
