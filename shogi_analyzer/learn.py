"""保存した対局から、本人の傾向を集計する（純粋関数。エンジンもLLMも呼ばない）。

1局ごとの集計（summarize）を保存しておき、それらを足し合わせる（aggregate）。
数は、本人の印と全手解析の行から決まった規則で数えるだけで、新しい数値を作らない。コメントの文は入れない。
"""
from __future__ import annotations

from collections import Counter

from .kifu_analysis import Kifu
from .regret import GROUP_KEYS, MARKED_KEYS, norm_kind

PHASES = ["序盤", "中盤", "終盤"]  # その対局の手数を3等分した前・中・後（仮の区切り。発案者の確認待ち）
KINDS = GROUP_KEYS  # 6つのラベルの区分（regret.SECTIONS）
# 傾向まとめに載せる局面例の種類（発案者の指示 2026-10-14）。並びは載せる順
EXAMPLE_KINDS = {"match": "悪手だと思った自分の手（エンジンでも悪手候補）", "data_only": "気づかなかった自分の悪手候補",
                 "surprise": "予想外で驚いた相手の手", "alt": "迷った別の読みのほうが良かった手"}
MAX_EXAMPLES_PER_GAME = 2  # 1局から残す局面例の数（種類ごと）
MAX_EXAMPLES = 4           # 傾向まとめに載せる局面例の数（3ページに収めるため）


def phase(ply: int, plies: int) -> str:
    return PHASES[min(2, (ply - 1) * 3 // max(plies, 1))]


def summarize(rows: list[dict], gap: dict, created: str = "", kifu: Kifu | None = None) -> dict:
    """1局ぶんの集計。rows: 全手解析の行。gap: regret.compare の結果。kifu があれば局面例（examples）も残す。"""
    plies = len(rows)
    alts = [a for k in MARKED_KEYS for m in gap.get(k, []) for a in m.get("alts", []) if "verdict" in a]
    noticed = {m["ply"] for m in gap["match"]}  # 悪手だと思った自分の手の印が、エンジンの悪手候補と重なった手
    tags = Counter(t for k in ("match", "feeling_only") for m in gap[k] for t in m.get("tags", []))
    return {
        "created": created, "plies": plies, "my_side": gap["my_side"],
        "counts": {k: len(gap.get(k, [])) for k in KINDS},
        "tags": dict(tags),
        # 自分の悪手候補（印の有無つき）
        "bad": [{"ply": r["ply"], "label": r["label"], "phase": phase(r["ply"], plies), "noticed": r["ply"] in noticed}
                for r in rows if r["label"] and r["side"] == gap["my_side"]],
        "good": sum(1 for r in rows if r.get("good") and r["side"] == gap["my_side"]),
        # 悪手だと思った自分の手の印が、どの段階に付いたか
        "regret_by_phase": dict(Counter(phase(m["ply"], plies) for k in ("match", "feeling_only") for m in gap[k])),
        # 予想外で驚いた相手の手が、エンジンではどうだったか（最善手と同じ / 悪手候補 / どちらでもない）
        "surprise": {"best": sum(1 for m in gap.get("surprise", []) if m["was_best"]),
                     "bad": sum(1 for m in gap.get("surprise", []) if m["label"]),
                     "total": len(gap.get("surprise", []))},
        # 迷った別の読みが、実戦の手と比べて 良い / 同じくらい / 悪い（alts.summarize の verdict）
        "alts": {v: sum(1 for a in alts if a["verdict"] == v) for v in ("良い", "同じくらい", "悪い")},
        "examples": examples(kifu, rows, gap, created) if kifu else [],
    }


def examples(kifu: Kifu, rows: list[dict], gap: dict, created: str = "") -> list[dict]:
    """傾向まとめの局面例の候補（決まった規則で選ぶ。AIは選ばない）。

    種類ごとに、勝率の下落（迷った読みは実戦の手との差）が大きい順に MAX_EXAMPLES_PER_GAME 個。
    局面は、その手を指す直前の SFEN で持つ（対局者名・コメントは入れない）。
    """
    from .service import board_after  # service は cshogi を読むので、ここで読む
    plies = len(rows)

    def ex(kind, m, score, **more):
        return {"kind": kind, "ply": m["ply"], "side": m["side"], "move_jp": m["move_jp"],
                "best_jp": m["best_before_jp"], "label": m["label"], "drop": m["winrate_drop"],
                "was_best": m["was_best"], "tags": list(m.get("tags", [])), "phase": phase(m["ply"], plies),
                "created": created, "sfen": board_after(kifu, m["ply"] - 1)[0].sfen(), "score": round(score, 3), **more}
    out = []
    for kind in ("match", "data_only"):
        out += sorted((ex(kind, m, m["winrate_drop"]) for m in gap.get(kind, [])),
                      key=lambda e: -e["score"])[:MAX_EXAMPLES_PER_GAME]
    out += sorted((ex("surprise", m, m["winrate_drop"] if m["label"] else 0.0) for m in gap.get("surprise", [])),
                  key=lambda e: -e["score"])[:MAX_EXAMPLES_PER_GAME]
    better = [(m, a) for k in MARKED_KEYS for m in gap.get(k, []) for a in m.get("alts", []) if a.get("verdict") == "良い"]
    out += sorted((ex("alt", m, a["diff"], alt={"line_jp": a["line_jp"], "final_winrate": a["final_winrate"],
                                               "actual_winrate": a["actual_winrate"], "diff": a["diff"]})
                   for m, a in better), key=lambda e: -e["score"])[:MAX_EXAMPLES_PER_GAME]
    return out


def pick_examples(summaries: list[dict], n: int = MAX_EXAMPLES) -> list[dict]:
    """保存した対局の局面例から n 個を選ぶ。まず種類ごとに1つ（いちばん点の大きいもの）、残りは点の大きい順。"""
    pool = sorted((e for s in summaries if not s.get("pending") for e in s.get("examples", [])),
                  key=lambda e: (-e["score"], e.get("created", ""), e["ply"]))
    first = [next(e for e in pool if e["kind"] == k) for k in EXAMPLE_KINDS if any(e["kind"] == k for e in pool)]
    rest = [e for e in pool if not any(e is f for f in first)]
    return sorted((first + rest)[:n], key=lambda e: list(EXAMPLE_KINDS).index(e["kind"]))


def summarize_marks(marks: list[dict], plies: int, my_side: str, created: str = "", sides: dict | None = None) -> dict:
    """エンジンで解析しなかった対局（思考時間 0）の集計。数えられるのは本人が選んだ原因だけ。

    原因は、悪手だと思った自分の手のものを数える（解析ありの対局と同じ）。sides: 手数 → "▲"/"△"（無ければ手数の偶奇）。
    """
    def side(p):
        return (sides or {}).get(p) or ("▲" if p % 2 else "△")
    tags = Counter(t for m in marks if norm_kind(m.get("kind"), side(m["ply"]) == my_side) == "bad"
                   and side(m["ply"]) == my_side for t in m.get("tags", []))
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
        "bad_noticed": sum(1 for b in bad if b["noticed"]),  # 自分の悪手候補のうち、悪手だと思った印を付けていた数
        "good_moves": sum(s.get("good", 0) for s in summaries),
        "regret_by_phase": {p: sum(s.get("regret_by_phase", {}).get(p, 0) for s in summaries) for p in PHASES},
        "surprise": {k: sum(s.get("surprise", {}).get(k, 0) for s in summaries) for k in ("total", "best", "bad")},
        "alts": {v: sum(s.get("alts", {}).get(v, 0) for s in summaries) for v in ("良い", "同じくらい", "悪い")},
    }
