"""「考える問題」の組み立て(純粋関数)。

評価値なしの局では最善手を出さない。解答編には実戦の手と次の相手の手だけを載せ、
実戦の手の良し悪しは書かない(docs/decisions.md D-4)。
"""
from __future__ import annotations

from typing import Optional

from .board import Position


def build_thinking_problems(moves: list[dict], positions: list[Position], marks: list[dict],
                            candidate_ns: list[int], my_side: str, max_problems: int = 5) -> list[dict]:
    """優先順: 本人の「悔しい」印 → 本人の「迷った」印 → 候補(長考など)。

    positions[n-1] が n手目を指す直前の局面。
    """
    order: list[tuple[int, str]] = []
    seen: set[int] = set()
    for feeling in ("regret", "unsure"):
        for mk in sorted(marks, key=lambda x: x["moveNo"]):
            if mk.get("feeling") == feeling and mk["moveNo"] not in seen:
                order.append((mk["moveNo"], "user_mark"))
                seen.add(mk["moveNo"])
    for n in candidate_ns:
        if n not in seen:
            order.append((n, "candidate"))
            seen.add(n)

    problems: list[dict] = []
    for n, origin in order:
        if len(problems) >= max_problems:
            break
        pr = make_problem(moves, positions, n, origin, my_side)
        if pr is not None:
            problems.append(pr)
    return problems


def make_problem(moves: list[dict], positions: list[Position], n: int, origin: str,
                 my_side: str) -> Optional[dict]:
    """n手目の直前の局面から「考える問題」を1問作る。範囲外なら None。"""
    by_n = {m["n"]: m for m in moves}
    mv = by_n.get(n)
    if mv is None or n < 1 or n - 1 >= len(positions):
        return None
    nxt = by_n.get(n + 1)
    return {
        "n": n,
        "origin": origin,  # user_mark | candidate(本人の印ではない)
        "toMove": mv["side"],
        "isMine": mv["side"] == my_side,
        "position": positions[n - 1].to_dict(),
        "question": "この局面で、あなたならどう指すか",
        "played": mv["text"],
        "playedSpentSec": mv.get("spentSec"),
        "nextMove": nxt["text"] if nxt else None,
        "bestMove": None,  # 最善手は未取得(評価値・読み筋がないため)
        "bestMoveNote": "最善手は未取得",
    }
