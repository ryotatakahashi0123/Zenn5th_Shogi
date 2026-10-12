"""エージェントが呼べる関数(許可リスト)。04の7.2。

ネットワーク取得系は持たせない。呼び出しは ToolBox.call 経由に限り、許可リスト外・回数超過は例外にする。
各呼び出しは steps に記録される(実行記録の画面に出る)。
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from ..domain.board import Position
from ..domain.compare import compare
from ..domain.problems import make_problem
from ..infra.config import Settings


class ToolNotAllowed(Exception):
    pass


class ToolLimitExceeded(Exception):
    pass


def build_candidates(game: dict, cfg: Settings) -> list[dict]:
    """決まった規則の候補(長考、残り時間が少ない局面、評価値入りなら急落)。自分の手を先に並べる。"""
    d = game["derived"]
    my = game["mySide"]
    by_n = {m["n"]: m for m in game["moves"]}
    cands: dict[int, dict] = {}

    def entry(n: int) -> dict:
        m = by_n[n]
        return cands.setdefault(n, {
            "n": n, "side": m["side"], "mine": m["side"] == my, "spentSec": m.get("spentSec"),
            "remainingSec": m.get("remainingSec"), "kinds": [], "parts": [],
        })

    th = d.get("longThreshold")
    for x in d["longThinks"]:
        e = entry(x["n"])
        e["kinds"].append("long")
        e["parts"].append(f"{x['n']}手目で{x['spentSec']}秒を使った手(長考の目安は{int(th)}秒以上)")
    for side, n in d["lowRemaining"].items():
        if n is not None and side == my:
            e = entry(n)
            e["kinds"].append("low_remaining")
            e["parts"].append(f"{n}手目で自分の残り時間が{cfg.low_remaining_sec}秒を切った(残り{e['remainingSec']}秒)")
    for x in d.get("swings", []):
        e = entry(x["n"])
        e["kinds"].append("swing")
        e["deltaCp"] = abs(x["deltaCp"])
        e["parts"].append(f"{x['n']}手目で評価値が{abs(x['deltaCp'])}下がった(参考値)")
    out = []
    for e in cands.values():
        e["detail"] = "。".join(e.pop("parts"))
        out.append(e)
    out.sort(key=lambda e: (not e["mine"], -(e["spentSec"] or 0), e["n"]))
    return out


def candidate_numbers(game: dict, cands: list[dict], cfg: Settings) -> set[int]:
    """LLMの文中に出てよい数字(与えたデータ由来のものだけ)。"""
    nums = {cfg.low_remaining_sec, cfg.long_min_sec, cfg.max_candidates}
    th = game["derived"].get("longThreshold")
    if th is not None:
        nums.add(int(th))
    for c in cands:
        for k in ("n", "spentSec", "remainingSec", "deltaCp"):
            if c.get(k) is not None:
                nums.add(int(c[k]))
    return nums


def _brief(x: Any, limit: int = 140) -> str:
    if isinstance(x, (list, tuple)):
        return f"{len(x)}件"
    s = json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[: limit - 1] + "…"


class ToolBox:
    ALLOWED = ("get_game_summary", "get_time_outliers", "get_eval_swings", "get_position",
               "compare_feeling_vs_data", "search_knowledge_cards", "add_problem")

    def __init__(self, game: dict, marks: list[dict], cards: list[dict],
                 positions: list[Position], cfg: Settings) -> None:
        self.game, self.marks, self.cards = game, marks, cards
        self.positions, self.cfg = positions, cfg
        self.steps: list[dict] = []
        self.calls = 0
        self.problems: list[dict] = []

    def call(self, name: str, **args: Any) -> Any:
        if name not in self.ALLOWED:
            raise ToolNotAllowed(name)
        if self.calls >= self.cfg.max_tool_calls:
            raise ToolLimitExceeded(f"ツール呼び出しの上限 {self.cfg.max_tool_calls}")
        self.calls += 1
        t0 = time.perf_counter()
        res = getattr(self, "_t_" + name)(**args)
        self.steps.append({
            "tool": name,
            "args": _brief(args, 80) if args else "",
            "resultSummary": _brief(res),
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        })
        return res

    # ---- 読取 ----
    def _t_get_game_summary(self) -> dict:
        g, d = self.game, self.game["derived"]
        return {
            "totalMoves": len(g["moves"]), "result": g.get("result"), "mySide": g["mySide"],
            "evalAvailable": d["evalAvailable"], "limitSec": d["limitSec"],
            "totalSec": d["totalSec"], "remainingEnd": d["remainingEnd"],
        }

    def _t_get_time_outliers(self) -> dict:
        d = self.game["derived"]
        return {"threshold": d["longThreshold"], "longThinks": d["longThinks"], "lowRemaining": d["lowRemaining"]}

    def _t_get_eval_swings(self) -> dict:
        d = self.game["derived"]
        return {"available": d["evalAvailable"], "swings": d["swings"]}

    def _t_get_position(self, n: int) -> dict:
        if not 0 <= n < len(self.positions):
            raise ValueError("手番号が範囲外")
        return self.positions[n].to_dict()

    def _t_compare_feeling_vs_data(self) -> dict:
        g, d = self.game, self.game["derived"]
        info = {m["n"]: {"side": m["side"], "spentSec": m.get("spentSec"),
                         "remainingSec": m.get("remainingSec")} for m in g["moves"]}
        return compare(self.marks, info, [x["n"] for x in d["longThinks"]],
                       [x["n"] for x in d["swings"]], g["mySide"],
                       quick_sec=self.cfg.quick_sec, low_sec=self.cfg.low_remaining_sec)

    def _t_search_knowledge_cards(self, tags: list) -> list:
        want = set(tags)
        hits = [c for c in self.cards
                if want & set(c.get("themes", [])) and c.get("url") and c.get("proName") and c.get("videoTitle")]
        return hits[:2]

    # ---- 書込(実行記録に残る) ----
    def _t_add_problem(self, n: int, origin: str) -> dict:
        if len(self.problems) >= self.cfg.max_problems:
            return {"added": False, "reason": "問題数の上限"}
        if any(p["n"] == n for p in self.problems):
            return {"added": False, "reason": "重複"}
        p = make_problem(self.game["moves"], self.positions, n, origin, self.game["mySide"])
        if p is None:
            return {"added": False, "reason": "範囲外または局面を再現できない"}
        self.problems.append(p)
        return {"added": True, "n": n}
