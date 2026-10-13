#!/usr/bin/env bash
# ふりかえり将棋を Google Cloud にデプロイする（初回もこの1本で可。何度実行しても同じ状態になる）。
#
#   export PROJECT_ID=<プロジェクトID>
#   bash deploy/setup.sh
#
# 構成:
#   shogi-web    … 公開（Streamlit + ADK エージェント）。サービスアカウント shogi-web
#   shogi-engine … 非公開（IAM 認証必須）。shogi-web だけが呼べる。サービスアカウント shogi-engine
#   Cloud Storage … 棋譜と結果を保存し、30日で自動削除
set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID を設定してください}"
REGION="${REGION:-asia-northeast1}"
REPO="${REPO:-shogi}"
BUCKET="${BUCKET:-${PROJECT_ID}-shogi-games}"
WEB_SA="shogi-web@${PROJECT_ID}.iam.gserviceaccount.com"
ENGINE_SA="shogi-engine@${PROJECT_ID}.iam.gserviceaccount.com"
IMG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"

gcloud config set project "$PROJECT_ID" >/dev/null

echo "== API を有効化"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com storage.googleapis.com logging.googleapis.com cloudtrace.googleapis.com

echo "== Artifact Registry"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION"

echo "== サービスアカウント（必要最小限の権限だけ付ける）"
for sa in shogi-web shogi-engine; do
  gcloud iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$sa" --display-name "$sa"
done
# web: Gemini（Vertex AI）を呼ぶ権限だけ。APIキーは使わない
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${WEB_SA}" \
  --role roles/aiplatform.user --condition None >/dev/null

echo "== Cloud Storage（棋譜と結果の games/ は30日で自動削除。利用者の一覧 auth/ は消さない）"
gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${BUCKET}" --location "$REGION" --uniform-bucket-level-access \
    --public-access-prevention
cat > /tmp/lifecycle.json <<'JSON'
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 30, "matchesPrefix": ["games/"]}}]}
JSON
gcloud storage buckets update "gs://${BUCKET}" --lifecycle-file /tmp/lifecycle.json
# web はこのバケットだけを読み書きできる（ログインの確認と、これまでの記録の集計で読む。集計の書き直しで上書きする）
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --member "serviceAccount:${WEB_SA}" \
  --role roles/storage.objectUser >/dev/null

echo "== Cloud Build の実行アカウントに、ビルドに要る権限だけ付ける"
# 新しいプロジェクトでは、Cloud Build が使う Compute Engine の既定サービスアカウントに権限が無く、
# ソースの取得で 403 になる。ソースの読み取り・イメージの書き込み・ログの書き込みだけを付ける
BUILD_SA="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')-compute@developer.gserviceaccount.com"
gcloud storage buckets describe "gs://${PROJECT_ID}_cloudbuild" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${PROJECT_ID}_cloudbuild" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding "gs://${PROJECT_ID}_cloudbuild" --member "serviceAccount:${BUILD_SA}" \
  --role roles/storage.objectViewer >/dev/null
gcloud artifacts repositories add-iam-policy-binding "$REPO" --location "$REGION" \
  --member "serviceAccount:${BUILD_SA}" --role roles/artifactregistry.writer >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${BUILD_SA}" \
  --role roles/logging.logWriter --condition None >/dev/null

echo "== イメージをビルド"
gcloud builds submit --config cloudbuild.yaml --substitutions "_REGION=${REGION},_REPO=${REPO}"

echo "== shogi-engine をデプロイ（非公開・1インスタンス1対局）"
gcloud run deploy shogi-engine --image "${IMG}/shogi-engine:latest" --region "$REGION" \
  --service-account "$ENGINE_SA" --no-allow-unauthenticated \
  --cpu 2 --memory 2Gi --concurrency 1 --min-instances 0 --max-instances 3 --timeout 900 \
  --set-env-vars ENGINE_THREADS=2,ENGINE_HASH_MB=256
ENGINE_URL="$(gcloud run services describe shogi-engine --region "$REGION" --format 'value(status.url)')"
# shogi-web のサービスアカウントだけが shogi-engine を呼べる
gcloud run services add-iam-policy-binding shogi-engine --region "$REGION" \
  --member "serviceAccount:${WEB_SA}" --role roles/run.invoker >/dev/null

echo "== shogi-web をデプロイ（公開）"
# Streamlit はアップロードした棋譜とダウンロード用のファイルをインスタンスのメモリに持つ。インスタンスが2つになると、
# 別のインスタンスに届いたアップロード（400）やPDFの取得（404）が失敗するので、1つに固定する
gcloud run deploy shogi-web --image "${IMG}/shogi-web:latest" --region "$REGION" \
  --service-account "$WEB_SA" --allow-unauthenticated --session-affinity \
  --cpu 1 --memory 1Gi --concurrency 80 --min-instances 0 --max-instances 1 --timeout 3600 \
  --set-env-vars "ENGINE_URL=${ENGINE_URL},BUCKET=${BUCKET},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=TRUE,COACH_MODEL=gemini-3.8-flash"

echo
echo "公開URL: $(gcloud run services describe shogi-web --region "$REGION" --format 'value(status.url)')"
echo "予算アラート（推奨）: コンソールの「お支払い › 予算とアラート」で \$50 / \$150 / \$300 の通知を作成"
