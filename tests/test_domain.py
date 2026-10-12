import pytest

from app.domain.anonymize import anonymize_kif
from app.domain.board import build_positions, initial_position
from app.domain.board_svg import render_board_svg
from app.domain.compare import compare
from app.domain.kif_parser import KifError, decode_kif_bytes, parse_kif
from app.domain.problems import build_thinking_problems
from app.domain import time_analysis as ta
from .conftest import read_sample


def doc(kif):
    g = parse_kif(kif)
    moves = [m.to_dict() for m in g.moves]
    return g, moves, ta.analyze(moves, g.time_limit_sec)


# ---- パーサ(07 T-01, T-14, T-16) ----
@pytest.mark.parametrize("crlf", [True, False])
def test_sample01_parse(crlf):
    g = parse_kif(read_sample("sample01.kif", crlf))
    assert len(g.moves) == 100
    assert g.result == {"n": 101, "word": "投了", "loser": "b", "winner": "w"}
    assert g.time_limit_sec == 600 and g.time_rule == "sudden_death"
    assert g.meta["手合割"] == "平手"
    assert g.warnings == []


@pytest.mark.parametrize("crlf", [True, False])
def test_sample02_parse(crlf):
    g = parse_kif(read_sample("sample02.kif", crlf))
    assert len(g.moves) == 85
    assert g.result == {"n": 86, "word": "投了", "loser": "w", "winner": "b"}


def test_cumulative_matches_spent(s01, s02):
    for kif in (s01, s02):
        g = parse_kif(kif)
        for side in ("b", "w"):
            run = 0
            for m in g.moves:
                if m.side == side:
                    run += m.spent_sec
                    assert run == m.cum_sec


def test_drop_has_no_from_and_end_word_not_a_move(s01):
    g = parse_kif(s01)
    assert g.moves[8].text == "８七歩打"
    assert all(m.text != "投了" for m in g.moves)


@pytest.mark.parametrize("bad", ["", "   \n\n", None])
def test_empty_input(bad):
    with pytest.raises(KifError):
        parse_kif(bad)


def test_no_moves_is_error():
    with pytest.raises(KifError):
        parse_kif("先手：A\n後手：B\n")


def test_broken_line_goes_to_warnings():
    kif = "手数----指手---------消費時間--\n1 ７六歩(77)   ( 0:01/00:00:01)\nゴミ行です\n2 ３四歩(33)   ( 0:02/00:00:02)\n"
    g = parse_kif(kif)
    assert len(g.moves) == 2 and len(g.warnings) == 1


def test_no_time_columns():
    g = parse_kif("手数----指手---------消費時間--\n1 ７六歩(77)\n2 ３四歩(33)\n")
    assert g.moves[0].spent_sec is None
    a = ta.analyze([m.to_dict() for m in g.moves], None)
    assert a["longThinks"] == [] and a["timeStats"]["median"] is None


def test_eval_comment_parsed_when_present():
    kif = "手数----指手---------消費時間--\n1 ７六歩(77)   ( 0:01/00:00:01)\n*評価値 55\n"
    g = parse_kif(kif)
    assert g.moves[0].eval_cp == 55


def test_variation_ignored():
    kif = ("手数----指手---------消費時間--\n1 ７六歩(77)   ( 0:01/00:00:01)\n\n変化：1手\n"
           "   1 ２六歩(27)   ( 0:01/00:00:01)\n")
    g = parse_kif(kif)
    assert len(g.moves) == 1 and g.warnings


def test_decode_sjis_and_bom():
    s = "先手：テスト"
    assert decode_kif_bytes(s.encode("cp932")) == s
    assert decode_kif_bytes(b"\xef\xbb\xbf" + s.encode("utf-8")) == s


# ---- 時間分析(07 T-08, T-13、08の3章) ----
def test_long_thinks_sample01(s01):
    _, _, a = doc(s01)
    assert [x["n"] for x in a["longThinks"]] == [55, 57, 58, 77, 93]
    assert a["longThreshold"] == 30 and a["timeStats"]["median"] == 4
    assert a["totalSec"] == {"b": 594, "w": 326}
    assert a["remainingEnd"] == {"b": 6, "w": 274}
    assert a["lowRemaining"]["b"] == 79
    assert a["phaseShare"]["b"] == [8, 60, 32]
    assert a["evalAvailable"] is False and a["swings"] == []


