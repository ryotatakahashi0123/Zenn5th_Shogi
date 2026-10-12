# AGENTS.md(このリポジトリで作業する人・AI向け)

## 守ること
- 数字・手番号・局面は、必ず `app/domain/` の関数から出す。LLMに計算させない、局面を作らせない
- LLMの出力は `app/agent/guardrails.py` を通ったものだけ画面に出す。通らなければ規則による縮退運転
- エージェントは利用者の代わりに印を付けない・問題に答えない。「最善手」は取得できていない前提で書く(bestMove は None)
- 「悪手」「最善」「正解は」を出力に出さない(`BANNED_PHRASES`)。評価値なしの棋譜では「時間を使った手」と呼ぶ
- 利用者の自由記述(`note`)はLLMに送らない(docs/decisions.md D-7)
- 生の棋譜・対局者ID・パスワード・秘密をコミットしない。公開してよい棋譜は `samples/anon/` だけ
- main への直接pushは、提出後(12/1まで)凍結される規定。ブランチ→PRで進める

## 作業手順
```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q                      # 全部通ること
uvicorn app.main:app_factory --factory --reload   # http://localhost:8000  demo@example.com / demo-pass
```

## 構成
`app/domain` 純粋関数(棋譜解析・時間分析・盤面・突き合わせ・問題) / `app/agent` LLM周り(brain・guardrails・runner・tools) /
`app/infra` 設定・認証・保存・PDF / `app/api` ルータ / `app/static` 画面 / `tests` / `scripts` / `docs`

## 数字を変えたら
`tests/test_domain.py` にサンプル2局の期待値(手数、長考の手番号、残り時間、局面の駒数)がある。変えるなら、理由を docs/decisions.md に書く。
