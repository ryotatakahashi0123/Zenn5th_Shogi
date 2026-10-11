"""設定値。閾値などはここに集める(04の4, 5章)。環境変数で上書きできる。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

CAUSE_TAGS = [
    "読み抜け", "形勢判断の誤り", "焦り・早指し", "時間不足", "定跡・形を知らなかった",
    "詰みの見落とし", "相手の好手に気づかなかった", "欲張り", "手拍子", "その他",
]  # 案。発案者が将棋として妥当か確認する(U-13)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    env: str = "dev"  # Cloud Run上では "production"(K_SERVICE があれば自動)
    store: str = "memory"  # memory | firestore
    brain: str = "fake"  # fake | gemini | none(none=LLMを呼ばない=縮退運転)
    gemini_model: str = "gemini-3.5-flash"  # 候補。提供状況は未確認(U-11)。実装時に公式で再確認
    gcp_project: str = ""
    gcp_location: str = "asia-northeast1"
    session_secret: str = ""
    demo_email: str = ""
    demo_password: str = ""
    my_ids: list = field(default_factory=list)  # 自分の対局者ID(自動で自分の手番を決める)
    seed_demo: bool = True
    ttl_days: int = 30
    max_kif_bytes: int = 200 * 1024
    max_games_per_user: int = 50
    daily_run_limit: int = 30
    # 時間分析(04の4章)
    long_min_sec: int = 30
    long_ratio: float = 4.0
    quick_sec: int = 3
    low_remaining_sec: int = 120
    swing_cp: int = 300
    eval_sente_perspective: bool = True  # 評価値の向き。未確認(U-2)
    # エージェントの上限(04の7.3)
    max_llm_calls: int = 6
    max_tool_calls: int = 20
    max_candidates: int = 5
    max_problems: int = 5  # プロトタイプは5(本番は10)
    max_retries: int = 2
    cause_tags: list = field(default_factory=lambda: list(CAUSE_TAGS))

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        on_cloud_run = bool(e.get("K_SERVICE"))
        s = cls(
            env=e.get("APP_ENV", "production" if on_cloud_run else "dev"),
            store=e.get("STORE", "firestore" if on_cloud_run else "memory"),
            brain=e.get("BRAIN", "gemini" if on_cloud_run else "fake"),
            gemini_model=e.get("GEMINI_MODEL", "gemini-3.5-flash"),
            gcp_project=e.get("GOOGLE_CLOUD_PROJECT", ""),
            gcp_location=e.get("GOOGLE_CLOUD_LOCATION", "asia-northeast1"),
            session_secret=e.get("SESSION_SECRET", ""),
            demo_email=e.get("DEMO_EMAIL", ""),
            demo_password=e.get("DEMO_PASSWORD", ""),
            my_ids=[x.strip() for x in e.get("MY_PLAYER_IDS", "").split(",") if x.strip()],
            seed_demo=e.get("SEED_DEMO", "0" if on_cloud_run else "1") not in ("0", "false", "False"),
            ttl_days=_int("TTL_DAYS", 30),
            daily_run_limit=_int("DAILY_RUN_LIMIT", 30),
            long_min_sec=_int("LONG_MIN_SEC", 30),
            quick_sec=_int("QUICK_SEC", 3),
            low_remaining_sec=_int("LOW_REMAINING_SEC", 120),
            swing_cp=_int("SWING_CP", 300),
        )
        s.validate()
        return s

    def validate(self) -> None:
        """本番で秘密が未設定のまま起動しない。開発時だけ既定値を入れる。"""
        if self.is_production:
            missing = [n for n, v in (("SESSION_SECRET", self.session_secret),
                                      ("DEMO_EMAIL", self.demo_email),
                                      ("DEMO_PASSWORD", self.demo_password)) if not v]
            if missing:
                raise RuntimeError("本番では次の環境変数が必要: " + ", ".join(missing))
        else:
            self.session_secret = self.session_secret or "dev-only-secret"
            self.demo_email = self.demo_email or "demo@example.com"
            self.demo_password = self.demo_password or "demo-pass"
