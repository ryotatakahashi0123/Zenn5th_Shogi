"""盤面での棋譜入力（エンジン不要）。

    python -m pytest tests/test_entry.py
"""
import cshogi
import pytest

from shogi_analyzer import entry
from shogi_analyzer.kifu_analysis import detect_format, load_kifu, load_kifu_text


def test_select_and_move():
    b, jp = entry.replay([])
    assert jp == [] and "7g" in entry.origins(b) and "3c" not in entry.origins(b)  # 相手の駒は選べない
    assert entry.targets(b, "7g") == {"7f"} and entry.choose(b, "7g", "7f") == ["7g7f"]
    assert entry.choose(b, "7g", "7e") == []  # 指せない行き先


def test_promotion_and_drop():
    # 角交換: ▲７六歩 △３四歩 ▲２二角成（成る・成らないの両方が指せる）
    b, jp = entry.replay(["7g7f", "3c3d"])
    assert entry.choose(b, "8h", "2b") == ["8h2b", "8h2b+"]
    b, jp = entry.replay(["7g7f", "3c3d", "8h2b+", "3a2b"])
    assert jp == ["▲７六歩", "△３四歩", "▲２二角成", "△２二銀"]
    assert entry.hand(b, 0) == [("B*", "角", 1)] and entry.hand(b, 1) == [("B*", "角", 1)]
    assert "B*" in entry.origins(b) and entry.choose(b, "B*", "5e") == ["B*5e"]
    assert entry.choose(b, "B*", "7f") == []  # 駒のあるマスには打てない
    with pytest.raises(ValueError, match="3手目"):
        entry.replay(["7g7f", "3c3d", "7g7f"])


def test_entered_moves_load_like_a_file():
    moves = [cshogi.move_to_usi(m) for m in load_kifu("sample.kif").moves]
    text = entry.to_csa(moves)
    assert detect_format(text) == "csa" and load_kifu_text(text).moves == load_kifu("sample.kif").moves
    # 投了で終えた棋譜も、同じ手順として読める
    assert entry.to_csa(moves, resign=True).endswith("%TORYO\n")
    assert load_kifu_text(entry.to_csa(moves, resign=True)).moves == load_kifu("sample.kif").moves
    with pytest.raises(ValueError, match="指し手を1つも"):
        load_kifu_text(entry.to_csa([]))  # 1手も指していなければ棋譜にならない


def test_click_sequence():
    # 駒を選ぶ → 行き先を押す。相手の駒・動かせない場所は選べない
    assert entry.click([], None, "7g") == ([], "7g", None)
    assert entry.click([], None, "3c") == ([], None, None)
    assert entry.click([], "7g", "7f") == (["7g7f"], None, None)
    assert entry.click([], "7g", "2g") == ([], "2g", None)   # 別の自分の駒を押したら選び直す
    assert entry.click([], "7g", "5e") == ([], None, None)   # 指せない行き先は選択を外す
    # 成る・成らないの両方があるときは、手を進めずに候補を返す
    m = ["7g7f", "3c3d"]
    assert entry.click(m, "8h", "2b") == (m, "8h", ["8h2b", "8h2b+"])
    # 持ち駒を選んで打つ
    m = ["7g7f", "3c3d", "8h2b+", "3a2b"]
    assert entry.click(m, None, "B*") == (m, "B*", None)
    assert entry.click(m, "B*", "5e") == (m + ["B*5e"], None, None)


def test_board_svg_marks_selection():
    import xml.dom.minidom
    b, _ = entry.replay(["7g7f", "3c3d"])
    plain = entry.board_svg(b)
    svg = entry.board_svg(b, sel="8h", goals=entry.targets(b, "8h"))
    xml.dom.minidom.parseString(svg)
    assert svg.startswith("<svg") and 'fill="#f5deb3"' in svg
    assert svg.count("<rect") - plain.count("<rect") == 1 + len(entry.targets(b, "8h"))
    # ８八（8h）は左から2列目・上から8段目
    g = entry.GEOM
    assert f'x="{g["x"] + 20}" y="{g["y"] + 7 * 20}" width="20" height="20" fill="#f08a5d"' in svg
    # 直前の手の色が付いていても壊れない。持ち駒を選んでいるときは、マスの色は行き先だけ
    b2, _ = entry.replay(["7g7f", "3c3d", "8h2b+", "3a2b"])
    last = entry.replay(["7g7f", "3c3d", "8h2b+"])[0].move_from_usi("3a2b")
    svg2 = entry.board_svg(b2, last=last, sel="B*", goals={"5e"})
    xml.dom.minidom.parseString(svg2)
    assert "#f08a5d" not in svg2 and svg2.count("#b9d98a") == 1
    assert svg2.index("#f6b94d") < svg2.index("#b9d98a")  # 選択の色は、直前の手の色より上に重なる
