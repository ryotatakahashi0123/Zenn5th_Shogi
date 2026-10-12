"""局面の再現(外部ライブラリなし)。

平手のみ対応。合法手チェックはしない(棋譜が正しい前提)。
適用に失敗した手があれば、そこまでの局面を返し、errors に理由を残す。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

FW = "０１２３４５６７８９"
KN = "〇一二三四五六七八九"

PROMOTED_BASE = {"と": "歩", "成香": "香", "成桂": "桂", "成銀": "銀", "馬": "角", "龍": "飛", "竜": "飛"}
PROMOTE_OF = {"歩": "と", "香": "成香", "桂": "成桂", "銀": "成銀", "角": "馬", "飛": "龍"}
HAND_ORDER = ["飛", "角", "金", "銀", "桂", "香", "歩"]

MV = re.compile(
    r"^(?:(?P<to>[１-９][一二三四五六七八九])|同[\s　]*)"
    r"(?P<pc>成香|成桂|成銀|[歩香桂銀金角飛玉王と馬龍竜])"
    r"(?P<pro>不成|成)?(?P<mod>[右左直上引寄行]*)(?P<drop>打)?"
    r"(?:\((?P<fr>\d\d)\))?$"
)


def norm_piece(p: str) -> str:
    return "玉" if p == "王" else ("龍" if p == "竜" else p)


def base_piece(p: str) -> str:
    return PROMOTED_BASE.get(p, p)


@dataclass
class Position:
    n: int  # 何手目を指した後の局面か(0=開始局面)
    board: dict = field(default_factory=dict)  # (file, rank) -> (side, piece)
    hands: dict = field(default_factory=lambda: {"b": {}, "w": {}})
    last_from: Optional[tuple] = None
    last_to: Optional[tuple] = None

    def piece_count(self) -> int:
        return len(self.board) + sum(sum(h.values()) for h in self.hands.values())

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "pieces": [
                {"file": f, "rank": r, "side": s, "piece": p}
                for (f, r), (s, p) in sorted(self.board.items())
            ],
            "hands": {s: dict(h) for s, h in self.hands.items()},
            "lastTo": list(self.last_to) if self.last_to else None,
            "lastFrom": list(self.last_from) if self.last_from else None,
        }

    def copy(self, n: int) -> "Position":
        return Position(n=n, board=dict(self.board),
                        hands={s: dict(h) for s, h in self.hands.items()})


def initial_position() -> Position:
    b = {}
    back = ["香", "桂", "銀", "金", "玉", "金", "銀", "桂", "香"]
    for i, p in enumerate(back):
        b[(9 - i, 9)] = ("b", p)
        b[(9 - i, 1)] = ("w", p)
    b[(8, 8)] = ("b", "角"); b[(2, 8)] = ("b", "飛")
    b[(2, 2)] = ("w", "角"); b[(8, 2)] = ("w", "飛")
    for f in range(1, 10):
        b[(f, 7)] = ("b", "歩"); b[(f, 3)] = ("w", "歩")
    return Position(n=0, board=b)


def _sq(s: str) -> tuple:
    return FW.index(s[0]), KN.index(s[1])


def build_positions(moves: list[dict], meta: Optional[dict] = None) -> tuple[list[Position], list[dict]]:
    """positions[i] = i手目を指した後の局面(positions[0]は開始局面)。

    errors: [{"n","text","reason"}]。エラーがあればそこで打ち切る。
    """
    meta = meta or {}
    handicap = meta.get("手合割", "平手")
    if handicap not in ("平手", ""):
        return [], [{"n": 0, "text": "", "reason": f"手合割「{handicap}」は未対応"}]
    cur = initial_position()
    out = [cur]
    errors: list[dict] = []
    prev_to: Optional[tuple] = None
    for mv in moves:
        n, text = mv["n"], mv["text"]
        side = "b" if n % 2 == 1 else "w"
        m = MV.match(text)
        if not m:
            errors.append({"n": n, "text": text, "reason": "指し手の表記を解釈できない"})
            break
        pc = norm_piece(m["pc"])
        if m["to"]:
            to = _sq(m["to"])
        elif prev_to is not None:
            to = prev_to
        else:
            errors.append({"n": n, "text": text, "reason": "「同」の移動先が不明"})
            break
        nxt = cur.copy(n)
        if m["drop"]:
            hand = nxt.hands[side]
            if hand.get(pc, 0) <= 0:
                errors.append({"n": n, "text": text, "reason": "持ち駒にない駒を打った"})
                break
            hand[pc] -= 1
            if hand[pc] == 0:
                del hand[pc]
            nxt.board[to] = (side, pc)
            nxt.last_from = None
        else:
            if not m["fr"]:
                errors.append({"n": n, "text": text, "reason": "移動元がない"})
                break
            fr = (int(m["fr"][0]), int(m["fr"][1]))
            piece = nxt.board.get(fr)
            if piece is None or piece[0] != side:
                errors.append({"n": n, "text": text, "reason": "移動元に自分の駒がない"})
                break
            if piece[1] != pc:
                errors.append({"n": n, "text": text, "reason": f"駒が一致しない(盤上は{piece[1]})"})
                break
            tgt = nxt.board.get(to)
            if tgt is not None:
                if tgt[0] == side:
                    errors.append({"n": n, "text": text, "reason": "自分の駒を取ろうとした"})
                    break
                bp = base_piece(tgt[1])
                if bp != "玉":
                    nxt.hands[side][bp] = nxt.hands[side].get(bp, 0) + 1
            del nxt.board[fr]
            newp = PROMOTE_OF.get(pc, pc) if m["pro"] == "成" else pc
            nxt.board[to] = (side, newp)
            nxt.last_from = fr
        nxt.last_to = to
        prev_to = to
        out.append(nxt)
        cur = nxt
    return out, errors
