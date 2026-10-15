"""6つのラベルの印とエンジン判定の突き合わせ（エンジン不要）。

    python -m pytest tests/test_regret.py
"""
import pytest

from shogi_analyzer.kifu_analysis import GOOD_GAP, is_good
from shogi_analyzer.regret import (GROUP_KEYS, MAX_NOTE, SECTIONS, clean_mark, compare, has_marks, kind_label, mark_title,
                                   sections, verdict)


def _row(ply, label="", drop=0.0, move="7g7f", best="7g7f"):
    return {"ply": ply, "side": "▲" if ply % 2 else "△", "move_usi": move, "move_jp": f"手{ply}",
            "label": label, "winrate_drop": drop, "best_before_usi": best, "best_before_jp": "最善"}


ROWS = [_row(1), _row(2, "緩手", 0.06, best="3c3d"), _row(3, "悪手", 0.3, best="2g2f"),
        _row(4), _row(5, "疑問手", 0.12, best="2g2f"), _row(6)]


def _plies(g, key):
    return [m["ply"] for m in g[key]]


def test_my_bad_groups():
    marks = [clean_mark(3, ["読み抜け"], "", 6), clean_mark(1, ["焦り・早指し", "手拍子"], "あせった", 6)]
    g = compare(ROWS, marks, "▲")
    assert _plies(g, "match") == [3] and g["match"][0]["tags"] == ["読み抜け"]
    assert _plies(g, "feeling_only") == [1]
    assert g["feeling_only"][0]["was_best"] and g["feeling_only"][0]["note"] == "あせった"
    assert _plies(g, "data_only") == [5]  # 相手(△)の2手目は入らない
    assert _plies(g, "opp_data_only") == [2]  # 相手の悪手候補で、印を付けていない（咎め損ねたかもしれない手）


def test_side_decides_label():
    # 後手として見る。▲の手は相手の手なので、同じ bad の印でも「相手の手・悪手だと思った」になる
    marks = [clean_mark(3, [], "", 6), clean_mark(1, [], "", 6), clean_mark(2, [], "", 6, kind="other"),
             clean_mark(5, [], "まさか", 6, kind="other")]
    g = compare(ROWS, marks, "△")
    assert _plies(g, "opp_match") == [3] and _plies(g, "opp_feeling_only") == [1] and _plies(g, "opp_data_only") == [5]
    assert _plies(g, "hesitate") == [2] and _plies(g, "surprise") == [5] and g["surprise"][0]["label"] == "疑問手"
    assert _plies(g, "data_only") == [2]  # 迷った印は、悪手だと思った印ではない
    assert g["match"] == [] and g["feeling_only"] == []
    # 手番を変えると、同じ印の呼び名と区分が入れ替わる
    g2 = compare(ROWS, marks, "▲")
    assert _plies(g2, "match") == [3] and _plies(g2, "hesitate") == [5] and _plies(g2, "surprise") == [2]
    assert kind_label("other", "▲", "△") == "予想外で驚いた" and kind_label("other", "△", "△") == "ほかにも指す手があった"
    assert kind_label("bad", "▲", "△") == kind_label("bad", "△", "△") == "悪手だと思った"
    assert mark_title("good", "▲", "△") == "相手の手・好手だと思った"


def test_legacy_regret_marks():
    # 以前の regret の印: 自分の手なら「悪手だと思った」、相手の手なら「予想外で驚いた」として読む
    marks = [{"ply": 3, "kind": "regret", "tags": ["読み抜け"], "note": ""}, {"ply": 2, "tags": [], "note": ""}]
    g = compare(ROWS, marks, "▲")
    assert _plies(g, "match") == [3] and _plies(g, "surprise") == [2] and g["opp_match"] == []


def test_mark_input():
    # 原因はどのラベルでも任意。好手にも付けられる
    assert clean_mark(2, [], "まさか", 6, kind="other")["tags"] == []
    assert clean_mark(2, [], "", 6)["kind"] == "bad"
    assert clean_mark(1, ["読み抜け"], " よい\n手 ", 6, kind="good") == {"ply": 1, "kind": "good", "tags": ["読み抜け"],
                                                                        "note": "よい 手"}
    # 迷った別の読みを持てる。無ければ項目を作らない（古い印と同じ形）
    assert clean_mark(1, ["その他"], "", 6, alts=[["2g2f", "8c8d"]])["alts"] == [["2g2f", "8c8d"]]
    assert "alts" not in clean_mark(1, ["その他"], "", 6)
    g = compare(ROWS, [clean_mark(3, [], "", 6, kind="other", alts=[["2g2f"]])], "▲",
                {3: [{"line": ["2g2f"], "final_winrate": 0.6}]})
    assert g["hesitate"][0]["alts"][0]["final_winrate"] == 0.6


