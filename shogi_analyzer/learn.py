"""保存した対局から、本人の傾向を集計する（純粋関数。エンジンもLLMも呼ばない）。

1局ごとの集計（summarize）を保存しておき、それらを足し合わせる（aggregate）。
数は、本人の印と全手解析の行から決まった規則で数えるだけで、新しい数値を作らない。コメントの文は入れない。
"""
from __future__ import annotations

from collections import Counter

PHASES = ["序盤", "中盤", "終盤"]  # その対局の手数を3等分した前・中・後（仮の区切り。発案者の確認待ち）
KINDS = ["match", "feeling_only", "data_only", "good_match", "good_feeling_only", "good_data_only"]


def phase(ply: int, plies: int) -> str:
    return PHASES[min(2, (ply - 1) * 3 // max(plies, 1))]


def summarize(rows: list[dict], gap: dict, created: str = "") -> dict:
    """1局ぶんの集計。rows: 全手解析の行。gap: regret.compare の結果。"""
    plies = len(rows)
    noticed = {m["ply"] for m in gap["match"]}  # 悔しかった手の印が、エンジンの悪手候補と重なった手
    tags = Counter(t for k in ("match", "feeling_only") for m in gap[k] for t in m.get("tags", []))
    return {
        "created": created, "plies": plies, "my_side": gap["my_side"],
        "counts": {k: len(gap.get(k, [])) for k in KINDS},
        "tags": dict(tags),
        # 自分の悪手候補（印の有無つき）
        "bad": [{"ply": r["ply"], "label": r["label"], "phase": phase(r["ply"], plies), "noticed": r["ply"] in noticed}
                for r in rows if r["label"] and r["side"] == gap["my_side"]],
        "good": sum(1 for r in rows if r.get("good") and r["side"] == gap["my_side"]),
    }


def summarize_marks(marks: list[dict], plies: int, my_side: str, created: str = "") -> dict:
    """エンジンで解析しなかった対局（思考時間 0）の集計。数えられるのは本人が選んだ原因だけ。"""
    tags = Counter(t for m in marks if m.get("kind", "regret") == "regret" for t in m.get("tags", []))
    return {"created": created, "plies": plies, "my_side": my_side, "analyzed": False,
            "counts": {k: 0 for k in KINDS}, "tags": dict(tags), "bad": [], "good": 0}


def aggregate(summaries: list[dict]) -> dict:
    """保存した対局の集計を足し合わせる。games が 0 なら記録なし。印の入力がまだの対局（pending）は数えない。"""
    summaries = [s for s in summaries if not s.get("pending")]
    bad = [b for s in summaries for b in s.get("bad", [])]
    tags = Counter()
    for s in summaries:
        tags.update(s.get("tags", {}))
    return {
        "games": len(summaries),
        "games_analyzed": sum(1 for s in summaries if s.get("analyzed", True)),  # エンジンで解析した対局（悪手候補などの数はこの分だけ）
        "counts": {k: sum(s.get("counts", {}).get(k, 0) for s in summaries) for k in KINDS},
        "tags": [{"tag": t, "count": n} for t, n in sorted(tags.items(), key=lambda x: (-x[1], x[0]))],
        "bad_moves": len(bad),
        "bad_by_phase": {p: sum(1 for b in bad if b["phase"] == p) for p in PHASES},
        "bad_by_label": {lab: sum(1 for b in bad if b["label"] == lab) for lab in ("悪手", "疑問手", "緩手")},
        "bad_noticed": sum(1 for b in bad if b["noticed"]),  # 自分の悪手候補のうち、悔しかった手の印を付けていた数
        "good_moves": sum(s.get("good", 0) for s in summaries),
    }
