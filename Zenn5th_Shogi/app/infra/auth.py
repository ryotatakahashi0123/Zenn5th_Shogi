"""テストアカウント認証(メール+パスワード)。署名つきクッキー。

審査員用の共有アカウント1つ(uid="demo")。ソーシャルログイン・メール受信は使わない。
"""
from __future__ import annotations

import hmac
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings

COOKIE = "furikaeri_session"
MAX_AGE = 60 * 60 * 24 * 7
DEMO_UID = "demo"


def _ser(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="furikaeri-session")


def check_credentials(settings: Settings, email: str, password: str) -> Optional[str]:
    """正しければ uid を返す。比較は定数時間。"""
    ok_email = hmac.compare_digest((email or "").strip().lower().encode(), settings.demo_email.lower().encode())
    ok_pass = hmac.compare_digest((password or "").encode(), settings.demo_password.encode())
    return DEMO_UID if (ok_email and ok_pass) else None


def make_token(settings: Settings, uid: str) -> str:
    return _ser(settings).dumps({"uid": uid})


def verify_token(settings: Settings, token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        return _ser(settings).loads(token, max_age=MAX_AGE).get("uid")
    except (BadSignature, SignatureExpired):
        return None
