# Google Cloud 作業手順(要約)

前提: Google Cloudプロジェクトと請求先アカウントがあり、`gcloud auth login` 済み。

1. **予算アラート**(最初にやる): 請求アカウントのコンソール「予算とアラート」で、金額としきい値を決めて作る。
   CLIは `gcloud billing budgets create --billing-account=… --display-name=… --budget-amount=…JPY --threshold-rule=percent=0.5`
   (引数名はヘルプと照合済み。`budgets` はAPI有効化と権限が要る場合がある)
2. `PROJECT_ID=<id> ./scripts/gcp_setup.sh` — API有効化、Artifact Registry、Cloud Storage バケット(30日で自動削除のルールつき)、実行用サービスアカウント
   (権限は `aiplatform.user` と、そのバケットの `storage.objectUser` だけ)、Secret Manager(セッション秘密・デモ認証)
3. `PROJECT_ID=<id> ./scripts/deploy.sh` — ソースからビルドして Cloud Run へ
4. `DEMO_EMAIL=… DEMO_PASSWORD=… ./scripts/check_deploy.sh <URL>` — 疎通確認。`degraded = True` なら Gemini 呼び出しの失敗(ログの `llm_error`)
5. 提出物の準備: デモ用アカウントを審査員に渡す、GitHubを公開にする

## 費用の管理
- アプリ側: 実行回数と保存件数の上限は審査用になし(0)。1日の全体の費用上限(`DAILY_COST_CAP_USD`、既定3ドル)を超えると、AIを使わず規則による要約に切り替わる。実行記録に、トークン数と概算費用が出る
- 請求側: 予算アラートはクレジットを差し引かない設定で作る(`--credit-types-treatment=exclude-all-credits`)。アラートは通知だけで、自動停止ではない
- 概算は実際の請求と一致しない場合がある。デプロイ後に請求画面の実額と見比べる

## 詰まりやすい所
- `--allow-unauthenticated` は、組織ポリシー(ドメイン限定共有)で拒否されることがある。その場合は管理者に確認
- Geminiのモデル名(既定 `gemini-3.5-flash`)と、リージョン `asia-northeast1` での提供は**未確認**。`GEMINI_MODEL` / `GOOGLE_CLOUD_LOCATION` で変えられる。
  モデルが見つからない場合も、画面は縮退運転で動く
- バケットのリージョンは作成後に変えられない。バケット名は世界で一意(使われていたら `BUCKET=…` で別の名前にする)
- ライフサイクルによる削除は、期限の直後ではなく、数日遅れることがある
- `roles/storage.objectUser` のロール名は、実プロジェクトでは未確認
