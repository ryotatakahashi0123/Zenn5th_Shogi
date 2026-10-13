"""動作確認用のサンプル棋譜を、エンジン同士の対局から生成する（自作の棋譜なので権利上の問題がない）。

15手目だけわざと評価の低い手を指させ、悪手検出の確認に使う。
"""
import sys
import random
from pathlib import Path

import cshogi
import cshogi.KIF

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shogi_analyzer.usi_engine import UsiEngine  # noqa: E402

ENGINE = sys.argv[1] if len(sys.argv) > 1 else "./engine/YaneuraOu"
OUT = sys.argv[2] if len(sys.argv) > 2 else "sample.kif"
PLIES, BLUNDER_PLY = 40, 15
random.seed(7)

board = cshogi.Board()
moves = []
with UsiEngine(ENGINE, {"BookFile": "no_book", "FV_SCALE": 24, "Threads": 2}) as eng:
    for ply in range(1, PLIES + 1):
        if board.is_game_over():
            break
        if ply == BLUNDER_PLY:
            # 合法手のうち、取られるだけの手を選びやすいよう駒打ち以外からランダム
            mv = random.choice([m for m in board.legal_moves if not cshogi.move_is_drop(m)])
            usi = cshogi.move_to_usi(mv)
        else:
            usi = eng.analyze("startpos", moves, byoyomi_ms=100).bestmove
            if usi in ("resign", "win"):
                break
            mv = board.move_from_usi(usi)
        board.push(mv)
        moves.append(usi)

exp = cshogi.KIF.Exporter(OUT)
exp.header(["サンプル先手", "サンプル後手"], starttime=None)
b = cshogi.Board()
for usi in moves:
    m = b.move_from_usi(usi)
    exp.move(m)
    b.push(m)
exp.end("resign" if not board.is_game_over() else "resign")
exp.close()
print(f"saved {OUT}: {len(moves)} moves")
