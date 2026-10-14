"""盤面での棋譜入力（平手の開始局面から、1手ずつ指して棋譜を作る）。エンジンは使わない。

マスは USI の書き方（"7g" = ７七）。持ち駒は "P*" のように駒の文字 + "*" で表す。
指せるのは合法手だけ。入力した手順は CSA 形式の棋譜にして、ほかの入力（ファイル・貼り付け）と同じ経路で読み込む。
"""
from __future__ import annotations

import cshogi

from .service import move_jp, to_svg

HAND = "PLNSGBR"  # 持ち駒の並び（cshogi の pieces_in_hand と同じ順。歩 香 桂 銀 金 角 飛）
FILES, RANKS = "987654321", "abcdefghi"  # 画面の左→右、上→下（先手が手前）


def replay(moves: list[str], sfen: str = "") -> tuple[cshogi.Board, list[str]]:
    """入力済みの手順を並べた局面と、日本語の手順。sfen は開始局面（空なら平手）。"""
    board, jp = (cshogi.Board(sfen) if sfen else cshogi.Board()), []
    for i, u in enumerate(moves, start=1):
        m = board.move_from_usi(u)
        if not m or not board.is_legal(m):
            raise ValueError(f"{i}手目 {u} は指せない手です")
        jp.append(move_jp(board, u))
        board.push(m)
    return board, jp


def legal(board: cshogi.Board) -> list[str]:
    return [cshogi.move_to_usi(m) for m in board.legal_moves]


def origins(board: cshogi.Board) -> set[str]:
    """動かせる駒のあるマスと、打てる持ち駒（"P*" など）。"""
    return {u[:2] for u in legal(board)}


def targets(board: cshogi.Board, sel: str) -> set[str]:
    """選んだ駒（マスか持ち駒）の行き先。"""
    return {u[2:4] for u in legal(board) if u[:2] == sel}


def choose(board: cshogi.Board, sel: str, sq: str) -> list[str]:
    """選んだ駒を sq に動かす手。成る・成らないの両方があれば2つ、指せなければ空。"""
    return sorted(u for u in legal(board) if u[:2] == sel and u[2:4] == sq)


def click(moves: list[str], sel: str | None, key: str, sfen: str = "") -> tuple[list[str], str | None, list[str] | None]:
    """マスか持ち駒（key）を押したあとの（手順, 選んでいる駒, 成る・成らないの候補）。

    駒を選ぶ → 行き先を押す、の順で1手になる。成る・成らないの両方が指せるときは、手を進めずに候補を返す。
    指せない場所を押したら、選び直しになる（押した場所に動かせる駒があれば、それを選ぶ）。
    """
    board, _ = replay(moves, sfen)
    found = choose(board, sel, key) if sel else []
    if len(found) == 2:
        return moves, sel, found
    if found:
        return moves + found, None, None
    return moves, key if key in origins(board) else None, None


GEOM = {"width": 230, "height": 192, "x": 20.5, "y": 10.5, "cell": 20}  # cshogi の図（to_svg）の中での盤の位置


def board_svg(board: cshogi.Board, last: int | None = None, sel: str | None = None, goals=(),
              background: str = "#f5deb3") -> str:
    """入力用の盤の図（cshogi の SVG）。選んでいる駒のマスと、行き先のマスに色を付ける。last は直前の手。"""
    def rect(sq, fill):
        x = GEOM["x"] + (9 - int(sq[0])) * GEOM["cell"]
        y = GEOM["y"] + RANKS.index(sq[1]) * GEOM["cell"]
        return f'<rect x="{x}" y="{y}" width="{GEOM["cell"]}" height="{GEOM["cell"]}" fill="{fill}"/>'
    head, rest = to_svg(board, last).split(">", 1)
    defs, body = rest.split("</defs>", 1)  # 直前の手の色（cshogi が付ける）より上に重ねる
    marks = "".join(rect(sq, "#b9d98a") for sq in sorted(goals)) + (rect(sel, "#f08a5d") if sel and sel[1] != "*" else "")
    return f'{head}><rect width="100%" height="100%" fill="{background}"/>{defs}</defs>' + _after_last(body, marks)


def _after_last(body: str, marks: str) -> str:
    """直前の手の色のマス（図の先頭にある fill 付きの rect）の後ろに、選択の色を入れる。"""
    i = 0
    while body.startswith("<rect", i) and 'fill="#f' in body[i:body.index(">", i)]:
        i = body.index(">", i) + 1
    return body[:i] + marks + body[i:]


def hand(board: cshogi.Board, side: int) -> list[tuple[str, str, int]]:
    """side（0 = 先手、1 = 後手）の持ち駒の（"P*" などの印, 駒の字, 枚数）。"""
    return [(HAND[i] + "*", cshogi.HAND_PIECE_JAPANESE_SYMBOLS[i], n)
            for i, n in reversed(list(enumerate(board.pieces_in_hand[side]))) if n]


def to_csa(moves: list[str], resign: bool = False) -> str:
    """入力した手順を CSA 形式の棋譜にする（対局者名は入れない）。resign なら最後に投了を書く。"""
    board, lines = cshogi.Board(), ["V2.2", "PI", "+"]
    for u in moves:
        m = board.move_from_usi(u)
        lines.append(("+" if board.turn == cshogi.BLACK else "-") + cshogi.move_to_csa(m))
        board.push(m)
    return "\n".join(lines + ["%TORYO"] * resign) + "\n"
