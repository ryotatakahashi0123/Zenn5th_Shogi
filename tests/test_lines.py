"""読み筋を伸ばす（短く途切れた読み筋の続きを読む）。エンジンの代わりに決まった手を返す偽物を使う。

    python -m pytest tests/test_lines.py
"""
import cshogi

from shogi_analyzer import service
from shogi_analyzer.kifu_analysis import load_kifu
from shogi_analyzer.service import ext_key, extend_line, extend_pv, extend_rows, legal_prefix, lines_jp

KIFU = load_kifu("sample.kif")


def _first_legal(board, n):
    """その局面から、合法手の先頭を n 手続けた手順（偽のエンジンの読み筋）。"""
    b, out = board.copy(), []
    for _ in range(n):
        if b.is_game_over():
            break
        u = cshogi.move_to_usi(next(iter(b.legal_moves)))
        out.append(u)
        b.push(b.move_from_usi(u))
    return out


class FakeClient:
    """全手解析の代わり。最後の手の後の読み筋を、毎回2手だけ返す。"""

    def __init__(self):
        self.calls = 0

    def analyze_game(self, kifu, ms):
        self.calls += 1
        b = cshogi.Board(kifu.start_sfen)
        for m in kifu.moves:
            b.push(m)
        return {"rows": [{"ply": len(kifu.moves), "pv": " ".join(_first_legal(b, 2))}]}


class FakeEngine:
    def __init__(self):
        self.calls = 0

    def analyze(self, position, moves, ms, multipv=1):
        self.calls += 1
        b = cshogi.Board(position.split(" ", 1)[1])
        for u in moves:
            b.push(b.move_from_usi(u))
        return type("R", (), {"pv": _first_legal(b, 2)})()


def test_extend_line_and_pv():
    start = cshogi.Board().sfen()
    c = FakeClient()
    line = extend_line(c, start, ["7g7f", "3c3d"], want=8)
    assert line[:2] == ["7g7f", "3c3d"] and len(line) == 8 and c.calls == 3  # 2手ずつ3回伸ばす
    assert legal_prefix(cshogi.Board(), line) == line
    assert extend_line(FakeClient(), start, ["7g7f"] * 20, want=8)[:1] == ["7g7f"]  # 指せない手から先は捨てる
    assert extend_line(FakeClient(), start, [], want=8) == []
    e = FakeEngine()
    pv = extend_pv(e, start, ["7g7f"], ["3c3d"], want=5)
    assert pv[0] == "3c3d" and len(pv) == 5 and e.calls == 2
    assert extend_pv(FakeEngine(), start, [], ["2g2f", "8c8d", "2f2e"], want=3) == ["2g2f", "8c8d", "2f2e"]  # 足りていれば読まない


def test_extend_rows_and_lines_jp():
    k = KIFU
    b = service.board_after(k, 14)[0]
    b.push(b.move_from_usi("2d3d"))
    second = next(cshogi.move_to_usi(m) for m in b.legal_moves if service.move_jp(b, cshogi.move_to_usi(m)) == "△３三角")
    rows = [{"ply": 15, "side": "▲", "move_usi": cshogi.move_to_usi(k.moves[14]), "best_pv": f"2d3d {second}", "pv": "8f8i+"}]
    plain = lines_jp(k, rows[0])
    assert plain["best"] == ["▲３四飛", "△３三角"] and len(plain["actual"]) == 2
    cache = extend_rows(FakeClient(), k, rows, [15, 99], {})
    assert set(cache) == {ext_key(15, "best"), ext_key(15, "actual")}
    ext = lines_jp(k, rows[0], cache)
    # 偽のエンジンは1回に2手しか返さないので、読み直しの上限（EXTEND_CALLS 回）まで伸ばしたところで止まる
    want = min(2 + 2 * service.EXTEND_CALLS, service.LINE_PLIES)
    assert ext["best"][:2] == ["▲３四飛", "△３三角"] and len(ext["best"]) == want
    assert ext["actual"][0] == "▲６六角" and len(ext["actual"]) == want
    n = FakeClient()
    extend_rows(n, k, rows, [15], cache)
    assert n.calls == 0  # 伸ばした読み筋は読み直さない


def test_line_positions_for_popup():
    # 15手目（▲６六角）の直前の局面から: 最善手 ▲３四飛 △７六飛、実戦 ▲６六角 △８九飛成
    row = {"ply": 15, "move_usi": cshogi.move_to_usi(KIFU.moves[14]), "best_pv": "2d3d 8f7f", "pv": "8f8i+"}
    pos = service.line_positions(KIFU, row)
    before = service.board_after(KIFU, 14)[0].sfen()
    assert [s["move_jp"] for s in pos["best"]] == [None, "▲３四飛", "△７六飛"]
    assert pos["best"][0]["sfen"] == before and pos["best"][1]["before"] == before
    assert pos["best"][0]["move_usi"] == cshogi.move_to_usi(KIFU.moves[13])  # 先頭は直前の実戦の手に色を付ける
    assert [s["move_jp"] for s in pos["actual"]] == [None, "▲６六角", "△８九飛成"]
    b = cshogi.Board(before)
    b.push_usi("2d3d")
    assert pos["best"][1]["sfen"] == b.sfen()
    # 伸ばした読み筋があればそれを使う。指せない手が出たらそこで打ち切る
    ext = {ext_key(15, "best"): ["2d3d", "8f7f", "9i9i"]}
    assert len(service.line_positions(KIFU, row, ext)["best"]) == 3
    # 実戦の手だけ（続きが無い）なら、実戦の読み筋は出さない（lines_jp と同じ）
    assert service.line_positions(KIFU, {**row, "pv": ""})["actual"] == []
    # 色を付けた図は、付けない図と違う
    s = pos["best"][1]
    assert service.position_svg(s["sfen"], s["move_usi"], s["before"]) != service.position_svg(s["sfen"])
