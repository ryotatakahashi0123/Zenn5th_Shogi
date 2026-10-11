"""消費時間の分析と評価値の急落(純粋関数)。

moves は dict のリスト: n, side, spentSec, cumSec, evalCp。
閾値は引数で受ける(既定値は infra/config.py)。
"""
from __future__ import annotations

from statistics import mean, median
from typing import Optional

PHASE_BOUNDS = (30, 70)  # 序盤/中盤/終盤の区切り(仮置き。08のP-5)


def add_remaining(moves: list[dict], limit_sec: Optional[int]) -> list[dict]:
    """各手に remainingSec(その手を指した後の自分の残り時間)を足す。"""
    for m in moves:
        if limit_sec is not None and m.get("cumSec") is not None:
            m["remainingSec"] = limit_sec - m["cumSec"]
        else:
            m["remainingSec"] = None
    return moves


def time_stats(moves: list[dict]) -> dict:
    xs = [m["spentSec"] for m in moves if m.get("spentSec") is not None]
    if not xs:
        return {"count": 0, "median": None, "mean": None, "max": None}
    return {"count": len(xs), "median": median(xs), "mean": round(mean(xs), 1), "max": max(xs)}


def long_threshold(moves: list[dict], long_min: int = 30, ratio: float = 4) -> Optional[float]:
    st = time_stats(moves)
    if st["median"] is None:
        return None
    return max(long_min, ratio * st["median"])


def long_thinks(moves: list[dict], long_min: int = 30, ratio: float = 4) -> list[dict]:
    """長考の手。spent >= max(long_min, ratio × 中央値)。閾値ちょうども含む。"""
    th = long_threshold(moves, long_min, ratio)
    if th is None:
        return []
    return [
        {"n": m["n"], "side": m["side"], "spentSec": m["spentSec"]}
        for m in moves
        if m.get("spentSec") is not None and m["spentSec"] >= th
    ]


def first_low_remaining(moves: list[dict], side: str, low_sec: int = 120) -> Optional[int]:
    """その側の残り時間が low_sec 未満になった最初の手の手数。なければ None。"""
    for m in moves:
        if m["side"] == side and m.get("remainingSec") is not None and m["remainingSec"] < low_sec:
            return m["n"]
    return None


def remaining_end(moves: list[dict], side: str) -> Optional[int]:
    xs = [m for m in moves if m["side"] == side and m.get("remainingSec") is not None]
    return xs[-1]["remainingSec"] if xs else None


def total_sec(moves: list[dict], side: str) -> int:
    return sum(m["spentSec"] for m in moves if m["side"] == side and m.get("spentSec") is not None)


def phase_share(moves: list[dict], side: str, bounds: tuple[int, int] = PHASE_BOUNDS) -> list[int]:
    """序盤/中盤/終盤の消費時間の割合(%)。合計が0なら [0,0,0]。丸めで100にならないことがある。"""
    lo, hi = bounds
    parts = [0, 0, 0]
    for m in moves:
        if m["side"] != side or m.get("spentSec") is None:
            continue
        idx = 0 if m["n"] <= lo else (1 if m["n"] <= hi else 2)
        parts[idx] += m["spentSec"]
    tot = sum(parts)
    if tot == 0:
        return [0, 0, 0]
    return [round(100 * p / tot) for p in parts]


def eval_swings(moves: list[dict], swing_cp: int = 300, sente_perspective: bool = True) -> list[dict]:
    """評価値の急落の候補(評価値入りの場合のみ)。

    評価値の向き(先手視点か手番視点か)は未確認(U-2)。既定は先手視点と仮定。
    n手目のコメントの評価値 = n手目を指した後の局面の評価。
    """
    by_n = {m["n"]: m for m in moves}
    out = []
    for m in moves:
        n = m["n"]
        prev = by_n.get(n - 1)
        if m.get("evalCp") is None or prev is None or prev.get("evalCp") is None:
            continue
        before, after = prev["evalCp"], m["evalCp"]
        if not sente_perspective:  # 手番視点: 次の手番側から見た値 → 先手視点へ
            before = before if prev["side"] == "b" else -before
            after = after if m["side"] == "b" else -after
        delta = (after - before) if m["side"] == "b" else (before - after)
        if delta <= -swing_cp:
            out.append({"n": n, "side": m["side"], "deltaCp": delta})
    return out


def analyze(moves: list[dict], limit_sec: Optional[int], *, long_min: int = 30,
            ratio: float = 4, low_sec: int = 120, swing_cp: int = 300,
            sente_perspective: bool = True) -> dict:
    add_remaining(moves, limit_sec)
    eval_available = any(m.get("evalCp") is not None for m in moves)
    return {
        "timeStats": time_stats(moves),
        "longThreshold": long_threshold(moves, long_min, ratio),
        "longThinks": long_thinks(moves, long_min, ratio),
        "lowRemaining": {s: first_low_remaining(moves, s, low_sec) for s in ("b", "w")},
        "remainingEnd": {s: remaining_end(moves, s) for s in ("b", "w")},
        "totalSec": {s: total_sec(moves, s) for s in ("b", "w")},
        "phaseShare": {s: phase_share(moves, s) for s in ("b", "w")},
        "limitSec": limit_sec,
        "lowSec": low_sec,
        "evalAvailable": eval_available,
        "swings": eval_swings(moves, swing_cp, sente_perspective) if eval_available else [],
    }
