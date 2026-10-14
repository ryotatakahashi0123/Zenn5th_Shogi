"""悔しかった手の印とエンジン判定の突き合わせ（エンジン不要）。

    python -m pytest tests/test_regret.py
"""
import pytest

from shogi_analyzer.kifu_analysis import GOOD_GAP, is_good
from shogi_analyzer.regret import MAX_NOTE, SURPRISE, clean_mark, compare, kind_label, verdict


def _row(ply, label="", drop=0.0, move="7g7f", best="7g7f"):
    return {"ply": ply, "side": "▲" if ply % 2 else "△", "move_usi": move, "move_jp": f"手{ply}",
            "label": label, "winrate_drop": drop, "best_before_usi": best, "best_before_jp": "最善"}


ROWS = [_row(1), _row(2, "緩手", 0.06, best="3c3d"), _row(3, "悪手", 0.3, best="2g2f"),
        _row(4), _row(5, "疑問手", 0.12, best="2g2f"), _row(6)]


def test_three_groups():
    marks = [clean_mark(3, ["読み抜け"], "", 6), clean_mark(1, ["焦り・早指し", "手拍子"], "あせった", 6)]
    g = compare(ROWS, marks, "▲")
    assert [m["ply"] for m in g["match"]] == [3] and g["match"][0]["tags"] == ["読み抜け"]
    assert [m["ply"] for m in g["feeling_only"]] == [1]
    assert g["feeling_only"][0]["was_best"] and g["feeling_only"][0]["note"] == "あせった"
    assert [m["ply"] for m in g["data_only"]] == [5]  # 相手(△)の2手目は入らない


def test_side_and_opponent_move():
    # 後手として見る。相手(▲)の手への印は「驚いた手」で、悔しかった手の区分には入らない（2026-10-14 の変更。以前は一致）
    g = compare(ROWS, [clean_mark(3, ["相手の好手に気づかなかった"], "", 6), clean_mark(2, ["読み抜け"], "", 6)], "△")
    assert [m["ply"] for m in g["surprise"]] == [3] and g["surprise"][0]["label"] == "悪手"
    assert [m["ply"] for m in g["match"]] == [2] and g["data_only"] == []
    # 手番を変えると、同じ印の呼び名と区分が入れ替わる
    g2 = compare(ROWS, [clean_mark(3, ["読み抜け"], "", 6)], "▲")
    assert [m["ply"] for m in g2["match"]] == [3] and g2["surprise"] == []
    assert kind_label("regret", "▲", "△") == SURPRISE and kind_label("regret", "△", "△") == "悔しかった手"
    assert kind_label("good", "▲", "△") == "好手"


def test_surprise_mark_input():
    # 驚いた手は原因が任意。悔しかった手は原因が必須のまま
    assert clean_mark(2, [], "まさか", 6, surprise=True)["tags"] == []
    with pytest.raises(ValueError):
        clean_mark(2, [], "", 6)
    # 迷った別の読みを持てる。無ければ項目を作らない（古い印と同じ形）
    assert clean_mark(1, ["その他"], "", 6, alts=[["2g2f", "8c8d"]])["alts"] == [["2g2f", "8c8d"]]
    assert "alts" not in clean_mark(1, ["その他"], "", 6)
    g = compare(ROWS, [clean_mark(3, ["読み抜け"], "", 6, alts=[["2g2f"]])], "▲",
                {3: [{"line": ["2g2f"], "final_winrate": 0.6}]})
    assert g["match"][0]["alts"][0]["final_winrate"] == 0.6


def test_no_marks_and_missing_row():
    g = compare(ROWS, [], "▲")
    assert not g["match"] and not g["feeling_only"] and [m["ply"] for m in g["data_only"]] == [3, 5]
    # 解析の行が無い手の印は捨てる
    assert compare(ROWS[:2], [clean_mark(6, ["その他"], "", 6)], "▲")["feeling_only"] == []


def test_clean_mark_rejects_bad_input():
    for ply in (0, 7):
        with pytest.raises(ValueError):
            clean_mark(ply, ["その他"], "", 6)
    with pytest.raises(ValueError):
        clean_mark(1, ["存在しない原因"], "", 6)
    with pytest.raises(ValueError):
        clean_mark(1, [], "", 6)
    assert len(clean_mark(1, ["その他"], "あ" * 500, 6)["note"]) == MAX_NOTE
    assert clean_mark(1, ["その他"], " 改行\nを\n消す ", 6)["note"] == "改行 を 消す"


def _good(ply, gap=0.3):
    return {**_row(ply), "good": True, "good_gap": gap}


def test_good_marks():
    # 1・4・6手目は最善手と同じ。うち 1（▲）と 4・6（△）はエンジンの好手候補。3手目は悪手
    rows = [_good(1), _row(2, "緩手", 0.06, best="3c3d"), _row(3, "悪手", 0.3, best="2g2f"), _good(4), _row(5), _good(6)]
    marks = [clean_mark(5, [], "うまく受けた", 6, kind="good"), clean_mark(3, ["読み抜け"], "", 6, kind="good"),
             clean_mark(2, ["読み抜け"], "", 6)]
    g = compare(rows, marks, "▲")
    assert [m["ply"] for m in g["good_match"]] == [5] and g["good_match"][0]["note"] == "うまく受けた"
    assert [m["ply"] for m in g["good_feeling_only"]] == [3] and "tags" not in g["good_feeling_only"][0]
    assert [m["ply"] for m in g["good_data_only"]] == [1]
    assert [m["ply"] for m in g["good_opponent"]] == [4, 6]
    # 好手の印は、悔しかった手の突き合わせに入らない。好手だと思った悪手は「データだけ」に残る
    # 相手(△)の2手目への印は「驚いた手」（2026-10-14 の変更。以前は一致）
    assert g["match"] == [] and [m["ply"] for m in g["surprise"]] == [2] and [m["ply"] for m in g["data_only"]] == [3]
    assert [verdict(r) for r in rows[:5]] == ["好手候補", "緩手", "悪手", "好手候補", "最善手と同じ"]
    assert verdict(g["good_feeling_only"][0]) == "悪手"
    # 好手の印を付けた好手候補は「データだけ」から外れる
    g2 = compare(rows, [clean_mark(1, [], "", 6, kind="good"), clean_mark(4, [], "", 6, kind="good")], "▲")
    assert g2["good_data_only"] == [] and [m["ply"] for m in g2["good_opponent"]] == [6]
    assert [m["ply"] for m in g2["good_match"]] == [1, 4]
    # 好手の欄が無い古い行でも動く
    assert compare(ROWS, [], "▲")["good_data_only"] == []


def test_good_mark_input_and_threshold():
    m = clean_mark(1, ["読み抜け"], " よい\n手 ", 6, kind="good")
    assert m == {"ply": 1, "kind": "good", "tags": [], "note": "よい 手"}  # 好手は原因を持たない
    assert clean_mark(1, ["その他"], "", 6)["kind"] == "regret"
    with pytest.raises(ValueError):
        clean_mark(1, [], "", 6, kind="other")
    # 好手候補は、最善手を指し、次善手との勝率の差がしきい値以上
    assert is_good(True, GOOD_GAP) and not is_good(True, GOOD_GAP - 0.001) and not is_good(False, 0.9)
