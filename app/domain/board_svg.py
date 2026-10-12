"""局面のSVG描画。振り返りビューが使う(PDFは infra/pdf.py が同じ Position から別に描く。docs/decisions.md D-6)。"""
from __future__ import annotations

from html import escape
from typing import Optional

from .board import FW, KN, HAND_ORDER, Position

CELL = 38
MARGIN_X = 22
HAND_H = 30
LABEL_H = 18
FONT = "'Hiragino Mincho ProN','Yu Mincho','Noto Serif JP','Noto Serif CJK JP',serif"

_KANJI_NUM = ["", "", "二", "三", "四", "五", "六", "七", "八", "九", "十",
              "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八"]


def hand_text(hand: dict) -> str:
    parts = []
    for p in HAND_ORDER:
        c = hand.get(p, 0)
        if c > 0:
            parts.append(p + (_KANJI_NUM[c] if c < len(_KANJI_NUM) else str(c)))
    return " ".join(parts) if parts else "なし"


def piece_label(side: str, piece: str) -> str:
    if piece == "玉":
        return "玉" if side == "b" else "王"
    return piece


def render_board_svg(pos: Position, perspective: str = "b", title: Optional[str] = None) -> str:
    """perspective: 手前に来る側('b'=先手, 'w'=後手)。"""
    if perspective not in ("b", "w"):
        perspective = "b"
    opp = "w" if perspective == "b" else "b"
    W = 9 * CELL + 2 * MARGIN_X
    top_y = HAND_H
    grid_y = top_y + LABEL_H
    grid_x = MARGIN_X
    H = grid_y + 9 * CELL + HAND_H
    mark = {"b": "☗", "w": "☖"}

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{escape(title or "局面図")}" style="width:100%;height:auto;display:block">',
        f'<title>{escape(title or "局面図")}</title>',
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="#fbf6ea"/>',
        f'<text x="{grid_x}" y="{HAND_H / 2 + 5}" font-size="13" font-family="{FONT}" fill="#333">'
        f'{mark[opp]}持駒 {escape(hand_text(pos.hands.get(opp, {})))}</text>',
        f'<text x="{grid_x}" y="{H - HAND_H / 2 + 5}" font-size="13" font-family="{FONT}" fill="#333">'
        f'{mark[perspective]}持駒 {escape(hand_text(pos.hands.get(perspective, {})))}</text>',
    ]

    def disp(file_: int, rank: int) -> tuple:
        if perspective == "b":
            return 9 - file_, rank - 1
        return file_ - 1, 9 - rank

    # 直前の手の移動先
    if pos.last_to:
        c, r = disp(*pos.last_to)
        out.append(f'<rect x="{grid_x + c * CELL}" y="{grid_y + r * CELL}" width="{CELL}" height="{CELL}" '
                   f'fill="#f3c94f" opacity="0.65"/>')
    # 罫線
    for i in range(10):
        x = grid_x + i * CELL
        y = grid_y + i * CELL
        out.append(f'<line x1="{x}" y1="{grid_y}" x2="{x}" y2="{grid_y + 9 * CELL}" stroke="#3a2f1c" stroke-width="1"/>')
        out.append(f'<line x1="{grid_x}" y1="{y}" x2="{grid_x + 9 * CELL}" y2="{y}" stroke="#3a2f1c" stroke-width="1"/>')
    # 筋・段の表記
    for c in range(9):
        f = 9 - c if perspective == "b" else c + 1
        out.append(f'<text x="{grid_x + c * CELL + CELL / 2}" y="{top_y + 13}" font-size="11" text-anchor="middle" '
                   f'font-family="{FONT}" fill="#555">{FW[f]}</text>')
    for r in range(9):
        k = r + 1 if perspective == "b" else 9 - r
        out.append(f'<text x="{grid_x + 9 * CELL + 11}" y="{grid_y + r * CELL + CELL / 2 + 4}" font-size="11" '
                   f'text-anchor="middle" font-family="{FONT}" fill="#555">{KN[k]}</text>')
    # 駒
    for (f, r), (side, piece) in sorted(pos.board.items()):
        c, rr = disp(f, r)
        cx = grid_x + c * CELL + CELL / 2
        cy = grid_y + rr * CELL + CELL / 2
        promoted = piece in ("と", "成香", "成桂", "成銀", "馬", "龍")
        fill = "#b3261e" if promoted else "#1c1710"
        label = piece_label(side, piece)
        fs = 22 if len(label) == 1 else 15
        rot = f' transform="rotate(180 {cx} {cy})"' if side != perspective else ""
        out.append(f'<text x="{cx}" y="{cy}" dy="0.35em" font-size="{fs}" text-anchor="middle" '
                   f'font-family="{FONT}" fill="{fill}"{rot}>{escape(label)}</text>')
    out.append("</svg>")
    return "".join(out)
