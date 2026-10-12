"""エージェントの実行(2回に分ける。03の3章)。

実行1: 候補印の選定(画面に薄く示すだけ。印は付けない)
実行2: 突き合わせと教材化(本人の印を受けて)

LLMが使えない・出力が検証に落ち続ける場合は、決まった規則だけの結果を返す(縮退運転、FR-36)。
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ..domain.board import Position
from ..infra.config import Settings
from ..infra.store import new_id, now_iso
from . import guardrails
from .brain import Brain, rule_narration
from .tools import ToolBox, build_candidates, candidate_numbers

log = logging.getLogger("furikaeri.agent")

DISCLAIMER = ("評価値は参考値(エンジン・探索深さ・設定で変わる)。AI所感は推測を含む。"
              "最善手は未取得であり、実戦の手の良し悪しは判定していない。")


def _new_run(kind: str) -> dict:
    return {"id": new_id(), "kind": kind, "startedAt": now_iso(), "finishedAt": None,
            "steps": [], "output": None,
            "guardrail": {"llmCalls": 0, "rejected": 0, "reasons": [], "degraded": False}}


def _llm_loop(brain: Optional[Brain], method: str, ctx: dict, validator: Callable[[str], tuple],
              cfg: Settings, run: dict):
    """検証に通る出力が得られれば返す。得られなければ None(→縮退運転)。"""
    import time
    g = run["guardrail"]
    if brain is None:
        g["reasons"].append("LLMを使わない設定(縮退運転)")
        return None
    feedback = None
    for _ in range(cfg.max_retries + 1):
        if g["llmCalls"] >= cfg.max_llm_calls:
            g["reasons"].append("LLM呼び出しの上限")
            break
        g["llmCalls"] += 1
        t0 = time.perf_counter()
        try:
            raw = getattr(brain, method)(ctx, feedback)
        except Exception as e:  # 認証・ネットワーク・割当などの失敗は縮退運転へ
            log.warning("llm_error", extra={"method": method, "error": type(e).__name__})
            g["rejected"] += 1
            g["reasons"].append(f"LLM呼び出し失敗: {type(e).__name__}")
            run["steps"].append({"tool": f"llm.{method}", "args": brain.name, "resultSummary": "失敗",
                                 "ms": round((time.perf_counter() - t0) * 1000, 1)})
            feedback = None
            continue
        out, reasons = validator(raw)
        run["steps"].append({"tool": f"llm.{method}", "args": brain.name,
                             "resultSummary": "採用" if out is not None else "不採用: " + " / ".join(reasons)[:120],
                             "ms": round((time.perf_counter() - t0) * 1000, 1)})
        if out is not None:
            return out
        g["rejected"] += 1
        g["reasons"].extend(reasons)
        feedback = "\n".join(reasons)
    return None


def run_suggestions(game: dict, positions: list[Position], brain: Optional[Brain], cfg: Settings) -> dict:
    """実行1。候補印(最大 cfg.max_candidates 件)を選ぶ。"""
    run = _new_run("questions")
    tb = ToolBox(game, [], [], positions, cfg)
    tb.call("get_game_summary")
    tb.call("get_time_outliers")
    tb.call("get_eval_swings")
    cands = build_candidates(game, cfg)
    by_n = {c["n"]: c for c in cands}
    total = len(game["moves"])
    nums = candidate_numbers(game, cands, cfg)

    marks: list[dict] = []
    if cands:
        ctx = {"my_side": game["mySide"], "max_marks": cfg.max_candidates, "total_moves": total,
               "candidates": cands[:20]}
        out = _llm_loop(brain, "suggest", ctx,
                        lambda raw: guardrails.validate_candidates(raw, total, set(by_n), nums, cfg.max_candidates),
                        cfg, run)
        if out is not None:
            marks = [{"n": m.n, "reason": m.reason, "kinds": by_n[m.n]["kinds"], "source": "agent"}
                     for m in out.marks]
        else:
            run["guardrail"]["degraded"] = True
            marks = [{"n": c["n"], "reason": c["detail"], "kinds": c["kinds"], "source": "rule"}
                     for c in cands[: cfg.max_candidates]]
    run["steps"] = tb.steps + run["steps"]
    run["output"] = {"marks": marks, "candidateCount": len(cands), "degraded": run["guardrail"]["degraded"]}
    run["finishedAt"] = now_iso()
    return run


def run_report(game: dict, positions: list[Position], marks: list[dict], cards: list[dict],
               brain: Optional[Brain], cfg: Settings) -> dict:
    """実行2。突き合わせ、考える問題、知識カード、AI所感(または規則による要約)。"""
    run = _new_run("report")
    tb = ToolBox(game, marks, cards, positions, cfg)
    tb.call("get_game_summary")
    cmp_ = tb.call("compare_feeling_vs_data")
    tb.call("get_time_outliers")
    tb.call("get_eval_swings")

    cands = build_candidates(game, cfg)
    # 問題: 本人の「悔しい」→「迷った」→ 候補の順(domain.problems と同じ優先順位)
    order: list[tuple[int, str]] = []
    seen: set[int] = set()
    for feeling in ("regret", "unsure"):
        for mk in sorted(marks, key=lambda x: x["moveNo"]):
            if mk.get("feeling") == feeling and mk["moveNo"] not in seen:
                order.append((mk["moveNo"], "user_mark"))
                seen.add(mk["moveNo"])
    for c in cands:
        if c["n"] not in seen and c["mine"]:
            order.append((c["n"], "candidate"))
            seen.add(c["n"])
    for n, origin in order:
        if len(tb.problems) >= cfg.max_problems:
            break
        tb.call("add_problem", n=n, origin=origin)

    tags = sorted({t for m in marks for t in m.get("causeTags", [])})
    found = tb.call("search_knowledge_cards", tags=tags) if tags else []

    allowed_ns = {m["moveNo"] for m in marks} | {x["n"] for k in ("coincide", "feelingOnly", "timeHabit", "dataOnly")
                                                  for x in cmp_[k]}
    nums = candidate_numbers(game, cands, cfg) | allowed_ns | {cmp_["markCount"]}
    nums |= {len(cmp_[k]) for k in ("coincide", "feelingOnly", "dataOnly", "timeHabit")}
    ctx = {"my_side": game["mySide"], "marks": [
               {"n": m["moveNo"], "feeling": m["feeling"], "causeTags": m.get("causeTags", [])} for m in marks],
           "compare": cmp_, "stats": tb.call("get_game_summary")}
    nums |= guardrails.numbers_in(str(ctx["stats"]))
    # note は利用者の自由記述。LLMには渡さない(命令混入と個人情報の送信を避ける。判断は docs/decisions.md D-7)
    nar = _llm_loop(brain, "narrate", ctx,
                    lambda raw: guardrails.validate_narration(raw, allowed_ns, nums), cfg, run)
    if nar is not None:
        narration = {"source": "ai", "summary": nar.summary,
                     "items": [i.model_dump() for i in nar.items], "nextFocus": nar.next_focus}
    else:
        run["guardrail"]["degraded"] = True
        r = rule_narration(ctx)
        narration = {"source": "rule", "summary": r["summary"], "items": r["items"], "nextFocus": r["next_focus"]}

    run["steps"] = tb.steps + run["steps"]
    run["output"] = {
        "compare": cmp_, "problems": tb.problems, "cards": found, "narration": narration,
        "noMarks": cmp_["markCount"] == 0, "evalAvailable": game["derived"]["evalAvailable"],
        "degraded": run["guardrail"]["degraded"], "disclaimer": DISCLAIMER,
    }
    run["finishedAt"] = now_iso()
    return run
