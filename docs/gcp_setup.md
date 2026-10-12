# Google Cloud 作業手順(要約)

前提: Google Cloudプロジェクトと請求先アカウントがあり、`gcloud auth login` 済み。

1. **予算アラート**(最初にやる): 請求アカウントのコンソール「予算とアラート」で、金額としきい値を決めて作る。
   CLIは `gcloud billing budgets create --billing-account=… --display-name=… --budget-amount=…JPY --threshold-rule=percent=0.5`
   (引数名はヘルプと照合済み。`budgets` はAPI有効化と権限が要る場合がある)
2. `PROJECT_ID=<id> ./scripts/gcp_setup.sh` — API有効化、Artifact Registry、Firestore(Native)、実行用サービスアカウント
   (権限は `datastore.user` と `aiplatform.user` だけ)、TTL、Secret Manager(セッション秘密・デモ認証)
3. `PROJECT_ID=<id> ./scripts/deploy.sh` — ソースからビルドして Cloud Run へ
4. `DEMO_EMAIL=… DEMO_PASSWORD=… ./scripts/check_deploy.sh <URL>` — 疎通確認。`degraded = True` なら Gemini 呼び出しの失敗(ログの `llm_error`)
5. 提出物の準備: デモ用アカウントを審査員に渡す、GitHubを公開にする

## 詰まりやすい所
- `--allow-unauthenticated` は、組織ポリシー(ドメイン限定共有)で拒否されることがある。その場合は管理者に確認
- Geminiのモデル名(既定 `gemini-3.5-flash`)と、リージョン `asia-northeast1` での提供は**未確認**。`GEMINI_MODEL` / `GOOGLE_CLOUD_LOCATION` で変えられる。
  モデルが見つからない場合も、画面は縮退運転で動く
- Firestoreのリージョンは作成後に変えられない
- TTLの反映には時間がかかる。削除は期限後すぐではない
