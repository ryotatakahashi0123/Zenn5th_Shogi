"""感覚(本人の印)とデータの突き合わせ(純粋関数)。

評価値なしの場合の読み替えは docs/decisions.md D-3(08の5.2)。
- 一致: 印のある手が、データ上の候補(長考、評価値入りなら急落)でもある
- 感覚だけ: 印はあるが、データ上は目立たない
- データだけ: 自分の手のデータ上の候補だが、本人が印を付けていない
- 時間の癖: 時間に関する原因タグ(焦り・時間不足)と、時間のデータ(早指し・残り少・長考)の組合せ
"""
from __future__ import annotations

from typing import Iterable, Optional

TIME_TAGS = {"時間不足", "焦り・早指し"}


def compare(marks: Iterable[dict], move_info: dict[int, dict], long_ns: Iterable[int],
            swing_ns: Iterable[int], my_side: str, *, quick_sec: int = 3,
            low_sec: int = 120) -> dict:
    """marks: [{"moveNo","feeling","causeTags"}]。feeling が "ok" の印は対象外。

    move_info: {n: {"side","spentSec","remainingSec"}}
    """
    long_set = set(long_ns)
    swing_set = set(swing_ns)
    data_set = long_set | swing_set
    active = [m for m in marks if m.get("feeling") in ("regret", "unsure")]
    marked = {m["moveNo"] for m in active}

    coincide, feeling_only, time_habit = [], [], []
    for m in sorted(active, key=lambda x: x["moveNo"]):
        n = m["moveNo"]
        entry = {"n": n, "feeling": m["feeling"], "causeTags": list(m.get("causeTags", []))}
        if n in data_set:
            kinds = [k for k, s in (("長考", long_set), ("評価値の急落", swing_set)) if n in s]
            coincide.append({**entry, "dataKinds": kinds})
        else:
            feeling_only.append(entry)
        info = move_info.get(n, {})
        tags = set(entry["causeTags"])
        if tags & TIME_TAGS:
            reasons = []
            if info.get("spentSec") is not None and info["spentSec"] <= quick_sec:
                reasons.append(f"消費{info['spentSec']}秒の早指し")
            if info.get("remainingSec") is not None and info["remainingSec"] < low_sec:
                reasons.append(f"残り{info['remainingSec']}秒")
            if n in long_set:
                reasons.append("長考")
            if reasons:
                time_habit.append({**entry, "dataKinds": reasons})

    data_only = []
    for n in sorted(data_set):
        info = move_info.get(n, {})
        if info.get("side") == my_side and n not in marked:
            kinds = [k for k, s in (("長考", long_set), ("評価値の急落", swing_set)) if n in s]
            data_only.append({"n": n, "dataKinds": kinds, "spentSec": info.get("spentSec")})

    return {
        "coincide": coincide,
        "feelingOnly": feeling_only,
        "dataOnly": data_only,
        "timeHabit": time_habit,
        "markCount": len(active),
    }
