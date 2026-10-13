"""Streamlit から使う、1対局ぶんのコーチとの会話。"""
from __future__ import annotations

import uuid

import cshogi
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from shogi_analyzer.kifu_analysis import Kifu
from .agent import root_agent

APP = "furikaeri_shogi"
_sessions = InMemorySessionService()
_runner = Runner(agent=root_agent, app_name=APP, session_service=_sessions)


class CoachSession:
    def __init__(self, kifu: Kifu, ms: int = 400, runner: Runner | None = None,
                 rows: list[dict] | None = None):
        """rows: 画面側で解析済みの全手解析結果。渡すとエージェントは同じ結果を使う（再解析しない）。"""
        from .tools import summarize_game
        self.runner = runner or _runner
        self.user_id = "web"
        self.session_id = uuid.uuid4().hex
        state = {"kifu": {"start_sfen": kifu.start_sfen,
                          "moves": [cshogi.move_to_usi(m) for m in kifu.moves]},
                 "ms": ms}
        if rows is not None:
            state.update(game=summarize_game(rows), rows=rows)
        self.runner.session_service.create_session_sync(
            app_name=self.runner.app_name, user_id=self.user_id, session_id=self.session_id, state=state)

    def set_regret(self, gap: dict) -> None:
        """本人の印とエンジン判定の突き合わせ（shogi_analyzer.regret.compare の結果）を渡す。

        印は会話の途中でも変わるので、質問のたびに呼んで最新にする。
        """
        from .tools import summarize_regret
        self._set_state(regret=summarize_regret(gap))

    def set_history(self, history: dict) -> None:
        """これまでの対局の集計（shogi_analyzer.learn.aggregate の結果。今回の対局は含めない）を渡す。"""
        self._set_state(history=history)

    def _set_state(self, **delta) -> None:
        import asyncio
        from google.adk.events import Event, EventActions
        svc = self.runner.session_service
        s = svc.get_session_sync(app_name=self.runner.app_name, user_id=self.user_id,
                                 session_id=self.session_id)
        asyncio.run(svc.append_event(s, Event(
            author="user", actions=EventActions(state_delta=delta))))

    def ask(self, text: str, on_tool=None) -> str:
        """メッセージを送り、最終回答の文章を返す。on_tool(name, args) はツール呼び出しごとに呼ばれる。"""
        msg = types.Content(role="user", parts=[types.Part(text=text)])
        answer = []
        for ev in self.runner.run(user_id=self.user_id, session_id=self.session_id, new_message=msg):
            for fc in ev.get_function_calls() or []:
                if on_tool:
                    on_tool(fc.name, dict(fc.args or {}))
            if ev.is_final_response() and ev.content and ev.content.parts:
                answer += [p.text for p in ev.content.parts if getattr(p, "text", None)]
        return "".join(answer).strip()

    def review(self, on_tool=None) -> str:
        return self.ask("この対局をふりかえってください。", on_tool)

    @property
    def state(self) -> dict:
        s = self.runner.session_service.get_session_sync(
            app_name=self.runner.app_name, user_id=self.user_id, session_id=self.session_id)
        return dict(s.state) if s else {}
