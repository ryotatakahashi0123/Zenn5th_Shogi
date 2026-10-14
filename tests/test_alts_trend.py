"""迷った別の読み、成り駒の色、傾向まとめ（エンジン不要。Gemini は台本モデルで代用）。

    python -m pytest tests/test_alts_trend.py
"""
from __future__ import annotations

import copy
import re
from typing import AsyncGenerator

import cshogi
import pytest
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from shogi_analyzer import alts, learn, regret, report, trend
from shogi_analyzer.kifu_analysis import load_kifu
from shogi_analyzer.service import PROMOTED_FILL, board_svg, move_jp, move_list
from shogi_coach import storage
from shogi_coach import trend as trend_job

KIFU = load_kifu("sample.kif")


class FakeEngine:
    """全手解析の代わり。手を指すたびに、指した側（先手）の勝率を wr の順に返す。"""

    def __init__(self, wr=(0.6, 0.55, 0.5)):
        self.wr, self.calls = list(wr), []

    def analyze_game(self, kifu, ms):
        self.calls.append(kifu)
        board, rows = cshogi.Board(kifu.start_sfen), []
        for i, m in enumerate(kifu.moves, start=1):
            u = cshogi.move_to_usi(m)
            side = "▲" if board.turn == cshogi.BLACK else "△"
            jp = move_jp(board, u)[1:]
            board.push(m)
            rows.append({"ply": i, "side": side, "move_usi": u, "move_jp": jp, "best_before_usi": u if i > 1 else "x",
                         "best_before_jp": "最善", "label": "疑問手" if i == 2 else "", "winrate_drop": 0.12 if i == 2 else 0.0,
                         "winrate_sente": self.wr[min(i - 1, len(self.wr) - 1)], "pv": "8c8d" if i == len(kifu.moves) else ""})
        return {"rows": rows}


def _rows(wr15=0.40):
    """実戦の全手解析の行（15手目 ▲６六角 の後の先手勝率は wr15）。"""
    k = KIFU
    return [{"ply": m["ply"], "side": m["side"], "move_usi": cshogi.move_to_usi(k.moves[m["ply"] - 1]),
             "move_jp": m["move_jp"], "label": "悪手" if m["ply"] == 15 else "", "winrate_drop": 0.3 if m["ply"] == 15 else 0.0,
             "best_before_usi": "2d3d" if m["ply"] == 15 else cshogi.move_to_usi(k.moves[m["ply"] - 1]),
             "best_before_jp": "３四飛(24)", "winrate_sente": wr15 if m["ply"] == 15 else 0.5, "good": False, "good_gap": 0.0}
            for m in move_list(k)]


def test_promoted_pieces_are_red():
    svg = board_svg(KIFU, 20)  # 19手目 ▲８二歩成、20手目 △３三角成 の後
    assert re.search(rf'<g id="black-pro-pawn" fill="{PROMOTED_FILL}"', svg)
    assert re.search(rf'<g id="black-horse" fill="{PROMOTED_FILL}"', svg)
    assert '<g id="black-pawn" fill' not in svg  # 成っていない駒は黒のまま


def test_clean_line():
    assert alts.clean_line(KIFU, 15, ["2d3d", "8f7f"]) == ["2d3d", "8f7f"]
    with pytest.raises(ValueError, match="1手以上"):
        alts.clean_line(KIFU, 15, [])
    with pytest.raises(ValueError, match="指せない"):
        alts.clean_line(KIFU, 15, ["5i5h", "5i5h"])
    with pytest.raises(ValueError, match="までです"):
        alts.clean_line(KIFU, 1, ["2g2f", "8c8d", "2f2e", "8d8e", "2e2d", "2c2d", "2h2d"][: alts.MAX_ALT_PLIES + 1])
    with pytest.raises(ValueError, match="同じ読み"):
        alts.clean_line(KIFU, 15, ["2d3d"], [["2d3d"]])
    assert alts.line_jp(KIFU, 15, ["2d3d"]) == ["▲３四飛"]
    assert cshogi.Board(alts.start_sfen(KIFU, 1)).sfen() == cshogi.Board().sfen()