def test_long_thinks_sample02(s02):
    _, moves, a = doc(s02)
    assert [x["n"] for x in a["longThinks"]] == [59, 68, 74, 76]
    assert a["remainingEnd"]["b"] == 325
    assert a["lowRemaining"]["b"] is None
    assert a["phaseShare"]["b"] == [19, 63, 18]
    m68 = next(m for m in moves if m["n"] == 68)
    assert m68["spentSec"] == 30  # 閾値ちょうど(境界)


def _mv(n, spent):
    return {"n": n, "side": "b" if n % 2 else "w", "spentSec": spent, "cumSec": None, "evalCp": None}


def test_long_boundary():
    base = [_mv(i, 1) for i in range(1, 30)]
    assert ta.long_thinks(base + [_mv(30, 29)]) == []
    assert [x["n"] for x in ta.long_thinks(base + [_mv(30, 30)])] == [30]
    assert [x["n"] for x in ta.long_thinks(base + [_mv(30, 31)])] == [30]


def test_all_equal_times():
    ms = [_mv(i, 5) for i in range(1, 11)]
    assert ta.long_thinks(ms) == []  # 閾値は max(30, 20)=30


def test_eval_swings_sente_perspective():
    ms = [
        {"n": 1, "side": "b", "spentSec": 1, "cumSec": 1, "evalCp": 0},
        {"n": 2, "side": "w", "spentSec": 1, "cumSec": 1, "evalCp": 0},
        {"n": 3, "side": "b", "spentSec": 1, "cumSec": 2, "evalCp": -500},   # 先手が500悪化
        {"n": 4, "side": "w", "spentSec": 1, "cumSec": 2, "evalCp": -1000},  # 後手が良くした
        {"n": 5, "side": "b", "spentSec": 1, "cumSec": 3, "evalCp": -900},
    ]
    sw = ta.eval_swings(ms, 300)
    assert [(s["n"], s["side"]) for s in sw] == [(3, "b")]


# ---- 盤面(07 T-80, T-81) ----
def test_positions_sample01_final_counts(s01):
    # 07 T-80(D-2 = sample01): 盤上29 + 持ち駒11
    g, moves, _ = doc(s01)
    pos, errs = build_positions(moves, g.meta)
    assert errs == [] and len(pos) == 101
    last = pos[-1]
    assert last.piece_count() == 40 and len(last.board) == 29
    assert last.hands["b"] == {"飛": 1, "銀": 3, "金": 1, "歩": 1}
    assert last.hands["w"] == {"歩": 4, "桂": 1}


def test_positions_sample02_final_counts(s02):
    # 07 T-81(D-1 = sample02): 盤上31 + 持ち駒9
    g, moves, _ = doc(s02)
    pos, errs = build_positions(moves, g.meta)
    assert errs == [] and len(pos) == 86
    last = pos[-1]
    assert last.piece_count() == 40 and len(last.board) == 31
    assert last.hands["b"] == {"歩": 5}
    assert last.hands["w"] == {"歩": 1, "金": 1, "銀": 2}


def test_every_position_has_40_pieces(s01, s02):
    for kif in (s01, s02):
        g, moves, _ = doc(kif)
        pos, errs = build_positions(moves, g.meta)
        assert all(p.piece_count() == 40 for p in pos)


def test_initial_position():
    p = initial_position()
    assert p.piece_count() == 40 and p.board[(5, 9)] == ("b", "玉") and p.board[(5, 1)] == ("w", "玉")


def test_bad_move_stops_with_error():
    moves = [{"n": 1, "text": "７六歩(77)"}, {"n": 2, "text": "３四歩(34)"}]  # 3四に歩はいない
    pos, errs = build_positions(moves, {})
    assert len(pos) == 2 and errs and errs[0]["n"] == 2


def test_handicap_unsupported():
    pos, errs = build_positions([{"n": 1, "text": "７六歩(77)"}], {"手合割": "香落ち"})
    assert pos == [] and errs


