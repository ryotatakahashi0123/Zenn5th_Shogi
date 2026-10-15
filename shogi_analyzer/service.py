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
from .kifu_analysis import Kifu, MAX_PLIES, analyze_kifu, kif_move, validate_kifu, winrate

DEEP_MS = 2000       # 深掘りの思考時間
DEEP_MULTIPV = 3     # 深掘りで出す候補手の数
LINE_PLIES = 12      # 画面・ノート・AIエージェントに出す読み筋の長さ（発案者の指示 2026-10-14: その先の展開まで出す）
EXTEND_MS = 400      # 読み筋を伸ばすときの1回の思考時間
EXTEND_CALLS = 3     # 読み筋を伸ばすために読み直す回数の上限（1つの読み筋あたり）
PROMOTED_FILL = "#d0021b"  # 成り駒の色（発案者の指示 2026-10-14。成ったことがひと目で分かるように）


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
    return mark(board) + re.sub(r"\(\d\d\)$", "", kif_move(board, m))


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


def legal_prefix(board: cshogi.Board, line: list[str]) -> list[str]:
    """読み筋のうち、その局面から順に指せる手までを返す（合わない手から先は捨てる）。"""
    b, out = board.copy(), []
    for u in line:
        try:
            m = b.move_from_usi(u)
        except Exception:
            break
        if not m or not b.is_legal(m):
            break
        out.append(u)
        b.push(m)
    return out


def extend_pv(engine: UsiEngine, start_sfen: str, moves: list[str], pv: list[str], want: int = LINE_PLIES,
              ms: int = EXTEND_MS, calls: int = EXTEND_CALLS) -> list[str]:
    """読み筋が短く途切れたとき、終わりの局面をエンジンで読み直して続きをつなげる（エンジンを直接使う版）。

    start_sfen + moves が読み筋の始まる局面。終局したら伸ばさない。返すのは読み筋（USI）だけ。
    """
    board = cshogi.Board(start_sfen)
    for u in moves:
        board.push(board.move_from_usi(u))
    pv = legal_prefix(board, pv)
    for _ in range(calls):
        if len(pv) >= want:
            break
        end = board.copy()
        for u in pv:
            end.push(end.move_from_usi(u))
        if end.is_game_over():
            break
        more = legal_prefix(end, engine.analyze(f"sfen {start_sfen}", moves + pv, ms).pv)
        if not more:
            break
        pv += more
    return pv[:want]


def extend_line(client, start_sfen: str, line: list[str], want: int = LINE_PLIES, ms: int = EXTEND_MS,
                calls: int = EXTEND_CALLS) -> list[str]:
    """extend_pv の、エンジンの窓口（shogi_coach.engine_client）を使う版。新しいエンジンAPIを足さないため、
    読み筋の最後の1手だけの短い棋譜を全手解析にかけ、その手の後の読み筋（行の pv）を続きとして使う。

    line は start_sfen から始まる手順（USI）。伸ばした手順を返す（最初の line も含む）。
    """
    board = cshogi.Board(start_sfen)
    line = legal_prefix(board, line)
    for _ in range(calls):
        if len(line) >= want or not line:
            break
        before = board.copy()
        for u in line[:-1]:
            before.push(before.move_from_usi(u))
        end = before.copy()
        end.push(end.move_from_usi(line[-1]))
        if end.is_game_over():
            break
        rows = client.analyze_game(Kifu(["", ""], before.sfen(), [before.move_from_usi(line[-1])]), ms)["rows"]
        more = legal_prefix(end, (rows[-1].get("pv") or "").split() if rows else [])
        if not more:
            break
        line += more
    return line[:want]


def _cand(board: cshogi.Board, r: AnalysisResult, line: list[str] | None = None) -> dict:
    """候補手1つを、指す側から見た評価値・勝率で表す。line があれば、それを読み筋にする（伸ばしたもの）。"""
    me = r.score_for_sente(True)  # 手番側視点の値（score_for_sente(True) は符号を変えない）
    return {
        "move_usi": r.bestmove,
        "move": move_jp(board, r.bestmove) if r.bestmove else "",
        "eval_for_mover": me,
        "winrate_for_mover": round(winrate(me), 3),
        "mate_in": r.score_mate,
        "line": pv_jp(board, line if line is not None else r.pv, LINE_PLIES),
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
    cands = [_cand(board, c, extend_pv(engine, kifu.start_sfen, usi, c.pv)) for c in res.candidates if c.bestmove]
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
        "line": [move_jp(board, move_usi)] + pv_jp(after, extend_pv(engine, start_sfen, usi + [move_usi], r.pv,
                                                                     LINE_PLIES - 1), LINE_PLIES - 1),
        "depth": r.depth,
    }


