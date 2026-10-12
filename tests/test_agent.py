"""ガードレールと縮退運転の試験。LLMが何を返しても、画面に出るのは検証済みの内容だけ。"""
import json

import pytest

from app import service
from app.agent import guardrails as gr
from app.agent.brain import FakeBrain
from app.agent.runner import run_report, run_suggestions
from app.infra.config import Settings
from .conftest import read_sample


@pytest.fixture
def cfg():
    c = Settings()
    c.validate()
    return c


@pytest.fixture
def game(s01, cfg):
    return service.build_game_doc(s01, "demo", cfg, my_side="b", doc_id="t1")


@pytest.fixture
def positions(game):
    return service.positions_for(game)[0]


class Bad:
    name = "bad"

    def __init__(self, text):
        self.text, self.calls = text, 0

    def suggest(self, ctx, feedback=None):
        self.calls += 1
        return self.text

    narrate = suggest


class Boom:
    name = "boom"

    def suggest(self, ctx, feedback=None):
        raise RuntimeError("quota")

    narrate = suggest


def test_strip_fence_and_numbers():
    assert gr.strip_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert gr.numbers_in("５５手目と12秒") == {55, 12}
    assert gr.numbers_ok("55手目", {55}) is None
    assert gr.numbers_ok("99手目", {55}) is not None


def test_validate_candidates_rejects():
    ok = json.dumps({"marks": [{"n": 55, "reason": "55手目は長考"}]})
    out, why = gr.validate_candidates(ok, 100, {55}, {55}, 5)
    assert out is not None and not why
    for raw in ("not json", json.dumps({"marks": [{"n": 999, "reason": "x"}]}),
                json.dumps({"marks": [{"n": 56, "reason": "x"}]}),                      # 候補外
                json.dumps({"marks": [{"n": 55, "reason": "悪手だった"}]}),            # 断定語
                json.dumps({"marks": [{"n": 55, "reason": "77秒かけた"}]}),            # 与えていない数字
                json.dumps({"marks": [{"n": 55, "reason": "a"}, {"n": 55, "reason": "b"}]})):  # 重複
        out, why = gr.validate_candidates(raw, 100, {55}, {55}, 5)
        assert out is None and why, raw


def test_validate_narration_rejects_unknown_move_and_long():
    raw = json.dumps({"summary": "まとめ", "items": [{"n": 77, "comment": "x"}], "next_focus": []})
    out, why = gr.validate_narration(raw, {55}, set())
    assert out is None and any("77" in w for w in why)
    raw = json.dumps({"summary": "あ" * 300, "items": [], "next_focus": []})
    assert gr.validate_narration(raw, set(), set())[0] is None
    raw = json.dumps({"summary": "ok", "items": [], "next_focus": ["a", "b", "c", "d"]})
    assert gr.validate_narration(raw, set(), set())[0] is None


def test_suggestions_with_fake_brain(game, positions, cfg):
    run = run_suggestions(game, positions, FakeBrain(), cfg)
    marks = run["output"]["marks"]
    assert 1 <= len(marks) <= cfg.max_candidates
    assert not run["guardrail"]["degraded"]
    assert all(m["source"] == "agent" for m in marks)


@pytest.mark.parametrize("brain", [None, Bad("garbage"), Boom()])
def test_suggestions_degrade(game, positions, cfg, brain):
    run = run_suggestions(game, positions, brain, cfg)
    assert run["guardrail"]["degraded"] is True
    assert run["output"]["marks"] and all(m["source"] == "rule" for m in run["output"]["marks"])


def test_retry_limit(game, positions, cfg):
    b = Bad("garbage")
    run_suggestions(game, positions, b, cfg)
    assert b.calls == cfg.max_retries + 1 <= cfg.max_llm_calls


def test_report_degrades_and_never_marks(game, positions, cfg):
    run = run_report(game, positions, [], [], Bad("garbage"), cfg)
    out = run["output"]
    assert out["degraded"] and out["narration"]["source"] == "rule"
    assert out["noMarks"] is True  # 印がなくても落ちない。エージェントが代わりに印を付けない
    assert out["problems"]  # 候補から考える問題は出る
    assert all(p.get("bestMove") is None for p in out["problems"])


def test_report_with_marks_and_ai(game, positions, cfg):
    marks = [{"moveNo": 55, "feeling": "regret", "causeTags": ["焦り・早指し"], "note": "秘密のメモ"}]
    run = run_report(game, positions, marks, [], FakeBrain(), cfg)
    assert run["output"]["narration"]["source"] == "ai"
    assert 55 in [p["n"] for p in run["output"]["problems"]]


def test_note_is_not_sent_to_llm(game, positions, cfg):
    seen = {}

    class Spy(FakeBrain):
        def narrate(self, ctx, feedback=None):
            seen["ctx"] = json.dumps(ctx, ensure_ascii=False)
            return super().narrate(ctx, feedback)

    marks = [{"moveNo": 55, "feeling": "regret", "causeTags": [], "note": "ひみつの自由記述"}]
    run_report(game, positions, marks, [], Spy(), cfg)
    assert "ひみつの自由記述" not in seen["ctx"]


class Metered(FakeBrain):
    """トークン数を返す偽のLLM。1回あたり 入力1000・出力2000。"""
    name = "metered"
    last_usage = None

    def suggest(self, ctx, feedback=None):
        self.last_usage = {"in": 1000, "out": 2000}
        return super().suggest(ctx, feedback)



def test_usage_and_cost_recorded(game, positions, cfg):
    run = run_suggestions(game, positions, Metered(), cfg)
    u = run["usage"]
    assert (u["inTokens"], u["outTokens"]) == (1000, 2000)
    assert u["estCostUsd"] == pytest.approx((1000 * cfg.price_in_per_m + 2000 * cfg.price_out_per_m) / 1e6, abs=1e-6)
    assert not run["guardrail"]["degraded"]


def test_fake_brain_has_no_cost(game, positions, cfg):
    assert run_suggestions(game, positions, FakeBrain(), cfg)["usage"]["estCostUsd"] == 0
