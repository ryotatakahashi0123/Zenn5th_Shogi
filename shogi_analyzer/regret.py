"""本人が付けた印（6つのラベル）と、エンジンの判定を突き合わせる。

印は本人の感覚で、エンジンやAIが代わりに付けない。ここでは決まった規則だけで分ける（数値はすべて全手解析の行から取り、新しく作らない）。

ラベル（発案者の指示 2026-10-14）。印の種類は3つ（bad / other / good）で持ち、自分の手か相手の手かは、
比べるときの本人の手番で決める（手番を後から直しても、印は付け直さなくてよい）:

    自分の手  bad   … 悪手だと思った        → 一致 / 感覚だけ（エンジンでも悪手・疑問手・緩手か）＋ データだけ（自分の悪手候補で印なし）
              other … ほかにも指す手があった → 区分なし（最善手と同じか、迷った読みの良し悪しを並べる）
              good  … 好手だと思った        → 一致 / 感覚だけ（エンジンの最善手と同じか）＋ データだけ（自分の好手候補で印なし）
    相手の手  bad   … 悪手だと思った        → 一致 / 感覚だけ ＋ データだけ（相手の悪手候補で印なし = 咎め損ねたかもしれない手）
              other … 予想外で驚いた        → 区分なし（エンジンの判定を並べる）
              good  … 好手だと思った        → 一致 / 感覚だけ ＋ データだけ（相手の好手候補で印なし）

原因（CAUSE_TAGS）はどのラベルでも任意。
以前の印の種類 regret は、自分の手なら bad、相手の手なら other（驚いた手）として読む（保存した対局のため）。
"""
from __future__ import annotations

# 原因の選択肢（複数選べる。どのラベルでも任意）。将棋として妥当かは発案者が確認する（仕様書 05 の U-13）
CAUSE_TAGS = ["読み抜け", "形勢判断の誤り", "焦り・早指し", "時間不足", "定跡・形を知らなかった",
              "詰みの見落とし", "相手の好手に気づかなかった", "欲張り", "手拍子", "その他"]
MARK_KINDS = ["bad", "other", "good"]  # 印の種類。呼び名は自分の手か相手の手かで変わる（LABELS）
LABELS = {("mine", "bad"): "悪手だと思った", ("mine", "other"): "ほかにも指す手があった", ("mine", "good"): "好手だと思った",
          ("opp", "bad"): "悪手だと思った", ("opp", "other"): "予想外で驚いた", ("opp", "good"): "好手だと思った"}
WHO = {"mine": "自分の手", "opp": "相手の手"}
MAX_MARKS = 10    # 1局に付けられる印の数（ラベルごと）
MAX_NOTE = 200    # コメントの文字数

SIDES = {"先手": "▲", "後手": "△"}

# 6つのラベルと、その中の区分（compare の結果のキー, 区分名, 説明）。並びは画面・ノート・AIエージェントで共通
SECTIONS = [
    {"key": "my_bad", "who": "mine", "kind": "bad", "groups": [
        ("match", "一致", "悪手だと思った自分の手が、エンジンでも悪手・疑問手・緩手"),
        ("feeling_only", "感覚だけ", "悪手だと思ったが、エンジンでは勝率がほとんど下がっていない"),
        ("data_only", "データだけ", "エンジンでは自分の悪手候補だが、印を付けていない")]},
    {"key": "my_other", "who": "mine", "kind": "other", "groups": [
        ("hesitate", "", "ほかにも指す手があったと思いながら指した自分の手。エンジンの判定と、迷った読みの評価")]},
    {"key": "my_good", "who": "mine", "kind": "good", "groups": [
        ("good_match", "一致", "好手だと思った自分の手が、エンジンの最善手と同じ"),
        ("good_feeling_only", "感覚だけ", "好手だと思ったが、エンジンの最善手とは違う"),
        ("good_data_only", "データだけ", "エンジンでは自分の好手候補だが、印を付けていない")]},
    {"key": "opp_bad", "who": "opp", "kind": "bad", "groups": [
        ("opp_match", "一致", "悪手だと思った相手の手が、エンジンでも悪手・疑問手・緩手"),
        ("opp_feeling_only", "感覚だけ", "相手の悪手だと思ったが、エンジンでは勝率がほとんど下がっていない"),
        ("opp_data_only", "データだけ", "エンジンでは相手の悪手候補だが、印を付けていない（咎め損ねたかもしれない手）")]},
    {"key": "opp_other", "who": "opp", "kind": "other", "groups": [
        ("surprise", "", "予想外で驚いた相手の手。エンジンの判定")]},
    {"key": "opp_good", "who": "opp", "kind": "good", "groups": [
        ("opp_good_match", "一致", "好手だと思った相手の手が、エンジンの最善手と同じ"),
        ("opp_good_feeling_only", "感覚だけ", "相手の好手だと思ったが、エンジンの最善手とは違う"),
        ("good_opponent", "データだけ", "エンジンでは相手の好手候補だが、印を付けていない")]},
]
GROUP_KEYS = [k for s in SECTIONS for k, _, _ in s["groups"]]
MARKED_KEYS = [k for k in GROUP_KEYS if "data_only" not in k and k != "good_opponent"]  # 本人が印を付けた手の区分


