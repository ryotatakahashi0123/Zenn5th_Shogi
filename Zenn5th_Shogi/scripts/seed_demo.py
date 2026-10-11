"""手元のサーバーにサンプル局が入っているか確認する小道具。
Cloud Run では SEED_DEMO=1 の起動時に自動で入る(固定IDなので重複しない)。
使い方: python scripts/seed_demo.py   (MemoryStoreに入れて件数を表示するだけ)"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import service  # noqa: E402
from app.infra.config import Settings  # noqa: E402
from app.infra.store import MemoryStore  # noqa: E402

cfg = Settings()
cfg.validate()
st = MemoryStore()
print("seeded:", service.seed_samples(st, cfg, "demo"), [g["id"] for g in st.list_games("demo")])
