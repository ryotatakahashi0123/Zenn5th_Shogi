# API(Cookie認証。`/api/docs` は開発時のみ)

| メソッド | パス | 内容 |
|---|---|---|
| POST | /api/login, /api/logout | 認証 |
| GET | /api/me, /api/config | 状態・設定(原因タグなど) |
| POST/GET | /api/games | 棋譜の登録(`{kif, mySide?, title?}`) / 一覧 |
| GET/DELETE | /api/games/{id} | 取得 / 削除(サンプルは403) |
| DELETE | /api/games | 自分の対局を全削除(サンプルを除く) |
| GET | /api/games/{id}/positions, /board/{n}.svg | 局面(JSON)/ 盤面SVG |
| GET/PUT/DELETE | /api/games/{id}/marks[/{n}] | 本人の印 |
| POST | /api/games/{id}/agent/questions | 実行1: 候補印 |
| POST | /api/games/{id}/agent/report | 実行2: レポート |
| GET | /api/games/{id}/report, /runs, /report.pdf | 結果・実行記録・PDF(レポート未作成は409) |
| GET | /api/knowledge | 知識カード |
| GET | /healthz | 死活監視 |

他人の対局は存在しないものとして404。1日の実行回数は `DAILY_RUN_LIMIT`(既定30)。
