"""解析結果を見せる前の画面で使う、指し手一覧と局面図（エンジン不要）。

    python -m pytest tests/test_board_view.py
"""
import xml.dom.minidom

import cshogi
import pytest

from shogi_analyzer.kifu_analysis import load_kifu
from shogi_analyzer.service import board_after, board_svg, move_list


def test_move_list():
    ms = move_list(load_kifu("sample.kif"))
    assert len(ms) == 40 and ms[0] == {"ply": 1, "side": "▲", "move_jp": "２六歩(27)"}
    assert [m["side"] for m in ms[:4]] == ["▲", "△", "▲", "△"]


def test_board_svg():
    k = load_kifu("sample.kif")
    for ply in (0, 1, 15, 40):
        svg = board_svg(k, ply)
        xml.dom.minidom.parseString(svg)  # 壊れたSVGでないこと
        assert svg.startswith("<svg") and 'fill="#f5deb3"' in svg  # 暗い画面でも見える背景
    assert board_svg(k, 0).count("<use ") == 40  # 開始局面は駒が40枚
    assert "#f5deb3" not in board_svg(k, 0, background=None)  # 印刷用は背景を敷かない
    for ply in (-1, 41):
        with pytest.raises(ValueError):
            board_svg(k, ply)


def test_board_svg_highlights_selected_move():
    # 選んだ手（ply手目）を指した後の局面で、その手のマスに色が付く。開始局面には色が付かない
    k = load_kifu("sample.kif")
    plain = board_svg(k, 0).count("<rect")
    assert board_svg(k, 1).count("<rect") > plain
    assert board_svg(k, 1) != board_svg(k, 2)
    assert board_after(k, 0)[1] is None and board_after(k, 1)[1] == k.moves[0]
    assert cshogi.move_to_usi(board_after(k, 1)[1]) == "2g2f"  # 1手目 ２六歩(27)


def test_king_glyphs():
    """先手の玉は「王」、後手の玉は「玉」（局面図と指し手の表記。発案者の指示 2026-10-15）。"""
    import cshogi
    from shogi_analyzer.service import move_jp, to_svg
    svg = to_svg(cshogi.Board())
    assert '<g id="black-king"><text' in svg and "&#29579;" in svg.split('<g id="black-king">')[1].split("</g>")[0]
    assert "&#29577;" in svg.split('<g id="white-king"')[1].split("</g>")[0]
    b = cshogi.Board()
    assert move_jp(b, "5i5h") == "▲５八王"
    b.push_usi("5i5h")
    assert move_jp(b, "5a5b") == "△５二玉"
    assert move_jp(b, "7c7d") == "△７四歩"
