"""ステップ4: Pythonから1局面の評価値と最善手を取得する。

使い方:
    python step4_one_position.py --engine ./engine/YaneuraOu
"""
import argparse

from shogi_analyzer.usi_engine import UsiEngine

ENGINE_OPTIONS = {
    "FV_SCALE": 24,          # 水匠5の推奨値（Releasesページ記載）
    "USI_Hash": 256,
    "Threads": 2,
    "BookFile": "no_book",   # 定跡を使わず毎局面を探索させる
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="./engine/YaneuraOu")
    ap.add_argument("--ms", type=int, default=1000)
    args = ap.parse_args()

    with UsiEngine(args.engine, ENGINE_OPTIONS) as eng:
        print("engine:", eng.name)
        # 例: ▲7六歩 △3四歩 の局面（次は先手番）
        r = eng.analyze("startpos", ["7g7f", "3c3d"], byoyomi_ms=args.ms)
        print("bestmove:", r.bestmove)
        print("score (手番側):", r.score_cp, "mate:", r.score_mate)
        print("score (先手視点):", r.score_for_sente(side_to_move_is_sente=True))
        print("depth:", r.depth, "pv:", " ".join(r.pv[:8]))
