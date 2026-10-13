"""エンジンAPIとエージェントの両方から使う解析機能。

入力はすべて「開始局面SFEN + USI指し手列」で受け取り、結果は JSON にできる dict で返す。
将棋の良し悪しの判断はすべてエンジンの数値で行い、言語モデルには数値だけを渡す。
"""
from __future__ import annotations

import re
from dataclasses import asdict

import cshogi
import cshogi.KIF

from .usi_engine import UsiEngine, AnalysisResult
from .kifu_analysis import Kifu, MAX_PLIES, analyze_kifu, validate_kifu, winrate

DEEP_MS = 2000       # 深掘りの思考時間
DEEP_MULTIPV = 3     # 深掘りで出す候補手の数


def kifu_from_usi(start_sfen: str, moves_usi: list[str]) -> Kifu:
    """API入力から Kifu を作り、合法性を検査する（不正手をエンジンに送らないため）。"""
    if len(moves_usi) > MAX_PLIES:
        raise ValueError(f"{MAX_PLIES}手を超える棋譜には対応していません")
    board = cshogi.Board(start_sfen)
    moves = []
    for i, u in enumerate(moves_usi, start=1):
        if not re.fullmatch(r"[1-9][a-i][1-9][a-i]\+?|[PLNSGBR]\*[1-9][a-i]", u):
            raise ValueError(f"{i}手目 '{u}' はUSI形式の指し手ではありません")
        m = board.move_from_usi(u)
        if not m or not board.is_legal(m):
            raise ValueError(f"{i}手目 {u} は不正な手です")
        board.push(m)
        moves.append(m)
    k = Kifu(["", ""], start_sfen, moves)
    validate_kifu(k)
    return k


def mark(board: cshogi.Board) -> str:
    return "▲" if board.turn == cshogi.BLACK else "△"


def move_jp(board: cshogi.Board, usi: str) -> str:
    """'7g7f' → '▲７六歩'（移動元の(77)は省く）。"""
    m = board.move_from_usi(usi)
    if not m:
        return usi
    return mark(board) + re.sub(r"\(\d\d\)$", "", cshogi.KIF.move_to_kif(m))


def pv_jp(board: cshogi.Board, pv: list[str], limit: int = 6) -> list[str]:
    b = board.copy()
    out = []
    for u in pv[:limit]:
        m = b.move_from_usi(u)
        if not m or not b.is_legal(m):
            break
        out.append(move_jp(b, u))
        b.push(m)
    return out


def _board_at(kifu: Kifu, ply: int) -> tuple[cshogi.Board, list[str]]:
    """ply手目を指す直前の局面と、そこまでのUSI指し手列。"""
    if not 1 <= ply <= len(kifu.moves):
        raise ValueError(f"手数は1〜{len(kifu.moves)}で指定してください")
    board = cshogi.Board(kifu.start_sfen)
    usi = []
    for m in kifu.moves[: ply - 1]:
        usi.append(cshogi.move_to_usi(m))
        board.push(m)
    return board, usi


def _cand(board: cshogi.Board, r: AnalysisResult) -> dict:
    """候補手1つを、指す側から見た評価値・勝率で表す。"""
    me = r.score_for_sente(True)  # 手番側視点の値（score_for_sente(True) は符号を変えない）
    return {
        "move_usi": r.bestmove,
        "move": move_jp(board, r.bestmove) if r.bestmove else "",
        "eval_for_mover": me,
        "winrate_for_mover": round(winrate(me), 3),
        "mate_in": r.score_mate,
        "line": pv_jp(board, r.pv),
        "depth": r.depth,
    }


# ---------------- 公開する3機能 ----------------

def analyze_game(engine: UsiEngine, kifu: Kifu, ms: int = 400) -> dict:
    rows = analyze_kifu(kifu, engine, ms)
    return {
        "plies": len(kifu.moves),
        "rows": [asdict(r) for r in rows],
    }


