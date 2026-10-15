"""ログイン。利用者名とパスワードで確かめる。

パスワードそのものは保存しない。利用者ごとの塩（salt）を付けた scrypt のハッシュだけを、
保存先（shogi_coach.storage）の auth/users.json に置く。利用者の追加は tools/add_user.py で行う。
画面から登録した利用者は、生年月日も同じ形のハッシュ（`birth`）で持ち、パスワードを忘れたときに
利用者名と生年月日が一致すれば、新しいパスワードに初期化できる（発案者の指示 2026-10-15）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from datetime import date

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


def birth_text(birth: date) -> str:
    return birth.isoformat()  # YYYY-MM-DD


def check_birth(birth, today: date | None = None) -> date:
    if not isinstance(birth, date):
        raise ValueError("生年月日を入力してください")
    if not date(1900, 1, 1) <= birth <= (today or date.today()):
        raise ValueError("生年月日が正しくありません")
    return birth


def load_users() -> dict:
    raw = storage.get(USERS)
    return json.loads(raw) if raw else {}


def add_user(name: str, password: str, min_password: int = MIN_PASSWORD, overwrite: bool = True,
             birth: date | None = None) -> None:
    """利用者を追加する。overwrite なら、同じ名前があればパスワードを変える（管理者用。登録済みの生年月日は残す）。"""
    if not re.fullmatch(NAME_RE, name):
        raise ValueError("利用者名は、半角の英小文字・数字・_ の2〜20文字にしてください")
    if len(password) < min_password:
        raise ValueError(f"パスワードは{min_password}文字以上にしてください")
    users = load_users()
    if name in users and not overwrite:
        raise ValueError("この利用者名は、すでに使われています")
    if name not in users and not overwrite and len(users) >= MAX_USERS:
        raise ValueError("登録できる利用者の数が上限に達しています")
    rec = hash_password(password)
    if birth is not None:
        rec["birth"] = hash_password(birth_text(birth))
    elif "birth" in users.get(name, {}):
        rec["birth"] = users[name]["birth"]
    users[name] = rec
    save_users(users)


def save_users(users: dict) -> None:
    storage.put(USERS, json.dumps(users, ensure_ascii=False, indent=1), "application/json")


def register(name: str, password: str, confirm: str, code: str = "", birth: date | None = None) -> str:
    """画面からの利用者登録。登録した利用者名を返す。すでにある名前は上書きしない。生年月日は必須。"""
    if SIGNUP_CODE and not hmac.compare_digest(code.strip().encode(), SIGNUP_CODE.encode()):
        raise ValueError("登録の合言葉が違います")
    if password != confirm:
        raise ValueError("パスワード（確認）が一致しません")
    name = name.strip().lower()
    add_user(name, password, MIN_SIGNUP_PASSWORD, overwrite=False, birth=check_birth(birth))
    return name


def has_birth(name: str) -> bool:
    return bool(load_users().get(name, {}).get("birth"))


def set_birth(name: str, password: str, birth, now: float | None = None) -> None:
    """ログイン中の利用者が、生年月日を登録する（または変える）。いまのパスワードで本人を確かめる。

    画面の登録より前の利用者が、パスワードを忘れたときの初期化を使えるようにするため（発案者の指示 2026-10-15）。
    """
    now = time.time() if now is None else now
    key = "birth:" + name
    _locked(key, now)
    check_birth(birth)
    users = load_users()
    ok = name in users and verify(users[name], password)
    _count(key, ok, now)
    if not ok:
        raise ValueError("パスワードが違います")
    users[name]["birth"] = hash_password(birth_text(birth))
    save_users(users)


def _locked(key: str, now: float) -> None:
    n, until = _fails.get(key, (0, 0.0))
    if now < until:
        raise ValueError(f"続けて間違えたため、この利用者名ではしばらく受け付けません（あと約{int((until - now) // 60) + 1}分）")


def _count(key: str, ok: bool, now: float) -> None:
    n, until = _fails.get(key, (0, 0.0))
    if ok:
        _fails.pop(key, None)
    else:
        n = 1 if until and now >= until else n + 1
        _fails[key] = (n, now + LOCK_SECONDS if n >= MAX_FAILS else 0.0)


def reset_password(name: str, birth, password: str, confirm: str, now: float | None = None) -> str:
    """パスワードを忘れたとき。利用者名と生年月日が一致すれば、新しいパスワードに初期化して利用者名を返す。

    一致しないときは、利用者がいない場合も同じ文の ValueError にする（利用者の有無が分からないように）。
    生年月日を登録していない利用者（画面の登録より前の利用者）は、管理者が tools/add_user.py で再設定する。
    続けて間違えた利用者名は、ログインと同じようにしばらく止める（生年月日の総当たり対策）。
    """
    now = time.time() if now is None else now
    name = name.strip().lower()
    key = "reset:" + name
    _locked(key, now)
    if password != confirm:
        raise ValueError("新しいパスワード（確認）が一致しません")
    if len(password) < MIN_SIGNUP_PASSWORD:
        raise ValueError(f"新しいパスワードは{MIN_SIGNUP_PASSWORD}文字以上にしてください")
    users = load_users()
    rec = users.get(name) if re.fullmatch(NAME_RE, name) else None
    text = birth_text(birth) if isinstance(birth, date) else ""
    ok = verify((rec or {}).get("birth") or hash_password("", b"\0" * 16), text) and bool(rec and rec.get("birth"))
    _count(key, ok, now)
    if not ok:
        raise ValueError("利用者名か生年月日が違います（生年月日を登録していない利用者は、画面から初期化できません）")
    users[name] = {**hash_password(password), "birth": rec["birth"]}
    save_users(users)
    _fails.pop(name, None)  # ログインの止めも解く
    return name


def login(name: str, password: str, now: float | None = None, users: dict | None = None) -> bool:
    """正しければ True。続けて間違えた利用者名は、しばらく ValueError（待ち時間の案内）にする。"""
    now = time.time() if now is None else now
    name = name.strip().lower()
    until = _fails.get(name, (0, 0.0))[1]
    if now < until:
        raise ValueError(f"続けて間違えたため、この利用者名ではしばらくログインできません（あと約{int((until - now) // 60) + 1}分）")
    users = load_users() if users is None else users
    rec = users.get(name) if re.fullmatch(NAME_RE, name) else None
    # 利用者がいないときも同じ計算をして、返事の速さで利用者の有無が分からないようにする
    ok = verify(rec or hash_password("", b"\0" * 16), password) and rec is not None
    _count(name, ok, now)
    return ok
