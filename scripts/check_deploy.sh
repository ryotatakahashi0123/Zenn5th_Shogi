#!/usr/bin/env bash
# 出した後の疎通確認。使い方: DEMO_EMAIL=.. DEMO_PASSWORD=.. ./scripts/check_deploy.sh https://xxx.run.app
set -euo pipefail
URL="${1:?URLを指定する}"; : "${DEMO_EMAIL:?}" "${DEMO_PASSWORD:?}"
J="$(mktemp)"; trap 'rm -f "$J"' EXIT
ok() { echo "OK  $1"; }; ng() { echo "NG  $1"; exit 1; }

[ "$(curl -s "$URL/healthz")" = '{"status":"ok"}' ] && ok healthz || ng healthz
[ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/api/games")" = 401 ] && ok "未ログインは401" || ng "未ログインの扱い"
curl -sf -c "$J" -H 'content-type: application/json' \
  -d "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASSWORD\"}" "$URL/api/login" >/dev/null && ok login || ng login
curl -sf -b "$J" "$URL/api/games" | grep -q sample-sample01 && ok "サンプル局が見える(Firestore読み書き)" || ng "サンプル局"
R="$(curl -sf -b "$J" -X POST "$URL/api/games/sample-sample01/agent/questions")" && ok "agent/questions" || ng "agent/questions"
echo "$R" | python3 -c 'import sys,json;r=json.load(sys.stdin);print("   degraded =",r["guardrail"]["degraded"],"| reasons =",r["guardrail"]["reasons"])'
echo "(degraded = True ならGeminiの呼び出しに失敗している。Cloud Runのログで llm_error を探す)"
curl -sf -b "$J" -X POST "$URL/api/games/sample-sample01/agent/report" >/dev/null && ok "agent/report" || ng "agent/report"
curl -sf -b "$J" -o "$J.pdf" "$URL/api/games/sample-sample01/report.pdf" && head -c5 "$J.pdf" | grep -q '%PDF-' && ok PDF || ng PDF
rm -f "$J.pdf"
