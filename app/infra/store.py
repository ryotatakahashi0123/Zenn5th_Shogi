"""保存先。MemoryStore(試験・ローカル)と FirestoreStore(Cloud Run)。

FirestoreStore は、このリポジトリの作成時点で実物のFirestoreに対して未検証
(docs/decisions.md の未確認事項)。MemoryStore と同じ契約で書いてあり、
tests/test_store_contract.py の契約試験を FIRESTORE_EMULATOR_HOST 付きで流せば確かめられる。
"""
from __future__ import annotations

import copy
import json
import pathlib
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

ROOT = pathlib.Path(__file__).resolve().parents[2]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class Store(Protocol):
    def ping(self) -> bool: ...
    def create_game(self, doc: dict) -> str: ...
    def get_game(self, game_id: str) -> Optional[dict]: ...
    def list_games(self, owner: str) -> list[dict]: ...
    def delete_game(self, game_id: str) -> bool: ...
    def delete_all_games(self, owner: str) -> int: ...
    def put_mark(self, game_id: str, mark: dict) -> None: ...
    def list_marks(self, game_id: str) -> list[dict]: ...
    def delete_mark(self, game_id: str, move_no: int) -> bool: ...
    def add_run(self, game_id: str, run: dict) -> None: ...
    def list_runs(self, game_id: str) -> list[dict]: ...
    def incr_quota(self, owner: str, day: str) -> int: ...
    def list_cards(self) -> list[dict]: ...
    def put_card(self, card: dict) -> str: ...