# ---------------- 画面用（エンジンを使わない） ----------------

def move_list(kifu: Kifu) -> list[dict]:
    """手数・手番・指し手の一覧。解析結果を見せる前の画面で、手を選ぶのに使う。"""
    board = cshogi.Board(kifu.start_sfen)
    out = []
    for i, m in enumerate(kifu.moves, start=1):
        out.append({"ply": i, "side": mark(board), "move_jp": kif_move(board, m)})
        board.push(m)
    return out


def _lines_usi(row: dict, ext: dict | None) -> dict:
    """指す前の局面からの読み筋（USI の並び）。best は最善手から、actual は実戦の手から。"""
    ext = ext or {}
    best = ext.get(ext_key(row["ply"], "best")) or (row.get("best_pv") or "").split()
    actual = ext.get(ext_key(row["ply"], "actual"))
    if actual is None:
        pv = (row.get("pv") or "").split()
        actual = [row["move_usi"]] + pv if pv else []
    return {"best": list(best), "actual": list(actual)}


def lines_jp(kifu: Kifu, row: dict, ext: dict | None = None) -> dict:
    """全手解析の行にある読み筋（USI）を、日本語の手順にする。エンジンは呼ばない。

    best: 指す前の局面で、エンジンの最善手から始まる手順。actual: 実戦の手と、その後の手順。
    ext: 伸ばした読み筋（ext_key → 指す前の局面からの USI の手順。extend_rows が作る）。あればそれを使う。
    読み筋が行に無い（古い結果・終局）ときは空の並び。
    """
    try:
        before, _ = board_after(kifu, row["ply"] - 1)
    except Exception:
        return {"best": [], "actual": []}

    def line(usi):
        try:
            return pv_jp(before, usi, LINE_PLIES)
        except Exception:  # 局面に合わない手が混じっていても、画面やPDFは止めない
            return []
    u = _lines_usi(row, ext)
    actual = line(u["actual"])
    return {"best": line(u["best"]), "actual": actual if len(actual) > 1 else []}


def line_positions(kifu: Kifu, row: dict, ext: dict | None = None) -> dict:
    """読み筋を1手ずつ盤で見るための局面の並び（ポップアップ用。エンジンは呼ばない）。

    best / actual のそれぞれが [{"sfen", "move_usi", "move_jp", "before"}, ...]。before はその手を指す前の SFEN（色を付けるのに使う）。
    先頭は指す前の局面（move_jp は None。move_usi は直前の実戦の手）で、
    i 番目は読み筋の i 手目を指した後の局面。指せない手が出たら、そこで打ち切る。lines_jp と同じく、
    実戦の手からの読み筋は実戦の手だけ（続きが無い）なら空にする。
    """
    try:
        start, last = board_after(kifu, row["ply"] - 1)
    except Exception:
        return {"best": [], "actual": []}
    prev = board_after(kifu, row["ply"] - 2)[0].sfen() if row["ply"] > 1 else None
    first = {"sfen": start.sfen(), "move_usi": cshogi.move_to_usi(last) if last else None, "move_jp": None, "before": prev}
    out = {}
    for which, usi in _lines_usi(row, ext).items():
        board, steps = cshogi.Board(start.sfen()), [first]
        for u in usi[:LINE_PLIES]:
            try:
                mv = board.move_from_usi(u)
                if not mv or not board.is_legal(mv):
                    break
                jp = move_jp(board, u)
            except Exception:
                break
            before = board.sfen()
            board.push(mv)
            steps.append({"sfen": board.sfen(), "move_usi": u, "move_jp": jp, "before": before})
        out[which] = steps if len(steps) > (2 if which == "actual" else 1) else []
    return out


