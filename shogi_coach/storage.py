"""棋譜・解析結果・利用者の一覧の保存先。BUCKET があれば Cloud Storage、無ければ手元のフォルダー（DATA_DIR、既定は ./data）。

棋譜と結果を保存するかどうかは本人が決める。呼び出し側（app.py）は、本人が保存に同意したときだけ保存する。
バケットでは games/ 以下を30日で自動削除する（deploy/setup.sh のライフサイクルルール）。手元のフォルダーは自動では消えない。

    games/<利用者>/<対局ID>/kifu.kif | kifu.csa   棋譜（対局者名を含む）
    games/<利用者>/<対局ID>/summary.json         印とエンジン判定の集計（数だけ。shogi_analyzer.learn.summarize）
    games/<利用者>/<対局ID>/result.json          解析の行、印、迷った別の読みの評価、AIコーチとのやり取り
    games/<利用者>/trend.json                    複数の対局の傾向まとめ（shogi_coach.trend。対局を保存するたびに作り直す）
    auth/users.json                               利用者とパスワードのハッシュ（shogi_coach.auth）
    prefs/<利用者>.json                          利用者ごとの設定（保存の同意を毎回確認しない、など）
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

BUCKET = os.environ.get("BUCKET", "")
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
_bucket = None


def _gcs():
    global _bucket
    if _bucket is None:
        from google.cloud import storage
        _bucket = storage.Client().bucket(BUCKET)
    return _bucket


def where() -> str:
    return f"gs://{BUCKET}" if BUCKET else str(DATA_DIR)


def put(path: str, data: bytes | str, content_type: str = "text/plain") -> str:
    if BUCKET:
        _gcs().blob(path).upload_from_string(data, content_type=content_type)
    else:
        f = DATA_DIR / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return f"{where()}/{path}"


def get(path: str) -> bytes | None:
    if BUCKET:
        blob = _gcs().blob(path)
        return blob.download_as_bytes() if blob.exists() else None
    f = DATA_DIR / path
    return f.read_bytes() if f.is_file() else None


def ls(prefix: str) -> list[str]:
    """prefix で始まるものの一覧（保存先の中での位置）。"""
    if BUCKET:
        return sorted(b.name for b in _gcs().list_blobs(prefix=prefix))
    base = DATA_DIR / prefix
    return sorted(str(f.relative_to(DATA_DIR)).replace(os.sep, "/") for f in base.rglob("*") if f.is_file())


def _game(user: str, game_id: str) -> str:
    # 利用者名と対局IDは保存先の位置になるので、決まった文字だけを通す
    if not re.fullmatch(r"[a-z0-9_]{1,32}", user) or not re.fullmatch(r"[0-9a-f-]{1,64}", game_id):
        raise ValueError("保存先の名前に使えない文字があります")
    return f"games/{user}/{game_id}"


def save_kifu(user: str, game_id: str, filename: str, kifu_bytes: bytes) -> str:
    ext = ".csa" if filename.lower().endswith(".csa") else ".kif"
    return put(f"{_game(user, game_id)}/kifu{ext}", kifu_bytes)


def save_json(user: str, game_id: str, name: str, obj: dict) -> str:
    return put(f"{_game(user, game_id)}/{name}.json", json.dumps(obj, ensure_ascii=False), "application/json")


def delete_game(user: str, game_id: str) -> int:
    """保存した対局（棋譜・集計・結果）を消す。消した数を返す。元には戻せない。"""
    base = _game(user, game_id) + "/"
    paths = ls(base)
    for path in paths:
        if BUCKET:
            _gcs().blob(path).delete()
        else:
            (DATA_DIR / path).unlink()
    if not BUCKET and (DATA_DIR / base).is_dir():
        (DATA_DIR / base).rmdir()
    return len(paths)


def load_prefs(user: str) -> dict:
    raw = get(f"prefs/{_game(user, '0').split('/')[1]}.json")
    return json.loads(raw) if raw else {}


def save_prefs(user: str, prefs: dict) -> None:
    put(f"prefs/{_game(user, '0').split('/')[1]}.json", json.dumps(prefs, ensure_ascii=False), "application/json")


def load_game(user: str, game_id: str) -> dict | None:
    """保存した対局の棋譜と解析結果（あとから開き直すため）。棋譜が無ければ None。結果が無ければ result は None。"""
    base = _game(user, game_id)
    for ext in (".kif", ".csa"):
        data = get(f"{base}/kifu{ext}")
        if data is not None:
            raw = get(f"{base}/result.json")
            return {"name": "kifu" + ext, "data": data, "result": json.loads(raw) if raw else None}
    return None


def load_summaries(user: str) -> list[dict]:
    """その利用者が保存した対局の集計（summary.json）。壊れたものは飛ばす。"""
    out = []
    for path in ls(_game(user, "0").rsplit("/", 1)[0] + "/"):
        if not path.endswith("/summary.json"):
            continue
        try:
            out.append({**json.loads(get(path)), "game_id": path.split("/")[-2]})
        except Exception:
            continue
    return out


def _user_dir(user: str) -> str:
    return _game(user, "0").rsplit("/", 1)[0]


def save_trend(user: str, obj: dict) -> str:
    return put(f"{_user_dir(user)}/trend.json", json.dumps(obj, ensure_ascii=False), "application/json")


def load_trend(user: str) -> dict | None:
    raw = get(f"{_user_dir(user)}/trend.json")
    return json.loads(raw) if raw else None
