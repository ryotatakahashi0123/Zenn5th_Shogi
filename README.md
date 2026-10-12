# ふりかえり将棋(仮称)

将棋ウォーズの棋譜(KIF)を貼ると、**時間の使い方**と**自分の感覚(「ここが悔しかった」)**を突き合わせて、
考える問題つきの振り返りノート(画面・PDF)にするエージェント。
第5回 Agentic AI Hackathon with Google Cloud 向けのプロトタイプ。Cloud Run + Gemini(Vertex AI)で動かす想定。

- 数字・局面は純粋関数で計算し、LLMには作らせない。LLMの出力は検証を通ったものだけ表示し、通らなければ規則による要約に切り替える
- 評価値なしの棋譜でも動く(「悪手」とは言わず「時間を使った手」と呼ぶ)
- エージェントは本人の代わりに印を付けない・問題に答えない

## すぐ動かす(クラウド不要)
```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
uvicorn app.main:app_factory --factory --reload      # http://localhost:8000
```
ログイン `demo@example.com` / `demo-pass`(開発時の既定)。サンプル2局が入っている(`samples/anon`、対局者名は匿名化済み)。
開発時の保存先はメモリ、LLMは固定応答(`FakeBrain`)。

## Google Cloud に出す
`docs/gcp_setup.md` の順に `scripts/gcp_setup.sh` → `scripts/deploy.sh` → `scripts/check_deploy.sh`。

## 構成
`AGENTS.md`(作業ルール)/ `docs/architecture.md`(図)/ `docs/decisions.md`(判断記録)/ `docs/api.md` / `docs/design/`(設計の原文)

## 状態:何が確かめてあり、何が未確認か
確かめた(自動試験70件。サンプル2局の手数・長考の手番号・残り時間・局面を期待値として固定):
KIF解析、時間分析、盤面の再現、突き合わせ、ガードレールと縮退運転、API一式(MemoryStore + FakeBrain)、PDF生成、
スマホ幅(360px)での画面表示。

**未確認(クラウドで初めて分かること)**
- `GcsStore`(Cloud Storage版。偽のバケットでの試験のみ。実際のバケットでは未実行)と、バケットの30日自動削除ルール
- `GeminiBrain` とモデル名 `gemini-3.5-flash` の提供状況・出力が検証を通る割合
- `scripts/` のgcloud引数(`--help` とは照合済み、実プロジェクトでは未実行)、TTL設定
- ADKは未接続(`docs/decisions.md` D-3)
- PDFの日本語フォントは既定で埋め込まれない(D-8)
- 評価値(`*` 行)の読み取りは仮定した書式。サンプルには評価値がなく、実物では未検証
- 知識カードは見本のみ(`knowledge/cards.json`)。載せる動画の利用可否は未確認

## 提出メモ
提出は 2026-10-15 23:59 JST 締切。提出後、mainは12/1まで凍結される規定。ブランチで進める。

髙橋瞭太
