"""使っている将棋エンジン・評価関数のクレジット（画面のフッターと README で同じ内容を出す）。エンジンもLLMも呼ばない。

やねうら王は GPL-3.0。GitHub のページと Wiki へのリンク、ライセンス名、無改変で使っていることを示す。
水匠5は明示的なライセンス表記が無いので、作者名と配布元（やねうら王の公式 Releases）を示す。
画面に規則を書かず、ここに置く（別の画面・アプリからも同じ表記を使えるように）。
"""
from __future__ import annotations

YANEURAOU_GITHUB = "https://github.com/yaneurao/YaneuraOu"
YANEURAOU_WIKI = "https://github.com/yaneurao/YaneuraOu/wiki"
YANEURAOU_COMMIT = "c1b80ea"  # engine_service/Dockerfile・Dockerfile の YANEURAOU_COMMIT と合わせる
SUISHO5_RELEASE = "https://github.com/yaneurao/YaneuraOu/releases/tag/suisho5"
SUISHO_AUTHOR_X = "https://x.com/tayayan_ts"
SOURCE_URL = "https://github.com/ryotatakahashi0123/Zenn5th_Shogi"
THIRD_PARTY_URL = SOURCE_URL + "/blob/main/THIRD_PARTY_LICENSES.md"


def footer_markdown() -> str:
    """画面のフッターに出す文（Markdown）。"""
    return (
        "**将棋エンジン**: [やねうら王](" + YANEURAOU_GITHUB + ")（作者: やねうらお氏 / [GitHub](" + YANEURAOU_GITHUB
        + ") / [Wiki](" + YANEURAOU_WIKI + ")、GPL-3.0、" + YANEURAOU_COMMIT + " を無改変でビルド）　"
        "**評価関数**: [水匠5](" + SUISHO5_RELEASE + ")（作者: [たややん氏](" + SUISHO_AUTHOR_X + ")、やねうら王の公式 Releases から取得）  \n"
        "このサービスは、やねうら王・水匠の作者による公式のものではありません。エンジンの評価は参考値です。"
        "このサービスのソースコード（GPL-3.0）は [GitHub](" + SOURCE_URL + ") で公開しています"
        "（ほかのソフトウェアのライセンスは [THIRD_PARTY_LICENSES.md](" + THIRD_PARTY_URL + ")）。"
    )
