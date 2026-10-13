"""棋譜の読み込み（ファイル・貼り付けたテキスト）と、読み筋の日本語化。エンジン不要。

    python -m pytest tests/test_kifu_input.py
"""
import pytest

from shogi_analyzer.kifu_analysis import MAX_PLIES, decode_kifu, detect_format, load_kifu, load_kifu_text
from shogi_analyzer.service import lines_jp

KIF = "手数----指し手---------消費時間--\n   1 ７六歩(77)\n   2 ３四歩(33)\n   3 投了\n"
CSA = "V2.2\nPI\n+\n+7776FU\n-3334FU\n%TORYO\n"


def test_pasted_text_is_detected_and_loaded():
    assert detect_format(KIF) == "kif" and detect_format(CSA) == "csa"
    assert len(load_kifu_text(KIF).moves) == 2 and len(load_kifu_text(CSA).moves) == 2
    # 貼り付けは拡張子が無いので内容から見分ける。ファイルは拡張子を優先する
    assert len(load_kifu(CSA.encode(), "kifu.txt").moves) == 2
    assert len(load_kifu(KIF.encode("cp932"), "game.KIF").moves) == 2
    # ファイルから読んだ文字列をそのまま貼り付けても同じ棋譜になる
    text = decode_kifu(open("sample.kif", "rb").read())
    assert load_kifu_text(text).moves == load_kifu("sample.kif").moves


@pytest.mark.parametrize("text, word", [
    ("", "空"), ("  \n ", "空"),
    ("hello world", "指し手を1つも"),                 # 棋譜でない文章
    ("▲７六歩 △３四歩 ▲２六歩", "指し手を1つも"),     # KI2 形式は未対応
    ("V2.2\ngarbage\n", "読み取れませんでした"),       # 壊れた CSA
    ("手数----指し手--\n   1 ５五歩(57)\n", "不正な手"),  # 指せない手
])
def test_bad_kifu_is_rejected(text, word):
    with pytest.raises(ValueError, match=word):
        load_kifu_text(text)


def test_bad_file_is_rejected():
    with pytest.raises(ValueError, match="文字コード"):
        load_kifu(bytes(range(256)), "image.kif")  # 文字として読めないファイル
    with pytest.raises(ValueError, match="指し手を1つも"):
        load_kifu(b"%PDF-1.4 not a kifu", "note.txt")
    # 長すぎる棋譜（飛車を往復させて MAX_PLIES を超える）
    seq = ["１八飛(28)", "９二飛(82)", "２八飛(18)", "８二飛(92)"]
    long = "手数----指し手--\n" + "".join(f"{i + 1} {seq[i % 4]}\n" for i in range(MAX_PLIES + 2))
    with pytest.raises(ValueError, match="超える"):
        load_kifu_text(long)


def test_lines_jp():
    k = load_kifu("sample.kif")  # 15手目は ▲６六角(88)。直前の局面で ▲３四飛(2d3d) が指せる
    row = {"ply": 15, "move_usi": "8h6f", "best_pv": "2d3d 8f7f", "pv": "8f8i+ 6f2b+"}
    assert lines_jp(k, row) == {"best": ["▲３四飛", "△７六飛"], "actual": ["▲６六角", "△８九飛成", "▲２二角成"]}
    # 局面に合わない手は、そこで打ち切る。読み筋が無い行は空
    assert lines_jp(k, {**row, "best_pv": "2d3d 5a5e", "pv": ""}) == {"best": ["▲３四飛"], "actual": []}
    assert lines_jp(k, {"ply": 1, "move_usi": "2g2f"}) == {"best": [], "actual": []}
