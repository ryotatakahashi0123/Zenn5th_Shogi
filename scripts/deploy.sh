#!/usr/bin/env bash
# ソースからビルドして Cloud Run に出す。使い方: PROJECT_ID=xxx ./scripts/deploy.sh
set -euo pipefail
: "${PROJECT_ID:?PROJECT_ID を指定する}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-furikaeri}"
SA="${SA_NAME:-furikaeri-run}@${PROJECT_ID}.iam.gserviceaccount.com"
MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"   # 候補。提供状況は未確認。失敗したら公式で確認して差し替える

cd "$(dirname "$0")/.."
gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" --source . \
  --service-account "$SA" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 3 --memory 512Mi --timeout 120 \
  --set-env-vars "STORE=firestore,BRAIN=gemini,GEMINI_MODEL=${MODEL},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},GOOGLE_GENAI_USE_VERTEXAI=true,SEED_DEMO=1,APP_ENV=production" \
  --set-secrets "SESSION_SECRET=furikaeri-session-secret:latest,DEMO_EMAIL=furikaeri-demo-email:latest,DEMO_PASSWORD=furikaeri-demo-password:latest"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
echo "URL: $URL"
echo "確認: ./scripts/check_deploy.sh $URL"
