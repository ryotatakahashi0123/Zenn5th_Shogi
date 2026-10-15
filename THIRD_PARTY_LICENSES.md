# 使用しているソフトウェアとライセンス

確認日: 2026-10-12。各コンポーネントの最新の公式情報を必ず再確認してください。本一覧は法的助言ではありません。

| コンポーネント | バージョン | ライセンス／利用条件 | 入手元 | 本リポジトリへの同梱 |
| --- | --- | --- | --- | --- |
| やねうら王（探索エンジン） | git c1b80ea（V9.80git） | GPL-3.0（Stockfish・Apery 等の GPLv3 コードを含む） | https://github.com/yaneurao/YaneuraOu （Wiki: https://github.com/yaneurao/YaneuraOu/wiki ） | しない（Dockerビルド時にソースから取得・無改変でビルド） |
| 水匠5（NNUE評価関数, 作者: たややん氏 https://x.com/tayayan_ts ） | Suisho5.7z | 明示的なライセンス表記なし。作者了承のもとやねうら王公式Releasesで公開 | https://github.com/yaneurao/YaneuraOu/releases/tag/suisho5 | しない（ビルド時に公式URLから取得） |
| cshogi | 1.0.9 | GPL-3.0 | https://github.com/TadaoYamaoka/cshogi | pip 依存 |
| Streamlit | 1.65.0 | Apache-2.0 | https://github.com/streamlit/streamlit | pip 依存 |
| pandas | 3.0.5 | BSD-3-Clause | https://pandas.pydata.org | pip 依存 |
| Altair | 6.3.0 | BSD-3-Clause | https://github.com/vega/altair | pip 依存 |
| ReportLab | 5.0.1 | BSD-3-Clause | https://www.reportlab.com/opensource/ | pip 依存 |
| BIZ UDゴシック（フォント。PDFに埋め込む） | Regular / Bold | SIL Open Font License 1.1（`assets/fonts/OFL.txt`） | https://github.com/google/fonts/tree/main/ofl/bizudgothic | する（`assets/fonts/`。無改変） |
| Agent Development Kit (google-adk) | 2.11.0 | Apache-2.0 | https://github.com/google/adk-python | pip 依存 |
| google-cloud-storage / google-auth | 3.17.0 / 2.61.0 | Apache-2.0 | https://github.com/googleapis | pip 依存 |
| FastAPI / uvicorn / httpx | 0.141.1 / 0.53.0 / 0.28.1 | MIT / BSD-3-Clause / BSD-3-Clause | PyPI | pip 依存 |
| Gemini（Vertex AI） | gemini-3.8-flash | Google Cloud の利用規約と生成AIの追加規約に従う（ソフトウェアの同梱なし） | Vertex AI | API 利用 |

## 方針

- 本リポジトリの Python コードは GPL-3.0 で公開する（cshogi が GPL-3.0 のため）。
- エンジンとは標準入出力の USI プロトコルのみで通信し、エンジンのコードを Python に取り込まない。
- Web サービスとしての公開は GPL の「頒布」に当たらないとされるが、Docker イメージやバイナリを第三者に渡す場合は、やねうら王の対応ソース（上記コミット）を同時に提供する。
- 水匠5を商用サービス・製品で使う前に、作者に利用条件を確認する。確認できない場合は差し替えを検討する（例: やねうら王 README で権利不主張が明記された「リゼロ評価関数」）。
- やねうら王（GitHub・Wiki へのリンク、GPL-3.0）と水匠5（作者名・配布元）のクレジットを、Webサービスのフッター（`shogi_analyzer/credits.py`）と README に表示する。
- AGPL のコンポーネントを追加する場合は公開範囲が変わるため、追加前に必ず確認する。
