"""本人が「悔しかった」「好手」と印を付けた手と、エンジンの判定を突き合わせる。

印（どの手が悔しかったか・原因、どの手が好手か）は本人の感覚で、エンジンやAIが代わりに付けない。
ここでは決まった規則だけで3つに分ける（数値はすべて全手解析の行から取り、新しく作らない）。

悔しかった手:
    一致     … 印を付けた手が、エンジンでも悪手・疑問手・緩手
    感覚だけ … 印を付けたが、エンジンでは勝率がほとんど下がっていない
    データだけ … エンジンでは自分の悪手候補だが、悔しかった手の印を付けていない

好手（結果のキーは good_ で始まる）:
    一致     … 好手の印を付けた手が、エンジンの最善手と同じ
    感覚だけ … 好手の印を付けたが、エンジンの最善手とは違う
    データだけ … エンジンでは自分の好手候補（最善手を指し、次善手なら勝率が大きく下がっていた）だが、好手の印を付けていない
    相手の好手候補（good_opponent）… エンジンでは相手の好手候補だが、好手の印を付けていない
"""
from __future__ import annotations

# 原因の選択肢（複数選べる）。将棋として妥当かは発案者が確認する（仕様書 05 の U-13）
CAUSE_TAGS = ["読み抜け", "形勢判断の誤り", "焦り・早指し", "時間不足", "定跡・形を知らなかった",
              "詰みの見落とし", "相手の好手に気づかなかった", "欲張り", "手拍子", "その他"]
MARK_KINDS = {"regret": "悔しかった手", "good": "好手"}  # 印の種類。好手には原因を付けない
MAX_MARKS = 10    # 1局に付けられる印の数（種類ごと）
MAX_NOTE = 200    # コメントの文字数

SIDES = {"先手": "▲", "後手": "△"}


def clean_mark(ply: int, tags: list[str], note: str, plies: int, kind: str = "regret") -> dict:
    """入力を検査して印を作る。範囲外の手数・一覧に無い原因・種類は受け付けない。好手の印は原因を持たない。"""
    if not 1 <= int(ply) <= plies:
        raise ValueError(f"手数は1〜{plies}で指定してください")
    if kind not in MARK_KINDS:
        raise ValueError(f"印の種類にありません: {kind}")
    tags = [] if kind == "good" else list(tags)
    bad = [t for t in tags if t not in CAUSE_TAGS]
    if bad:
        raise ValueError(f"原因の選択肢にありません: {', '.join(bad)}")
    if kind == "regret" and not tags:
        raise ValueError("原因を1つ以上選んでください")
    return {"ply": int(ply), "kind": kind, "tags": tags, "note": " ".join((note or "").split())[:MAX_NOTE]}


def compare(rows: list[dict], marks: list[dict], my_side: str) -> dict:
    """rows: 全手解析の行。marks: clean_mark の結果。my_side: "▲" か "△"（本人の手番）。"""
    by_ply = {r["ply"]: r for r in rows}
    marked = {m["ply"]: m for m in marks if m.get("kind", "regret") == "regret"}
    good = {m["ply"]: m for m in marks if m.get("kind") == "good"}
    out = {"my_side": my_side, "match": [], "feeling_only": [], "data_only": [],
           "good_match": [], "good_feeling_only": []}
    for ply in sorted(marked):
        r = by_ply.get(ply)
        if r is None:  # 解析の行が無い手（終局後など）は比べられない
            continue
        item = {**_engine(r), "tags": marked[ply]["tags"], "note": marked[ply]["note"]}
        out["match" if r["label"] else "feeling_only"].append(item)
    out["data_only"] = [_engine(r) for r in rows
                        if r["label"] and r["side"] == my_side and r["ply"] not in marked]
    for ply in sorted(good):
        r = by_ply.get(ply)
        if r is None:
            continue
        item = {**_engine(r), "note": good[ply]["note"]}
        out["good_match" if item["was_best"] else "good_feeling_only"].append(item)
    cands = [_engine(r) for r in rows if r.get("good")]
    out["good_data_only"] = [m for m in cands if m["side"] == my_side and m["ply"] not in good]
    out["good_opponent"] = [m for m in cands if m["side"] != my_side and m["ply"] not in good]
    return out


def _engine(r: dict) -> dict:
    return {"ply": r["ply"], "side": r["side"], "move_jp": r["move_jp"], "label": r["label"],
            "winrate_drop": r["winrate_drop"], "best_before_jp": r["best_before_jp"],
            "was_best": r["move_usi"] == r["best_before_usi"],
            "good": bool(r.get("good")), "good_gap": r.get("good_gap", 0.0)}


def verdict(m: dict) -> str:
    """エンジンの判定の表示（全手解析の行でも、compare の項目でも使える）。"""
    if m.get("good"):
        return "好手候補"
    if m["label"]:
        return m["label"]
    was_best = m["was_best"] if "was_best" in m else m["move_usi"] == m["best_before_usi"]
    return "最善手と同じ" if was_best else "判定なし"