def section_title(s: dict) -> str:
    """ラベルの見出し（例: 「自分の手・悪手だと思った」）。"""
    return f'{WHO[s["who"]]}・{LABELS[(s["who"], s["kind"])]}'


def norm_kind(kind: str | None, mine: bool) -> str:
    """印の種類を bad / other / good にそろえる。以前の regret は、自分の手なら bad、相手の手なら other。
    呼び名（「予想外で驚いた」など）が来たときも種類に直す（画面の欄が表示名を返すことがあるため）。"""
    if kind in (None, "regret"):
        return "bad" if mine else "other"
    if kind not in MARK_KINDS:
        named = {label: k for (_, k), label in LABELS.items()}
        if kind in named:
            return named[kind]
    return kind


def kind_label(kind: str, side: str, my_side: str) -> str:
    """印の呼び名（例: 「悪手だと思った」「予想外で驚いた」）。"""
    who = "mine" if side == my_side else "opp"
    return LABELS[(who, norm_kind(kind, who == "mine"))]


def mark_title(kind: str, side: str, my_side: str) -> str:
    """印の呼び名に、自分の手か相手の手かを付けたもの（例: 「相手の手・予想外で驚いた」）。"""
    who = "mine" if side == my_side else "opp"
    return f"{WHO[who]}・{kind_label(kind, side, my_side)}"


def clean_mark(ply: int, tags: list[str], note: str, plies: int, kind: str = "bad", alts: list[list[str]] = ()) -> dict:
    """入力を検査して印を作る。範囲外の手数・一覧に無い原因・種類は受け付けない。原因はどのラベルでも任意。

    alts: 迷った別の読み（shogi_analyzer.alts.clean_line で検査済みのもの）。
    """
    if not 1 <= int(ply) <= plies:
        raise ValueError(f"手数は1〜{plies}で指定してください")
    if kind not in MARK_KINDS:
        raise ValueError(f"印の種類にありません: {kind}")
    bad = [t for t in tags if t not in CAUSE_TAGS]
    if bad:
        raise ValueError(f"原因の選択肢にありません: {', '.join(bad)}")
    out = {"ply": int(ply), "kind": kind, "tags": list(tags), "note": " ".join((note or "").split())[:MAX_NOTE]}
    if alts:
        out["alts"] = [list(a) for a in alts]
    return out


def compare(rows: list[dict], marks: list[dict], my_side: str, alt_summaries: dict | None = None) -> dict:
    """rows: 全手解析の行。marks: clean_mark の結果。my_side: "▲" か "△"（本人の手番）。

    結果のキーは SECTIONS の区分（GROUP_KEYS）。alt_summaries: 手数 → 迷った別の読みの評価（shogi_analyzer.alts.summaries）。
    あれば印の項目の alts に入れる。解析の行が無い手（終局後など）は比べられないので入れない。
    """
    by_ply = {r["ply"]: r for r in rows}
    alt_summaries = alt_summaries or {}
    out = {"my_side": my_side, **{k: [] for k in GROUP_KEYS}}
    marked = {}  # (自分の手か, 種類) → 手数の集合
    for m in sorted(marks, key=lambda m: m["ply"]):
        r = by_ply.get(m["ply"])
        if r is None:
            continue
        mine = r["side"] == my_side
        kind = norm_kind(m.get("kind"), mine)
        marked.setdefault((mine, kind), set()).add(r["ply"])
        it = {**_engine(r), "tags": list(m.get("tags", [])), "note": m.get("note", "")}
        if alt_summaries.get(r["ply"]):
            it["alts"] = alt_summaries[r["ply"]]
        pre = "" if mine else "opp_"
        if kind == "bad":
            out[pre + ("match" if r["label"] else "feeling_only")].append(it)
        elif kind == "good":
            out[pre + ("good_match" if it["was_best"] else "good_feeling_only")].append(it)
        else:
            out["hesitate" if mine else "surprise"].append(it)
    for r in rows:
        mine = r["side"] == my_side
        if r["label"] and r["ply"] not in marked.get((mine, "bad"), ()):
            out["data_only" if mine else "opp_data_only"].append(_engine(r))
        if r.get("good") and r["ply"] not in marked.get((mine, "good"), ()):
            out["good_data_only" if mine else "good_opponent"].append(_engine(r))
    return out


def sections(gap: dict) -> list[dict]:
    """compare の結果を、6つのラベルごとにまとめる（画面・ノート用）。項目には区分名 group が付く。"""
    out = []
    for s in SECTIONS:
        items = sorted(({**m, "group": name, "group_key": k} for k, name, _ in s["groups"] for m in gap.get(k, [])),
                       key=lambda m: m["ply"])
        out.append({**s, "title": section_title(s), "items": items,
                    "counts": [(name, len(gap.get(k, []))) for k, name, _ in s["groups"] if name]})
    return out


def has_marks(gap: dict) -> bool:
    return any(gap.get(k) for k in MARKED_KEYS)


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