def test_no_marks_and_missing_row():
    g = compare(ROWS, [], "▲")
    assert not g["match"] and not g["feeling_only"] and _plies(g, "data_only") == [3, 5] and not has_marks(g)
    # 解析の行が無い手の印は捨てる
    assert compare(ROWS[:2], [clean_mark(6, ["その他"], "", 6)], "▲")["feeling_only"] == []
    assert set(GROUP_KEYS) <= set(g) and len(SECTIONS) == 6


def test_clean_mark_rejects_bad_input():
    for ply in (0, 7):
        with pytest.raises(ValueError):
            clean_mark(ply, ["その他"], "", 6)
    with pytest.raises(ValueError):
        clean_mark(1, ["存在しない原因"], "", 6)
    for kind in ("regret", "surprise", "x"):  # 新しく付ける印は bad / other / good だけ
        with pytest.raises(ValueError):
            clean_mark(1, [], "", 6, kind=kind)
    assert len(clean_mark(1, ["その他"], "あ" * 500, 6)["note"]) == MAX_NOTE
    assert clean_mark(1, ["その他"], " 改行\nを\n消す ", 6)["note"] == "改行 を 消す"


def _good(ply, gap=0.3):
    return {**_row(ply), "good": True, "good_gap": gap}


def test_good_marks():
    # 1・4・6手目は最善手と同じ。うち 1（▲）と 4・6（△）はエンジンの好手候補。3手目は悪手
    rows = [_good(1), _row(2, "緩手", 0.06, best="3c3d"), _row(3, "悪手", 0.3, best="2g2f"), _good(4), _row(5), _good(6)]
    marks = [clean_mark(5, [], "うまく受けた", 6, kind="good"), clean_mark(3, ["読み抜け"], "", 6, kind="good"),
             clean_mark(2, [], "", 6, kind="good"), clean_mark(4, [], "", 6, kind="good")]
    g = compare(rows, marks, "▲")
    assert _plies(g, "good_match") == [5] and g["good_match"][0]["note"] == "うまく受けた"
    assert _plies(g, "good_feeling_only") == [3] and g["good_feeling_only"][0]["tags"] == ["読み抜け"]
    assert _plies(g, "good_data_only") == [1]
    # 相手(△)の手への好手の印は、相手の手のラベルに入る
    assert _plies(g, "opp_good_match") == [4] and _plies(g, "opp_good_feeling_only") == [2]
    assert _plies(g, "good_opponent") == [6]
    # 好手だと思った悪手は「データだけ」に残る
    assert g["match"] == [] and _plies(g, "data_only") == [3] and _plies(g, "opp_data_only") == [2]
    assert [verdict(r) for r in rows[:5]] == ["好手候補", "緩手", "悪手", "好手候補", "最善手と同じ"]
    assert verdict(g["good_feeling_only"][0]) == "悪手"
    # 好手の欄が無い古い行でも動く
    assert compare(ROWS, [], "▲")["good_data_only"] == []
    # 好手候補は、最善手を指し、次善手との勝率の差がしきい値以上
    assert is_good(True, GOOD_GAP) and not is_good(True, GOOD_GAP - 0.001) and not is_good(False, 0.9)


def test_sections():
    marks = [clean_mark(3, [], "", 6), clean_mark(2, [], "", 6, kind="other")]
    secs = sections(compare(ROWS, marks, "▲"))
    assert [s["title"] for s in secs] == ["自分の手・悪手だと思った", "自分の手・ほかにも指す手があった", "自分の手・好手だと思った",
                                         "相手の手・悪手だと思った", "相手の手・予想外で驚いた", "相手の手・好手だと思った"]
    assert [(m["ply"], m["group"]) for m in secs[0]["items"]] == [(3, "一致"), (5, "データだけ")]
    assert secs[0]["counts"] == [("一致", 1), ("感覚だけ", 0), ("データだけ", 1)]
    assert secs[1]["counts"] == [] and secs[4]["items"][0]["group"] == "" and secs[4]["items"][0]["ply"] == 2


def test_norm_kind_reads_labels():
    """画面の欄が呼び名を返しても、印の種類に直す（手番を変えたあとに「予想外で驚いた」が残って止まった不具合）。"""
    from shogi_analyzer.regret import norm_kind
    assert norm_kind("予想外で驚いた", False) == "other"
    assert norm_kind("ほかにも指す手があった", True) == "other"
    assert norm_kind("悪手だと思った", True) == "bad"
    assert norm_kind("好手だと思った", False) == "good"
    assert norm_kind("good", True) == "good"
    assert norm_kind(None, True) == "bad" and norm_kind("regret", False) == "other"
