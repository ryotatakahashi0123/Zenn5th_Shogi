"""ADKによるエージェント定義(未実装・未配線)。

状態: このリポジトリの作成時点では、ADK(`google-adk`)のAPI名・版を公式ドキュメントで確認できていない
(05のU-5)。そのため、実行の枠(許可リスト、上限、検証、縮退運転、実行記録)は
app/agent/runner.py と tools.py に自前で実装し、LLM呼び出しは brain.py に分離してある。

ADKへ載せ替えるときの対応(案。API名は実装時に adk.dev で確認する):
  - tools.ToolBox の各 `_t_*` を、ADKのツールとして登録する(許可リストはそのまま)
  - brain.GeminiBrain の `suggest` / `narrate` を、ADKのエージェントの実行に置き換える
  - guardrails.validate_* は、ADKの出力を受けた後にも必ず通す
  - runner の steps は、ADKのコールバック/トレースから作る

提出要件(Cloud Run + AI技術)は、現状の構成(Cloud Run + Gemini on Vertex AI)で満たす見込みだが、
「ADKを使う」と説明文に書くのは、載せ替えて動作を確認してからにする。
"""