def test_dou_notation():
    moves = [{"n": 1, "text": "７六歩(77)"}, {"n": 2, "text": "３四歩(33)"},
             {"n": 3, "text": "２二角成(88)"}, {"n": 4, "text": "同　銀(31)"}]
    pos, errs = build_positions(moves, {})
    assert errs == []
    assert pos[4].board[(2, 2)] == ("w", "銀") and pos[4].hands["b"] == {"角": 1}


def test_svg_renders_both_perspectives():
    p = initial_position()
    for persp in ("b", "w"):
        svg = render_board_svg(p, persp)
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert svg.count("歩") >= 18
    # 後手視点では後手の駒が正立(回転なし)、先手の駒が回転
    assert "rotate(180" in render_board_svg(p, "w")


# ---- 突き合わせ(07 T-23) ----
def test_compare_four_categories():
    info = {
        55: {"side": "b", "spentSec": 42, "remainingSec": 200},
        57: {"side": "b", "spentSec": 97, "remainingSec": 100},
        60: {"side": "b", "spentSec": 2, "remainingSec": 80},
        77: {"side": "b", "spentSec": 53, "remainingSec": 60},
        58: {"side": "w", "spentSec": 77, "remainingSec": 300},
    }
    marks = [
        {"moveNo": 57, "feeling": "regret", "causeTags": ["読み抜け"]},   # 一致
        {"moveNo": 10, "feeling": "regret", "causeTags": []},             # 感覚だけ
        {"moveNo": 60, "feeling": "unsure", "causeTags": ["焦り・早指し"]},  # 感覚だけ + 時間の癖
        {"moveNo": 99, "feeling": "ok", "causeTags": []},                 # 対象外
    ]
    r = compare(marks, info, long_ns=[55, 57, 58, 77], swing_ns=[], my_side="b")
    assert [x["n"] for x in r["coincide"]] == [57]
    assert [x["n"] for x in r["feelingOnly"]] == [10, 60]
    assert [x["n"] for x in r["dataOnly"]] == [55, 77]  # 58は相手の手なので対象外
    assert [x["n"] for x in r["timeHabit"]] == [60]
    assert r["markCount"] == 3


def test_compare_empty_marks():
    r = compare([], {}, [], [], "b")
    assert r["coincide"] == [] and r["markCount"] == 0


# ---- 問題 ----
def test_problems_priority_and_no_best_move(s01):
    g, moves, a = doc(s01)
    pos, _ = build_positions(moves, g.meta)
    marks = [{"moveNo": 30, "feeling": "regret"}, {"moveNo": 31, "feeling": "unsure"}]
    ps = build_thinking_problems(moves, pos, marks, [55, 57], "b", max_problems=3)
    assert [p["n"] for p in ps] == [30, 31, 55]
    assert [p["origin"] for p in ps] == ["user_mark", "user_mark", "candidate"]
    assert all(p["bestMove"] is None and p["bestMoveNote"] == "最善手は未取得" for p in ps)
    assert ps[0]["position"]["n"] == 29  # 30手目の直前の局面
    assert ps[0]["played"] == moves[29]["text"] and ps[0]["nextMove"] == moves[30]["text"]


def test_problems_ignore_out_of_range(s01):
    g, moves, _ = doc(s01)
    pos, _ = build_positions(moves, g.meta)
    ps = build_thinking_problems(moves, pos, [{"moveNo": 999, "feeling": "regret"}], [], "b")
    assert ps == []


# ---- 匿名化(07 T-46。合成データ) ----
def test_anonymize_synthetic():
    kif = ("先手：テスト太郎\r\n後手：ダミー花子\r\n手数----指手---------消費時間--\r\n"
           "1 ７六歩(77)   ( 0:01/00:00:01)\r\n*テスト太郎さんの手\r\n")
    out = anonymize_kif(kif, ["テスト太郎"])
    assert "テスト太郎" not in out and "ダミー花子" not in out
    assert "先手：自分" in out and "後手：相手" in out and "\r\n" in out


def test_anonymize_masked_opponent():
    out = anonymize_kif("先手：abc123\n後手：?????\n", ["abc123"])
    assert out == "先手：自分\n後手：相手\n"
