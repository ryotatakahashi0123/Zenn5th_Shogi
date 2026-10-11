"""LLMの差し替え口。FakeBrain(固定応答。試験・ローカル)と GeminiBrain(Vertex AI)。

GeminiBrain は、このリポジトリの作成時点で実物のGeminiに対して未検証
(docs/decisions.md の未確認事項。モデル名 U-11、料金 U-9)。
"""
from __future__ import annotations

import json
from typing import Optional, Protocol

from . import prompts


class Brain(Protocol):
    name: str

    def suggest(self, ctx: dict, feedback: Optional[str] = None) -> str: ...
    def narrate(self, ctx: dict, feedback: Optional[str] = None) -> str: ...


class FakeBrain:
    """決まった規則だけで答える。LLMを使わずに画面・API・PDFを通すための固定応答。"""

    name = "fake"

    def suggest(self, ctx: dict, feedback: Optional[str] = None) -> str:
        cands = ctx["candidates"][: ctx["max_marks"]]
        return json.dumps({"marks": [{"n": c["n"], "reason": c["detail"]} for c in cands]},
                          ensure_ascii=False)

    def narrate(self, ctx: dict, feedback: Optional[str] = None) -> str:
        return json.dumps(rule_narration(ctx), ensure_ascii=False)


def rule_narration(ctx: dict) -> dict:
    """数値だけから作る要約。縮退運転でも使う。"""
    c = ctx["compare"]
    mk = c["markCount"]
    if mk == 0:
        summary = "本人の印がまだない。時間のデータだけを示す。"
    else:
        summary = (f"本人の印は{mk}件。長考などのデータと重なったのは{len(c['coincide'])}件、"
                   f"データでは目立たなかったのは{len(c['feelingOnly'])}件。")
    if c["dataOnly"]:
        summary += f"自分の手で時間を使ったのに印のない手が{len(c['dataOnly'])}件ある。"
    items = [{"n": x["n"], "comment": "印とデータが重なった手(" + "・".join(x["dataKinds"]) + ")"}
             for x in c["coincide"][:5]]
    focus = []
    if c["dataOnly"]:
        focus.append("時間を使った手を、盤面で見直す")
    if c["timeHabit"]:
        focus.append("時間の使い方と原因タグの組合せを見直す")
    return {"summary": summary, "items": items, "next_focus": focus[:3]}


class GeminiBrain:
    """google-genai のVertexモード。環境変数 GOOGLE_GENAI_USE_VERTEXAI 等はCloud Run側で設定する。"""

    name = "gemini"

    def __init__(self, model: str, project: str, location: str) -> None:
        from google import genai  # 遅延import

        self.model = model
        self.client = genai.Client(vertexai=True, project=project or None, location=location or None)

    def _generate(self, system: str, ctx: dict, schema, feedback: Optional[str]) -> str:
        from google.genai import types

        payload = json.dumps(ctx, ensure_ascii=False)
        text = f"データ(JSON):\n{payload}"
        if feedback:
            text += f"\n\n前回の出力は次の理由で不採用になった。直して再出力する:\n{feedback}"
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.2,
            max_output_tokens=2048,  # 思考トークンが出力枠を食う挙動があるため広めに取る(04の7.5)
        )
        resp = self.client.models.generate_content(model=self.model, contents=text, config=cfg)
        return resp.text or ""

    def suggest(self, ctx: dict, feedback: Optional[str] = None) -> str:
        from .schemas import CandidateOutput
        return self._generate(prompts.SUGGEST, ctx, CandidateOutput, feedback)

    def narrate(self, ctx: dict, feedback: Optional[str] = None) -> str:
        from .schemas import NarrationOutput
        return self._generate(prompts.NARRATE, ctx, NarrationOutput, feedback)


def make_brain(kind: str, model: str = "", project: str = "", location: str = "") -> Optional[Brain]:
    """none は None(LLMを呼ばない=縮退運転)。"""
    if kind == "gemini":
        return GeminiBrain(model, project, location)
    if kind == "none":
        return None
    return FakeBrain()