def position_svg(sfen: str, last_usi: str | None = None, before: str | None = None, scale: float = 2.0,
                 background: str = "#f5deb3") -> str:
    """SFEN の局面図（画面用）。last_usi の手（before の局面で指した手）のマスに色を付ける。盤の色を敷き、成り駒は赤。"""
    board, last = cshogi.Board(sfen), None
    if last_usi and before:
        try:
            last = cshogi.Board(before).move_from_usi(last_usi) or None
        except Exception:
            last = None
    head, rest = to_svg(board, last, scale).split(">", 1)
    return f'{head}><rect width="100%" height="100%" fill="{background}"/>{rest}'


def ext_key(ply: int, which: str) -> str:
    return f"{ply}:{which}"


def extend_rows(client, kifu: Kifu, rows: list[dict], plies, cache: dict, ms: int = EXTEND_MS,
                cancel=lambda: False) -> dict:
    """指定した手数の読み筋（最善手から・実戦の手から）を LINE_PLIES 手まで伸ばし、cache に入れる。

    cache: ext_key → 指す前の局面からの USI の手順。すでにあるものは読み直さない。失敗したら元の読み筋のまま入れる。
    """
    by_ply = {r["ply"]: r for r in rows}
    for ply in plies:
        r = by_ply.get(ply)
        if r is None or cancel():
            continue
        try:
            start = board_after(kifu, ply - 1)[0].sfen()
        except Exception:
            continue
        pv = (r.get("pv") or "").split()
        for which, line in (("best", (r.get("best_pv") or "").split()),
                            ("actual", [r["move_usi"]] + pv if pv else [])):
            k = ext_key(ply, which)
            if k in cache:
                continue
            try:
                cache[k] = extend_line(client, start, line) if line else []
            except Exception:
                cache[k] = line
    return cache


def board_after(kifu: Kifu, ply: int) -> tuple[cshogi.Board, int | None]:
    """ply手目を指した後の局面と、その手（0 は開始局面で、手は None）。"""
    if not 0 <= ply <= len(kifu.moves):
        raise ValueError(f"手数は0〜{len(kifu.moves)}で指定してください")
    board = cshogi.Board(kifu.start_sfen)
    for m in kifu.moves[:ply]:
        board.push(m)
    return board, (kifu.moves[ply - 1] if ply else None)


def red_promoted(svg: str) -> str:
    """cshogi の図で、成り駒（と・成香・成桂・成銀・馬・龍）の字を赤くする。駒の形は図の先頭（defs）で1回だけ定義されている。"""
    return re.sub(r'(<g id="(?:black|white)-(?:pro-[a-z]+|horse|dragon)")', rf'\1 fill="{PROMOTED_FILL}"', svg)


def king_glyphs(svg: str) -> str:
    """cshogi の図は先手・後手とも「王」なので、後手の玉を「玉」にする（先手は「王」のまま。発案者の指示 2026-10-15）。"""
    return re.sub(r'(<g id="white-king"[^>]*><text[^>]*>)&#29579;', r"\1&#29577;", svg)


def to_svg(board: cshogi.Board, lastmove: int | None = None, scale: float = 2.0) -> str:
    """cshogi の局面図（SVG）に、成り駒の色を付け、後手の玉を「玉」にしたもの。"""
    return king_glyphs(red_promoted(str(board.to_svg(lastmove=lastmove, scale=scale))))


def board_svg(kifu: Kifu, ply: int, scale: float = 2.0, background: str | None = "#f5deb3") -> str:
    """ply手目を指した後の局面図（SVG）。その手のマスを強調する。0 は開始局面。

    cshogi の図は背景が透明なので、暗い画面でも見えるよう盤の色を敷く（印刷用は None で敷かない）。
    """
    board, last = board_after(kifu, ply)
    svg = to_svg(board, last, scale)
    if not background:
        return svg
    head, rest = svg.split(">", 1)
    return f'{head}><rect width="100%" height="100%" fill="{background}"/>{rest}'


def sfen_svg(sfen: str, scale: float = 2.0, background: str = "#f5deb3") -> str:
    """SFEN の局面図（画面用。盤の色を敷き、成り駒は赤）。"""
    head, rest = to_svg(cshogi.Board(sfen), None, scale).split(">", 1)
    return f'{head}><rect width="100%" height="100%" fill="{background}"/>{rest}'
