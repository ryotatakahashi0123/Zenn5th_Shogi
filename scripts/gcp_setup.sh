#!/usr/bin/env bash
# Google Cloud の初期設定(1回だけ)。実行前に `gcloud auth login` 済みであること。
# 使い方: PROJECT_ID=xxx ./scripts/gcp_setup.sh
# 引数名は gcloud 570.0.0 の --help と照合した。ただし実プロジェクトでは未実行(初回は1行ずつ確認しながら流す)。
set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID を指定する}"
REGION="${REGION:-asia-northeast1}"
SA_NAME="${SA_NAME:-furikaeri-run}"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
REPO="${REPO:-furikaeri}"

gcloud config set project "$PROJECT_ID"

echo "== API の有効化(請求先アカウントの紐づけが必要)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com aiplatform.googleapis.com secretmanager.googleapis.com

echo "== Artifact Registry"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION"

echo "== Firestore (Native)"
gcloud firestore databases describe >/dev/null 2>&1 || \
  gcloud firestore databases create --location="$REGION" --type=firestore-native

echo "== 実行用サービスアカウント(最小権限)"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name="furikaeri runtime"
for ROLE in roles/datastore.user roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$ROLE" --condition=None >/dev/null
done

echo "== Firestore TTL(games.expireAt)。子コレクション(reviews/runs)と quota は自動では消えない"
gcloud firestore fields ttls update expireAt --collection-group=games --enable-ttl --async || \
  echo "TTL設定に失敗。コンソールで設定する(docs/gcp_setup.md)"

echo "== Secret Manager"
mk_secret() { # 名前 値
  if gcloud secrets describe "$1" >/dev/null 2>&1; then
    echo "  $1 は既にある(値は変更しない。変える時は gcloud secrets versions add)"
  else
    printf '%s' "$2" | gcloud secrets create "$1" --replication-policy=automatic --data-file=-
  fi
  gcloud secrets add-iam-policy-binding "$1" --member="serviceAccount:$SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
}
SESSION_SECRET_VAL="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
mk_secret furikaeri-session-secret "$SESSION_SECRET_VAL"
read -r -p "デモ用メールアドレス(審査員に渡すもの): " DEMO_EMAIL_VAL
read -r -s -p "デモ用パスワード: " DEMO_PASSWORD_VAL; echo
mk_secret furikaeri-demo-email "$DEMO_EMAIL_VAL"
mk_secret furikaeri-demo-password "$DEMO_PASSWORD_VAL"

echo
echo "完了。次: ./scripts/deploy.sh   (予算アラートは docs/gcp_setup.md の手順で請求アカウント側に設定する)"
