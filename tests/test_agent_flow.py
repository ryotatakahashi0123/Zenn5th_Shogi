"""Gemini の代わりに台本どおりツールを呼ぶモデルで、エージェントの流れを通しで確認する。

    ENGINE_URL=http://localhost:8081 python -m pytest tests/test_agent_flow.py -s
（ENGINE_URL を外すとプロセス内のエンジンを使う）
"""
from __future__ import annotations

from typing import AsyncGenerator

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from shogi_analyzer.kifu_analysis import load_kifu
from shogi_coach.agent import root_agent
from shogi_coach.session import CoachSession

SCRIPT = [
    ("analyze_game", {}),
    ("deep_dive", {"ply": 15}),
    ("compare_move", {"ply": 15, "move": "３四飛"}),
    ("compare_move", {"ply": 15, "move": "５一玉"}),  # 指せない手 → エラーがモデルに返る
]


class ScriptedLlm(BaseLlm):
    model: str = "scripted"
    seen: list = []

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False
                                     ) -> AsyncGenerator[LlmResponse, None]:
        responses = [p.function_response for c in llm_request.contents for p in (c.parts or [])
                     if p.function_response]
        self.seen[:] = [(r.name, r.response) for r in responses]
        step = len(responses)
        if step < len(SCRIPT):
            name, args = SCRIPT[step]
            part = types.Part(function_call=types.FunctionCall(name=name, args=args))
        else:
            part = types.Part(text="### 総評\n（台本モデルの回答）")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def test_flow():
    llm = ScriptedLlm()
    agent = root_agent.model_copy(update={"model": llm})
    runner = Runner(agent=agent, app_name="test", session_service=InMemorySessionService())
    kifu = load_kifu("sample.kif")
    s = CoachSession(kifu, ms=200, runner=runner)
    calls = []
    answer = s.review(on_tool=lambda n, a: calls.append((n, a)))

    assert [c[0] for c in calls] == [n for n, _ in SCRIPT]
    res = dict(llm.seen)  # 同名は最後の結果
    names = [n for n, _ in llm.seen]
    game = llm.seen[0][1]
    deep = llm.seen[1][1]
    cmp_ok = llm.seen[2][1]
    cmp_bad = llm.seen[3][1]
    print("\nanalyze_game flagged:", game["flagged"])
    print("deep_dive:", deep)
    print("compare ok:", cmp_ok)
    print("compare bad:", cmp_bad)
    assert game["plies"] == 40 and game["flagged"][0]["ply"] == 15
    # 15手目は ▲３四飛 と ▲５八玉 の勝率の差が1%ほどで、どちらが1番目になるかは実行ごとに変わる（エンジンは並列で読む）
    assert "▲３四飛" in [c["move"] for c in deep["candidates"]]
    assert cmp_ok["alternative"]["move"] == "▲３四飛"
    assert "error" in cmp_bad
    st = s.state
    assert len(st["actions"]) == 4 and "rows" in st
    assert answer.startswith("### 総評")
    assert names == ["analyze_game", "deep_dive", "compare_move", "compare_move"]
