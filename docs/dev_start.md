# 開発を始めるときの手順(毎回)

最終更新: 2026-10-12 09:15 JST

作業の最初に、上から順に実行する。リポジトリの直下(`app/` と `README.md` が見える場所)で行う。
Cloud Shell は、しばらく触らないと接続が切れてサーバーが止まる。止まったら、この手順をやり直す。

## 1. 場所とプロジェクトを確かめる

```bash
cd ~/Zenn5th_Shogi            # リポジトリの直下。ls で app/ と README.md が見えること
gcloud config get-value project   # zenn5thshogi と出ること
```

違っていたら次を実行する。

```bash
gcloud config set project zenn5thshogi
```

## 2. 最新のコードにする

```bash
git status                    # 未コミットの変更がないか見る
git checkout main
git pull
git checkout -b feature/<やること>   # 例: feature/sheet-side-panel
```

- 提出後(12/1まで)、mainは凍結される。**mainに直接pushしない**。いつもブランチを切って、Pull Requestで入れる
- 他の人(友人)が先に進めている場合がある。`git pull` で取り込んでから始める

## 3. Python環境を起こす

```bash
. .venv/bin/activate          # 「No such file」と出たら下の「環境がない場合」へ
pip install -r requirements-dev.txt   # requirements が変わった日だけでよい(2026-10-12に google-cloud-storage を追加)
python -m pytest -q           # 全部通ること(2026-10-12時点で70件)
```

環境がない場合:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
```

試験が通らない状態で作業を始めない。通らなければ、先にその原因を直す(分からなければ出力を貼って相談する)。

## 4. 動かして確かめる

```bash
uvicorn app.main:app_factory --factory --host 0.0.0.0 --port 8080
```

- 「ウェブでプレビュー」→「ポート 8080 でプレビュー」で開く
- ログインは `demo@example.com` / `demo-pass`(開発時の既定)
- この起動では、AIは固定応答(`FakeBrain`)で、Google Cloudの費用はかからない
- 止めるときは `Ctrl+C`

## 5. 作業が終わったら

```bash
python -m pytest -q
git status                    # .env や .venv、パスワード、トークンが入っていないか目で見る
git add -A
git commit -m "<何をしたか>"
git push -u origin feature/<やること>
```

GitHubでPull Requestを作り、確認してからmainに入れる。

## 6. Google Cloudに出す前に(毎回)

1. 試験が全部通っている
2. 予算アラートがある(請求アカウントの「予算とアラート」)
3. `git status` に秘密(トークン、パスワード、`.env`)がない
4. `PROJECT_ID=zenn5thshogi ./scripts/deploy.sh`
5. `DEMO_EMAIL=… DEMO_PASSWORD=… ./scripts/check_deploy.sh <URL>` で `degraded = False` を確かめる
6. 数局動かしたら、請求画面で実額を見る(反映は数時間〜1日遅れる)。アプリの実行記録の概算と見比べる

## 7. やってはいけないこと

- トークン、パスワード、`SESSION_SECRET` を、チャット・コード・コミットに書かない。漏れたら、すぐ無効にして作り直す
- `samples/raw/`(生の棋譜、対局者IDを含む)をコミットしない。公開してよい棋譜は `samples/anon/` だけ
- 請求・クーポンに関わる設定を、他の人に渡さない
- `git push -f`(強制push)をしない

## 8. Claudeに頼むとき

- 最初に `AGENTS.md` を読ませる(守ること、構成、数字を変えたときの扱いが書いてある)
- エラーは、出力をそのまま貼る(秘密が入っていないか確認してから)
- 数字や判定を変える依頼は、変える理由も伝える。試験の期待値(サンプル2局)が変わるため

## 9. 困ったとき

| 症状 | 見る所 |
|---|---|
| `pytest` が通らない | 出力の最初の失敗を貼る。`pip install -r requirements-dev.txt` をやり直す |
| 画面が開かない | サーバーが止まっていないか、ポートが8080か |
| `degraded = True` | Gemini呼び出しの失敗。`gcloud run services logs read furikaeri --region asia-northeast1 --limit 50 \| grep llm_error` |
| `push` が拒否される | 履歴の食い違い。強制pushせず、出力を貼って相談する |
| 費用が心配 | `docs/gcp_setup.md` の「費用の管理」。上限は `DAILY_COST_CAP_USD` |

髙橋瞭太