def test_evaluate_and_summarize():
    marks = [regret.clean_mark(15, ["読み抜け"], "", 40, alts=[["2d3d", "8f7f"], ["2d2c+"]])]
    eng, cache = FakeEngine(wr=(0.62, 0.60)), {}
    alts.evaluate(eng, KIFU, marks, 300, cache)
    n = len(eng.calls)  # 読み2つの評価と、読みの終わりから先を伸ばすための読み直し
    assert n >= 2 and all(c.names == ["", ""] for c in eng.calls)  # 対局者名は送らない
    alts.evaluate(eng, KIFU, marks, 300, cache)
    assert len(eng.calls) == n  # 評価済みは読み直さない
    s = alts.summaries(KIFU, marks, _rows(0.40), cache)[15]
    a = s[0]
    assert a["line_jp"] == ["▲３四飛", "△７六飛"] and a["final_winrate"] == 0.60 and a["actual_winrate"] == 0.40
    assert a["verdict"] == "良い" and a["diff"] == 0.2 and a["steps"][1]["label"] == "疑問手" and a["first_best"] is False
    assert a["after"] == ["▲８四歩"] or a["after"] == []  # 読みの終わりから先（エンジンの読み筋。合わなければ空）
    assert "実戦より良い" in alts.describe(a) and "疑問手" in alts.describe(a)
    # 境界: 差が 5% ちょうどは「良い」、5% 未満は「同じくらい」、-5% は「悪い」
    for final, want in ((0.45, "良い"), (0.449, "同じくらい"), (0.35, "悪い")):
        assert alts.summarize(KIFU, 15, ["2d3d"], FakeEngine((final,)).analyze_game(alts.alt_kifu(KIFU, 15, ["2d3d"]), 1)["rows"],
                              _rows(0.40)[14])["verdict"] == want
    # 失敗した読みは失敗として残す。まだの読みは pending
    assert "error" in alts.summarize(KIFU, 15, ["2d3d"], {"error": "engine down"}, None)
    assert alts.summaries(KIFU, marks, _rows(), {})[15][0]["pending"] is True


def test_surprise_and_alts_in_summary_and_examples():
    rows = _rows(0.40)
    rows[15].update(label="悪手", winrate_drop=0.25, best_before_usi="zz")  # 16手目（△）は相手の悪手候補
    marks = [regret.clean_mark(15, ["読み抜け"], "コメント", 40, alts=[["2d3d"]]),
             regret.clean_mark(16, [], "", 40, kind="other")]  # 相手（△）の手: 予想外で驚いた
    cache = {}
    alts.evaluate(FakeEngine((0.6,)), KIFU, marks, 300, cache)
    gap = regret.compare(rows, marks, "▲", alts.summaries(KIFU, marks, rows, cache))
    s = learn.summarize(rows, gap, "2026-10-14", KIFU)
    assert s["surprise"] == {"best": 0, "bad": 1, "total": 1} and s["alts"] == {"良い": 1, "同じくらい": 0, "悪い": 0}
    assert s["regret_by_phase"] == {"中盤": 1}
    kinds = [e["kind"] for e in s["examples"]]
    assert kinds == ["match", "surprise", "alt"] and "コメント" not in str(s["examples"])
    assert cshogi.Board(s["examples"][0]["sfen"]).sfen() == alts.start_sfen(KIFU, 15)
    # 局面例は種類ごとに1つを先に選び、合わせて MAX_EXAMPLES まで
    many = [copy.deepcopy(s) for _ in range(5)]
    picked = learn.pick_examples(many)
    assert len(picked) == learn.MAX_EXAMPLES and [e["kind"] for e in picked] == ["match", "match", "surprise", "alt"]
    agg = learn.aggregate(many)
    assert agg["surprise"]["total"] == 5 and agg["alts"]["良い"] == 5 and agg["counts"]["surprise"] == 5


