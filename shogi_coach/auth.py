"""ログイン。利用者名とパスワードで確かめる。

パスワードそのものは保存しない。利用者ごとの塩（salt）を付けた scrypt のハッシュだけを、
保存先（shogi_coach.storage）の auth/users.json に置く。利用者の追加は tools/add_user.py で行う。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time

from . import storage

USERS = "auth/users.json"
NAME_RE = r"[a-z0-9_]{2,20}"
MIN_PASSWORD = 4            # tools/add_user.py（管理者が決めるとき）
MIN_SIGNUP_PASSWORD = 8     # 画面からの登録
MAX_USERS = int(os.environ.get("MAX_USERS", "30"))  # 画面から登録できる利用者の上限（費用が膨らまないように）
SIGNUP_CODE = os.environ.get("SIGNUP_CODE", "")     # 設定すると、登録にこの合言葉が要る
MAX_FAILS, LOCK_SECONDS = 5, 300  # 続けて間違えたら、その利用者名でのログインをしばらく止める（総当たり対策）
_fails: dict[str, tuple[int, float]] = {}  # 利用者名 → (続けて間違えた回数, 止めている期限)。このプロセスの中だけ


def hash_password(password: str, salt: bytes | None = None) -> dict:
    salt = salt or os.urandom(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return {"salt": salt.hex(), "hash": h.hex()}


def verify(rec: dict, password: str) -> bool:
    try:
        want = hash_password(password, bytes.fromhex(rec["salt"]))["hash"]
        return hmac.compare_digest(want, rec["hash"])
    except Exception:
        return False


def load_users() -> dict:
    raw = storage.get(USERS)
    return json.loads(raw) if raw else {}


def add_user(name: str, password: str, min_password: int = MIN_PASSWORD, overwrite: bool = True) -> None:
    """利用者を追加する。overwrite なら、同じ名前があればパスワードを変える（管理者用）。"""
    if not re.fullmatch(NAME_RE, name):
        raise ValueError("利用者名は、半角の英小文字・数字・_ の2〜20文字にしてください")
    if len(password) < min_password:
        raise ValueError(f"パスワードは{min_password}文字以上にしてください")
    users = load_users()
    if name in users and not overwrite:
        raise ValueError("この利用者名は、すでに使われています")
    if name not in users and not overwrite and len(users) >= MAX_USERS:
        raise ValueError("登録できる利用者の数が上限に達しています")
    users[name] = hash_password(password)
    storage.put(USERS, json.dumps(users, ensure_ascii=False, indent=1), "application/json")


def register(name: str, password: str, confirm: str, code: str = "") -> str:
    """画面からの利用者登録。登録した利用者名を返す。すでにある名前は上書きしない。"""
    if SIGNUP_CODE and not hmac.compare_digest(code.strip().encode(), SIGNUP_CODE.encode()):
        raise ValueError("登録の合言葉が違います")
    if password != confirm:
        raise ValueError("パスワード（確認）が一致しません")
    name = name.strip().lower()
    add_user(name, password, MIN_SIGNUP_PASSWORD, overwrite=False)
    return name


def login(name: str, password: str, now: float | None = None, users: dict | None = None) -> bool:
    """正しければ True。続けて間違えた利用者名は、しばらく ValueError（待ち時間の案内）にする。"""
    now = time.time() if now is None else now
    name = name.strip().lower()
    n, until = _fails.get(name, (0, 0.0))
    if now < until:
        raise ValueError(f"続けて間違えたため、この利用者名ではしばらくログインできません（あと約{int((until - now) // 60) + 1}分）")
    users = load_users() if users is None else users
    rec = users.get(name) if re.fullmatch(NAME_RE, name) else None
    # 利用者がいないときも同じ計算をして、返事の速さで利用者の有無が分からないようにする
    ok = verify(rec or hash_password("", b"\0" * 16), password) and rec is not None
    if ok:
        _fails.pop(name, None)
    else:
        n = 1 if until and now >= until else n + 1
        _fails[name] = (n, now + LOCK_SECONDS if n >= MAX_FAILS else 0.0)
    return ok
