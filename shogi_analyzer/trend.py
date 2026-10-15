"""複数の対局から言える傾向のまとめ（大会に向けた資料。発案者の指示 2026-10-14）。

保存した対局の集計（learn.aggregate）と局面例（learn.pick_examples）から、3ページ以内の資料を作る。
- 「記録から言えること」は決まった規則で作る箇条書き（数はすべて集計の値）
- 「AIの所感」は Gemini が書いた箇条書き。ここでは書かせず（shogi_coach.trend）、検証だけをする:
  記録に無い数字を含む行・長すぎる行は捨て、行数も制限する（設計原則 1・2）
- 局面例は規則で選んだもの（AIは選ばない）。図はその手を指す直前の局面

エンジンもLLMも呼ばない。
"""
from __future__ import annotations

import hashlib
import json
import re

from .learn import EXAMPLE_KINDS, PHASES

MAX_AI_BULLETS = 8     # AIの所感の行数（3ページに収めるため）
MAX_BULLET_CHARS = 120
MIN_GAMES_NOTE = 3     # これより少ない対局数では、傾向は参考程度と注記する


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def facts(agg: dict) -> list[str]:
    """記録から言えること（規則で作る箇条書き）。agg は learn.aggregate の結果。"""
    if not agg.get("games"):
        return []
    out = [f"保存した対局は{agg['games']}局（うちエンジンで解析した対局は{agg['games_analyzed']}局）。"]
    if agg["games"] < MIN_GAMES_NOTE:
        out.append(f"対局がまだ{MIN_GAMES_NOTE}局より少ないので、傾向は参考程度です。")
    if agg["tags"]:
        top = "、".join(f"{t['tag']}（{t['count']}回）" for t in agg["tags"][:3])
        out.append(f"悪手だと思った自分の手で多く選んだ原因: {top}。")
    regret_phase = agg.get("regret_by_phase", {})
    if any(regret_phase.values()):
        p = max(PHASES, key=lambda k: (regret_phase.get(k, 0), -PHASES.index(k)))
        out.append("悪手だと思った自分の手の印が多い段階: " + "、".join(f"{k} {regret_phase.get(k, 0)}" for k in PHASES)
                   + f"（いちばん多いのは{p}）。")
    if agg["bad_moves"]:
        r = agg.get("bad_rate_by_phase") or {}
        if agg.get("bad_rate") is not None and any(v is not None for v in r.values()):
            p = max((k for k in PHASES if r.get(k) is not None), key=lambda k: (r[k], -PHASES.index(k)))
            out.append(f"自分が指した手のうち、悪手候補だった割合は{pct(agg['bad_rate'])}（"
                       + "、".join(f"{k} {pct(r[k]) if r.get(k) is not None else '—'}" for k in PHASES)
                       + f"）。いちばん高いのは{p}。")
        else:
            p = max(PHASES, key=lambda k: (agg["bad_by_phase"][k], -PHASES.index(k)))
            out.append(f"自分の悪手候補は{agg['bad_moves']}回（" + "、".join(f"{k} {agg['bad_by_phase'][k]}" for k in PHASES)
                       + f"）。いちばん多いのは{p}。")
        out.append(f"悪手候補のうち、解析を見る前に自分で気づけたのは{agg['bad_noticed']}回"
                   f"（{pct(agg['bad_noticed'] / agg['bad_moves'])}）。")
    c = agg["counts"]
    if c.get("feeling_only"):
        out.append(f"悪手だと思ったが、エンジンでは勝率がほとんど下がっていなかった自分の手（感覚だけ）: {c['feeling_only']}回。")
    if c.get("hesitate"):
        out.append(f"ほかにも指す手があったと思いながら指した自分の手: {c['hesitate']}回。")
    if c.get("opp_match") or c.get("opp_feeling_only") or c.get("opp_data_only"):
        out.append(f"相手の悪手候補は{c.get('opp_match', 0) + c.get('opp_data_only', 0)}回。そのうち悪手だと気づけたのは"
                   f"{c.get('opp_match', 0)}回（相手の悪手だと思ったが、エンジンでは勝率がほとんど下がっていなかった手は"
                   f"{c.get('opp_feeling_only', 0)}回）。")
    s = agg.get("surprise", {})
    if s.get("total"):
        out.append(f"予想外で驚いた相手の手は{s['total']}回。そのうちエンジンの最善手と同じだったのは{s['best']}回、"
                   f"相手の悪手候補だったのは{s['bad']}回。")
    a = agg.get("alts", {})
    if sum(a.values()):
        out.append(f"迷った別の読みは{sum(a.values())}本。実戦の手と比べて、良かった {a['良い']}本、"
                   f"同じくらい {a['同じくらい']}本、悪かった {a['悪い']}本。")
    if agg["good_moves"]:
        out.append(f"自分の好手候補は{agg['good_moves']}回。")
    if c.get("good_match") or c.get("good_feeling_only"):
        out.append(f"好手だと思った自分の手は{c.get('good_match', 0) + c.get('good_feeling_only', 0)}回。"
                   f"そのうちエンジンの最善手と同じだったのは{c.get('good_match', 0)}回。")
    return out