def _agg_examples():
    rows = _rows(0.40)
    marks = [regret.clean_mark(15, ["読み抜け", "手拍子"], "", 40, alts=[["2d3d"]])]
    cache = {}
    alts.evaluate(FakeEngine((0.6,)), KIFU, marks, 300, cache)
    gap = regret.compare(rows, marks, "▲", alts.summaries(KIFU, marks, rows, cache))
    s = learn.summarize(rows, gap, "2026-10-14", KIFU)
    return learn.aggregate([s, s]), learn.pick_examples([s, s])


def test_trend_facts_and_verify():
    agg, ex = _agg_examples()
    f = trend.facts(agg)
    assert f[0] == "保存した対局は2局（うちエンジンで解析した対局は2局）。" and any("参考程度" in x for x in f)
    assert any(x.startswith("悪手だと思った自分の手で多く選んだ原因: ") and "読み抜け（2回）" in x for x in f)
    material = trend.ai_input(agg, ex)
    text = "前置き\n- 読み抜けが2回あります\n- 中盤に37回も悪手\n・**終盤**は丁寧に\n- " + "長" * 200 + "\n普通の文"
    assert trend.verify(text, material) == ["読み抜けが2回あります", "終盤は丁寧に"]  # 記録に無い数字・長すぎる行・箇条書きでない行を捨てる
    assert len(trend.verify("\n".join(f"- 行{i % 2}" for i in range(20)), material)) == trend.MAX_AI_BULLETS
    assert trend.digest(agg, ex) == trend.digest(agg, ex) and trend.digest(agg, []) != trend.digest(agg, ex)
    assert trend.facts({"games": 0}) == []


def test_trend_pdf_fits_three_pages():
    agg, ex = _agg_examples()
    ex = (ex * 4)[: learn.MAX_EXAMPLES]
    d = trend.collect(agg, ex, ["<script>x</script> " + "長い所感の行です。" * 10] * trend.MAX_AI_BULLETS, "2026-10-14")
    pdf = report.trend_pdf(d)
    assert pdf.startswith(b"%PDF") and len(re.findall(rb"/Type /Page\b", pdf)) <= 3
    page = report.trend_html(d)
    assert "<script>x" not in page and "&lt;script&gt;" in page and page.count('class="card"') == len(ex)


class TrendLlm(BaseLlm):
    model: str = "scripted"
    text: str = ""

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False
                                     ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=self.text)]))


def test_trend_build_and_store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "BUCKET", "")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    assert trend_job.build("taro", ai_ready=False) is None  # 対局が無い
    rows = _rows(0.40)
    gap = regret.compare(rows, [regret.clean_mark(15, ["読み抜け"], "", 40)], "▲")
    storage.save_json("taro", "20261014-aa", "summary", learn.summarize(rows, gap, "2026-10-14", KIFU))
    agent = trend_job._agent().model_copy(update={"model": TrendLlm(text="- 読み抜けが1回\n- 99回も負けた")})
    runner = Runner(agent=agent, app_name="t", session_service=InMemorySessionService())
    d = trend_job.build("taro", ai_ready=True, runner=runner)
    assert d["ai"] == ["読み抜けが1回"] and d["games"] == 1 and storage.load_trend("taro")["digest"] == d["digest"]
    # 集計が変わらなければ作り直さない（Gemini を呼ばない）
    assert trend_job.build("taro", ai_ready=True, runner=None)["created"] == d["created"]
    # 傾向まとめは対局の一覧に混ざらない
    assert [s["game_id"] for s in storage.load_summaries("taro")] == ["20261014-aa"]
    # AIを使わないときは、記録と局面例だけで作る
    d2 = trend_job.build("taro", ai_ready=False, force=True)
    assert d2["ai"] == [] and d2["ai_error"] and d2["facts"]