def deep_dive(engine: UsiEngine, kifu: Kifu, ply: int, ms: int = DEEP_MS,
              multipv: int = DEEP_MULTIPV) -> dict:
    """ply手目の直前局面を長めに読み、候補手上位と実戦の手を比べる。"""
    board, usi = _board_at(kifu, ply)
    actual = cshogi.move_to_usi(kifu.moves[ply - 1])
    res = engine.analyze(f"sfen {kifu.start_sfen}", usi, ms, multipv=multipv)
    cands = [_cand(board, c) for c in res.candidates if c.bestmove]
    actual_c = next((c for c in cands if c["move_usi"] == actual), None)
    if actual_c is None:
        actual_c = _eval_move(engine, kifu.start_sfen, usi, board, actual, ms // 2)
    return {
        "ply": ply,
        "side": mark(board),
        "actual": actual_c,
        "candidates": cands,
        "loss_vs_best": round(cands[0]["winrate_for_mover"] - actual_c["winrate_for_mover"], 3) if cands else None,
    }


def compare_move(engine: UsiEngine, kifu: Kifu, ply: int, alt_move_usi: str, ms: int = 1500) -> dict:
    """ply手目で別の手（alt_move_usi）を指していたらどうなったかを、実戦の手と比べる。"""
    board, usi = _board_at(kifu, ply)
    m = board.move_from_usi(alt_move_usi)
    if not m or not board.is_legal(m):
        raise ValueError(f"{alt_move_usi} はこの局面で指せない手です")
    actual = cshogi.move_to_usi(kifu.moves[ply - 1])
    return {
        "ply": ply,
        "side": mark(board),
        "actual": _eval_move(engine, kifu.start_sfen, usi, board, actual, ms),
        "alternative": _eval_move(engine, kifu.start_sfen, usi, board, alt_move_usi, ms),
    }


def _eval_move(engine, start_sfen, usi, board, move_usi, ms) -> dict:
    """指定の手を指した後の局面を読み、指した側から見た値に直す。"""
    r = engine.analyze(f"sfen {start_sfen}", usi + [move_usi], ms)
    after = board.copy()
    after.push(after.move_from_usi(move_usi))
    me = -r.score_for_sente(True)  # 相手番の値なので符号反転
    mate = -r.score_mate if r.score_mate is not None else None
    return {
        "move_usi": move_usi,
        "move": move_jp(board, move_usi),
        "eval_for_mover": me,
        "winrate_for_mover": round(winrate(me), 3),
        "mate_in": mate,
        "line": [move_jp(board, move_usi)] + pv_jp(after, r.pv, 5),
        "depth": r.depth,
    }


# ---------------- 画面用（エンジンを使わない） ----------------

def move_list(kifu: Kifu) -> list[dict]:
    """手数・手番・指し手の一覧。解析結果を見せる前の画面で、手を選ぶのに使う。"""
    board = cshogi.Board(kifu.start_sfen)
    out = []
    for i, m in enumerate(kifu.moves, start=1):
        out.append({"ply": i, "side": mark(board), "move_jp": cshogi.KIF.move_to_kif(m)})
        board.push(m)
    return out


def lines_jp(kifu: Kifu, row: dict) -> dict:
    """全手解析の行にある読み筋（USI）を、日本語の手順にする。エンジンは呼ばない。

    best: 指す前の局面で、エンジンの最善手から始まる手順。actual: 実戦の手と、その後の手順。
    読み筋が行に無い（古い結果・終局）ときは空の並び。
    """
    def line(ply, pv):
        try:
            return pv_jp(board_after(kifu, ply)[0], (pv or "").split(), 8)
        except Exception:  # 局面に合わない手が混じっていても、画面やPDFは止めない
            return []
    before, _ = board_after(kifu, row["ply"] - 1)
    actual = line(row["ply"], row.get("pv"))
    return {"best": line(row["ply"] - 1, row.get("best_pv")),
            "actual": [move_jp(before, row["move_usi"])] + actual if actual else []}


def board_after(kifu: Kifu, ply: int) -> tuple[cshogi.Board, int | None]:
    """ply手目を指した後の局面と、その手（0 は開始局面で、手は None）。"""
    if not 0 <= ply <= len(kifu.moves):
        raise ValueError(f"手数は0〜{len(kifu.moves)}で指定してください")
    board = cshogi.Board(kifu.start_sfen)
    for m in kifu.moves[:ply]:
        board.push(m)
    return board, (kifu.moves[ply - 1] if ply else None)


def board_svg(kifu: Kifu, ply: int, scale: float = 2.0, background: str | None = "#f5deb3") -> str:
    """ply手目を指した後の局面図（SVG）。その手のマスを強調する。0 は開始局面。

    cshogi の図は背景が透明なので、暗い画面でも見えるよう盤の色を敷く（印刷用は None で敷かない）。
    """
    board, last = board_after(kifu, ply)
    svg = str(board.to_svg(lastmove=last, scale=scale))
    if not background:
        return svg
    head, rest = svg.split(">", 1)
    return f'{head}><rect width="100%" height="100%" fill="{background}"/>{rest}'