def load_cards_file(path: Optional[pathlib.Path] = None) -> list[dict]:
    p = path or (ROOT / "knowledge" / "cards.json")
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [c for c in data if not str(c.get("id", "")).startswith("_")]


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.games: dict[str, dict] = {}
        self.marks: dict[str, dict[int, dict]] = {}
        self.runs: dict[str, list[dict]] = {}
        self.quota: dict[str, int] = {}
        self.cards: dict[str, dict] = {}

    def ping(self) -> bool:
        return True

    def create_game(self, doc: dict) -> str:
        with self._lock:
            gid = doc.get("id") or new_id()
            doc = copy.deepcopy(doc)
            doc["id"] = gid
            self.games[gid] = doc
            return gid

    def get_game(self, game_id: str) -> Optional[dict]:
        with self._lock:
            g = self.games.get(game_id)
            return copy.deepcopy(g) if g else None

    def list_games(self, owner: str) -> list[dict]:
        with self._lock:
            gs = [copy.deepcopy(g) for g in self.games.values() if g["ownerUid"] == owner]
        return sorted(gs, key=lambda g: g["createdAt"], reverse=True)

    def delete_game(self, game_id: str) -> bool:
        with self._lock:
            existed = self.games.pop(game_id, None) is not None
            self.marks.pop(game_id, None)
            self.runs.pop(game_id, None)
            return existed

    def delete_all_games(self, owner: str) -> int:
        ids = [g["id"] for g in self.list_games(owner) if not g.get("sample")]
        for i in ids:
            self.delete_game(i)
        return len(ids)

    def put_mark(self, game_id: str, mark: dict) -> None:
        with self._lock:
            self.marks.setdefault(game_id, {})[int(mark["moveNo"])] = copy.deepcopy(mark)

    def list_marks(self, game_id: str) -> list[dict]:
        with self._lock:
            return [copy.deepcopy(v) for _, v in sorted(self.marks.get(game_id, {}).items())]

    def delete_mark(self, game_id: str, move_no: int) -> bool:
        with self._lock:
            return self.marks.get(game_id, {}).pop(int(move_no), None) is not None

    def add_run(self, game_id: str, run: dict) -> None:
        with self._lock:
            self.runs.setdefault(game_id, []).append(copy.deepcopy(run))

    def list_runs(self, game_id: str) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self.runs.get(game_id, []))

    def incr_quota(self, owner: str, day: str) -> int:
        with self._lock:
            k = f"{owner}_{day}"
            self.quota[k] = self.quota.get(k, 0) + 1
            return self.quota[k]

    def list_cards(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(list(self.cards.values()))

    def put_card(self, card: dict) -> str:
        with self._lock:
            cid = card.get("id") or new_id()
            self.cards[cid] = {**copy.deepcopy(card), "id": cid}
            return cid


class FirestoreStore:
    """Firestore版。構造は docs/architecture.md の「Firestore のデータ構造」。"""

    def __init__(self, project: Optional[str] = None) -> None:
        from google.cloud import firestore  # 遅延import(ローカル試験では不要)

        self._fs = firestore
        self.db = firestore.Client(project=project or None)

    # ---- 基本 ----
    def ping(self) -> bool:
        next(iter(self.db.collection("games").limit(1).stream()), None)
        return True

    def _g(self, gid: str):
        return self.db.collection("games").document(gid)

    def create_game(self, doc: dict) -> str:
        gid = doc.get("id") or new_id()
        data = {**doc, "id": gid}
        self._g(gid).set(data)
        return gid

    def get_game(self, game_id: str) -> Optional[dict]:
        snap = self._g(game_id).get()
        return snap.to_dict() if snap.exists else None

    def list_games(self, owner: str) -> list[dict]:
        # 複合インデックスを避けるため、並べ替えはPython側で行う
        q = self.db.collection("games").where("ownerUid", "==", owner)
        return sorted((s.to_dict() for s in q.stream()), key=lambda g: g["createdAt"], reverse=True)

    def _delete_sub(self, ref) -> None:
        for sub in ref.collections():
            for d in sub.stream():
                d.reference.delete()

    def delete_game(self, game_id: str) -> bool:
        ref = self._g(game_id)
        if not ref.get().exists:
            return False
        self._delete_sub(ref)
        ref.delete()
        return True

    def delete_all_games(self, owner: str) -> int:
        n = 0
        for g in self.list_games(owner):
            if not g.get("sample") and self.delete_game(g["id"]):
                n += 1
        return n

    # ---- 印(reviews) ----
    def put_mark(self, game_id: str, mark: dict) -> None:
        self._g(game_id).collection("reviews").document(str(int(mark["moveNo"]))).set(mark)

    def list_marks(self, game_id: str) -> list[dict]:
        ms = [s.to_dict() for s in self._g(game_id).collection("reviews").stream()]
        return sorted(ms, key=lambda m: m["moveNo"])

    def delete_mark(self, game_id: str, move_no: int) -> bool:
        ref = self._g(game_id).collection("reviews").document(str(int(move_no)))
        if not ref.get().exists:
            return False
        ref.delete()
        return True

    # ---- 実行記録(runs) ----
    def add_run(self, game_id: str, run: dict) -> None:
        self._g(game_id).collection("runs").document(run["id"]).set(run)

    def list_runs(self, game_id: str) -> list[dict]:
        rs = [s.to_dict() for s in self._g(game_id).collection("runs").stream()]
        return sorted(rs, key=lambda r: r["startedAt"])

    # ---- 実行回数の上限 ----
    def incr_quota(self, owner: str, day: str) -> int:
        ref = self.db.collection("quota").document(f"{owner}_{day}")
        ref.set({"count": self._fs.Increment(1), "owner": owner, "day": day}, merge=True)
        return int(ref.get().to_dict()["count"])

    # ---- 知識カード ----
    def list_cards(self) -> list[dict]:
        return [s.to_dict() for s in self.db.collection("knowledge_cards").stream()]

    def put_card(self, card: dict) -> str:
        cid = card.get("id") or new_id()
        self.db.collection("knowledge_cards").document(cid).set({**card, "id": cid})
        return cid


def expire_at(days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def make_store(kind: str, project: str = "") -> "Store":
    if kind == "firestore":
        return FirestoreStore(project)
    return MemoryStore()
