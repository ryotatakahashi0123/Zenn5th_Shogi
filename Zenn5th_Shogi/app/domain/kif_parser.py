"""KIFの構文解析(純粋関数)。

確認済みの書式: 将棋ウォーズの書き出し2局(docs/decisions.md D-2)。
未確認の書式: ぴよ将棋などの解析入りKIF(評価値のコメント行)。EVAL_RE は仮の形。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# 例: "   1 ７六歩(77)   ( 0:05/00:00:05)" / "9 ８七歩打   ( 0:00/00:00:03)" / "101 投了"
MOVE_RE = re.compile(
    r"^\s*(?P<n>\d+)\s+(?P<text>\S+?)\s*"
    r"(?:\(\s*(?P<m>\d+):(?P<s>\d+)\s*/\s*(?P<ch>\d+):(?P<cm>\d+):(?P<cs>\d+)\s*\))?\s*$"
)
# 終局語。サンプルで確認できたのは「投了」のみ。他は未確認(実物で確かめる)
END_WORDS = {
    "投了", "詰み", "中断", "千日手", "持将棋", "切れ負け",
    "反則勝ち", "反則負け", "入玉勝ち", "不戦勝", "不戦敗",
}
# 手番側が負ける終局語(n手目の手番の側が負け)
LOSER_IS_MOVER = {"投了", "詰み", "切れ負け", "反則負け", "不戦敗"}
# 評価値コメントの書式は未確認(U-21)。「評価値 123」形式を仮定
EVAL_RE = re.compile(r"評価値\s*(?P<v>[+-]?\d+)")
TIME_LIMIT_RE = re.compile(r"(?P<min>\d+)分切れ負け")

SIDE_NAMES = {"b": "先手", "w": "後手"}


class KifError(ValueError):
    """解析できない入力。"""


@dataclass
class Move:
    n: int
    text: str
    side: str  # "b"=先手 / "w"=後手(手数の偶奇から決める)
    spent_sec: Optional[int] = None
    cum_sec: Optional[int] = None
    eval_cp: Optional[int] = None
    comment: str = ""

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "text": self.text,
            "side": self.side,
            "spentSec": self.spent_sec,
            "cumSec": self.cum_sec,
            "evalCp": self.eval_cp,
            "evalMate": None,
            "comment": self.comment,
        }


@dataclass
class Game:
    meta: dict = field(default_factory=dict)
    moves: list[Move] = field(default_factory=list)
    result: Optional[dict] = None  # {"n","word","loser","winner"}
    time_limit_sec: Optional[int] = None
    time_rule: Optional[str] = None  # "sudden_death" | None
    warnings: list[str] = field(default_factory=list)


def decode_kif_bytes(raw: bytes) -> str:
    """UTF-8(BOM可)を試し、だめならShift_JIS(cp932)。"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise KifError("文字コードを判別できない(UTF-8 / Shift_JIS 以外)")


def parse_kif(text: str) -> Game:
    """KIFテキストを解析する。手が1つも取れなければ KifError。"""
    game = Game()
    if text is None:
        raise KifError("入力が空")
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise KifError("入力が空")

    in_moves = False
    for lineno, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        s = line.strip()
        if s.startswith("#"):
            continue
        if s.startswith("変化："):  # 分岐は初版では無視(メインルートのみ)
            game.warnings.append(f"{lineno}行目: 変化(分岐)以降は無視した")
            break
        if s.startswith("*"):
            if game.moves:
                mv = game.moves[-1]
                mv.comment += s[1:].strip() + "\n"
                m = EVAL_RE.search(s)
                if m:
                    mv.eval_cp = int(m.group("v"))
            continue
        if s.startswith("手数-") or s.startswith("手数－"):
            in_moves = True
            continue
        if not in_moves and not s[0].isdigit() and "：" in s:
            k, v = s.split("：", 1)
            game.meta[k.strip()] = v.strip()
            continue
        m = MOVE_RE.match(line)
        if not m:
            game.warnings.append(f"{lineno}行目: 解析できない行を読み飛ばした")
            continue
        in_moves = True
        n = int(m.group("n"))
        word = m.group("text")
        if word in END_WORDS:
            loser = ("b" if n % 2 == 1 else "w") if word in LOSER_IS_MOVER else None
            winner = None if loser is None else ("w" if loser == "b" else "b")
            if loser is None:
                game.warnings.append(f"終局語「{word}」の勝敗は判定していない")
            game.result = {"n": n, "word": word, "loser": loser, "winner": winner}
            continue
        spent = cum = None
        if m.group("m") is not None:
            spent = int(m.group("m")) * 60 + int(m.group("s"))
            cum = int(m.group("ch")) * 3600 + int(m.group("cm")) * 60 + int(m.group("cs"))
        game.moves.append(Move(n=n, text=word, side="b" if n % 2 == 1 else "w",
                               spent_sec=spent, cum_sec=cum))

    if not game.moves:
        raise KifError("指し手が1つも取れなかった")

    for i, mv in enumerate(game.moves, start=1):
        if mv.n != i:
            game.warnings.append(f"手数が連続していない({i}番目の手が{mv.n}手目)")
            break

    tl = game.meta.get("持ち時間", "")
    t = TIME_LIMIT_RE.search(tl)
    if t:
        game.time_limit_sec = int(t.group("min")) * 60
        game.time_rule = "sudden_death"
    elif tl:
        game.warnings.append(f"持ち時間「{tl}」は未対応(残り時間を出さない)")
    return game
