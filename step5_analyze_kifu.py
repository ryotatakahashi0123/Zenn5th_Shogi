"""ステップ5: 棋譜ファイルを読み込み、全手を解析してCSVに出力する。

使い方:
    python step5_analyze_kifu.py sample.kif --engine ./engine/YaneuraOu --ms 500 -o result.csv
"""
import argparse

from shogi_analyzer.usi_engine import UsiEngine
from shogi_analyzer.kifu_analysis import load_kifu, analyze_kifu, write_csv
from step4_one_position import ENGINE_OPTIONS

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("kifu")
    ap.add_argument("--engine", default="./engine/YaneuraOu")
    ap.add_argument("--ms", type=int, default=500, help="1局面あたりの思考時間(ms)")
    ap.add_argument("-o", "--out", default="result.csv")
    args = ap.parse_args()

    kifu = load_kifu(args.kifu)
    print(f"{kifu.names[0]} vs {kifu.names[1]} / {len(kifu.moves)}手")
    with UsiEngine(args.engine, ENGINE_OPTIONS) as eng:
        rows = analyze_kifu(kifu, eng, args.ms,
                            progress=lambda i, n: print(f"\r{i}/{n}", end="", flush=True))
    print()
    write_csv(rows, args.out)
    for r in rows:
        if r.label:
            print(f"{r.ply:>3} {r.side}{r.move_jp:<10} {r.label} (勝率 -{r.winrate_drop:.0%}) 最善: {r.best_before_jp}")
    print("saved:", args.out)