def example_text(e: dict) -> list[tuple[str, str]]:
    """局面例の欄に出す項目（見出し, 値）。数はすべて保存した集計の値。"""
    out = [("実戦の手", f"{e['side']}{_strip(e['move_jp'])}（{e['label'] or ('最善手と同じ' if e['was_best'] else '判定なし')}、"
                        f"勝率の下落 {pct(e['drop'])}）"),
           ("エンジンの最善手", e["side"] + _strip(e["best_jp"]))]
    if e.get("tags"):
        out.append(("選んだ原因", "、".join(e["tags"])))
    if e.get("alt"):
        x = e["alt"]
        out.append(("迷った別の読み", " ".join(x["line_jp"])
                    + f"（読みの終わりの勝率 {pct(x['final_winrate'])}。実戦の手の後は {pct(x['actual_winrate'])}）"))
    return out


def example_title(e: dict) -> str:
    return f"{EXAMPLE_KINDS[e['kind']]}　{e.get('created', '')}の対局 {e['ply']}手目（{e['phase']}）"


def ai_input(agg: dict, examples: list[dict]) -> dict:
    """AIに渡す材料（数だけ。対局者名・コメント・棋譜は入れない）。"""
    return {"facts": facts(agg),
            "tags": agg.get("tags", []), "bad_by_phase": agg.get("bad_by_phase", {}),
            "bad_rate_by_phase": {k: pct(v) for k, v in (agg.get("bad_rate_by_phase") or {}).items() if v is not None},
            "regret_by_phase": agg.get("regret_by_phase", {}),
            "examples": [{"kind": EXAMPLE_KINDS[e["kind"]], "phase": e["phase"], "date": e.get("created", ""), "ply": e["ply"],
                          **{k: v for k, v in example_text(e)}} for e in examples]}


def verify(text: str, material: dict) -> list[str]:
    """AIの文から箇条書きの行だけを取り出し、検証を通った行を返す。

    捨てる行: 材料に無い数字を含む行（AIに数字を作らせない）、長すぎる行。残すのは MAX_AI_BULLETS 行まで。
    """
    wide = str.maketrans("０１２３４５６７８９", "0123456789")  # 全角の数字（▲７六歩 など）も半角にそろえて比べる
    known = set(re.findall(r"[0-9]+(?:\.[0-9]+)?", json.dumps(material, ensure_ascii=False).translate(wide)))
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*(?:[-*]\s+|[・●]\s*|\d+[.)]\s+)(.*)", line)
        if not m:
            continue
        b = re.sub(r"\*\*|__|`", "", m.group(1)).strip()
        b = b.translate(wide)
        if not b or len(b) > MAX_BULLET_CHARS:
            continue
        if any(n not in known for n in re.findall(r"[0-9]+(?:\.[0-9]+)?", b)):
            continue
        out.append(b)
    return out[:MAX_AI_BULLETS]


def digest(agg: dict, examples: list[dict]) -> str:
    """集計と局面例が変わったかを見分ける値（変わっていなければ、作り直さない）。"""
    return hashlib.sha256(json.dumps([agg, examples], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def collect(agg: dict, examples: list[dict], ai_bullets: list[str], created: str = "", ai_error: str = "") -> dict:
    """傾向まとめの中身（画面・PDF・印刷用ページで共通）。"""
    return {"created": created, "games": agg.get("games", 0), "facts": facts(agg), "ai": list(ai_bullets),
            "ai_error": ai_error, "examples": examples, "digest": digest(agg, examples)}


def _strip(jp: str) -> str:
    return re.sub(r"\(\d\d\)$", "", jp)
