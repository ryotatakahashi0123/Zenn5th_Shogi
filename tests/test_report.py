"""ふりかえりノート（PDF・印刷用HTML）。エンジン不要。

    python -m pytest tests/test_report.py
"""
import base64
import re

import cshogi

from shogi_analyzer import regret, report
from shogi_analyzer.kifu_analysis import load_kifu
from shogi_analyzer.service import move_list

PV = {15: ("2d3d 8f7f", "8f8i+ 6f2b+")}  # 15手目: (最善手からの読み筋, 実戦の手 ▲６六角 の後の読み筋)
BEST = {15: "2d3d"}  # 15手目の直前の局面で指せる手（▲３四飛）。ほかは指せない手 → 回答の図は直前の局面のまま
GOOD_NOTE = "<b>うまく受けた</b>"
NOTE = "<script>alert(1)</script> 攻め合いで勝てると思った😀"


ANSWER = "回答（エンジンの最善手と、その後の読み筋）"
QA = [("15手目で<u>３四飛</u>なら\nどうだった？", "### 結論\n▲３四飛なら**互角**でした。\n- 先手の勝率 55%"), ("もう1つ質問", "回答2")]


def _data(coach="", qa=()):
    k = load_kifu("sample.kif")
    labels = {15: ("悪手", 0.31), 21: ("疑問手", 0.12), 22: ("緩手", 0.06)}
    rows = [{"ply": m["ply"], "side": m["side"], "move_usi": cshogi.move_to_usi(k.moves[m["ply"] - 1]), "move_jp": m["move_jp"],
             "label": labels.get(m["ply"], ("", 0.0))[0], "winrate_drop": labels.get(m["ply"], ("", 0.0))[1],
             "best_before_usi": BEST.get(m["ply"], "y"), "best_before_jp": "７六歩(77)", "winrate_sente": 0.5 + 0.01 * (m["ply"] % 7),
             "good": m["ply"] in (9, 16), "good_gap": 0.25 if m["ply"] in (9, 16) else 0.0,
             "best_pv": PV.get(m["ply"], ("", ""))[0], "pv": PV.get(m["ply"], ("", ""))[1]}
            for m in move_list(k)]
    marks = [regret.clean_mark(15, ["読み抜け", "手拍子"], NOTE, 40), regret.clean_mark(3, ["その他"], "", 40),
             regret.clean_mark(5, [], GOOD_NOTE, 40, kind="good"), regret.clean_mark(22, [], "", 40),
             regret.clean_mark(16, [], "", 40, kind="other")]  # 22・16手目は相手（△）の手
    gap = regret.compare(rows, marks, "▲")
    return k, report.collect(k, rows, gap, title="sample", coach_text=coach, created="2026-10-12", qa=qa)


def test_collect_keeps_numbers_and_order():
    _, d = _data()
    secs = {s["key"]: s for s in d["sections"]}
    assert [s["title"] for s in d["sections"]][0] == "自分の手・悪手だと思った" and len(d["sections"]) == 6
    assert secs["my_bad"]["counts"] == [("一致", 1), ("感覚だけ", 1), ("データだけ", 1)]
    assert report.count_text(secs["opp_other"]) == "1手"
    # 局面図つきの欄は、6つのラベルの順に、印を付けた手と自分の悪手候補
    assert [(m["ply"], m["kind"]) for m in d["items"]] == [
        (3, "自分の手・悪手だと思った／感覚だけ"), (15, "自分の手・悪手だと思った／一致"), (21, "自分の手・悪手だと思った／データだけ"),
        (5, "自分の手・好手だと思った／感覚だけ"), (22, "相手の手・悪手だと思った／一致"), (16, "相手の手・予想外で驚いた")]
    assert report.table_row(secs["my_bad"]["rows"][1])[3:5] == ["悪手", "31%"]  # 数値は解析の行のまま
    assert len(d["series"]) == 40 and d["my_side"] == "先手"
    # 好手: 印を付けた5手目（最善手とは違う）と自分の好手候補の9手目。相手の好手候補の16手目は、相手の手のラベル
    assert [(m["ply"], m["kind"]) for m in secs["my_good"]["rows"]] == [(5, "感覚だけ"), (9, "データだけ")]
    assert [(m["ply"], m["kind"]) for m in secs["opp_good"]["rows"]] == [(16, "データだけ")]
    assert report.good_row(secs["my_good"]["rows"][1])[3:5] == ["好手候補", "25%"]


def test_md_blocks_and_inline():
    b = report.md_blocks("### 総評\n\n先手が**15手目**で損をした。\n- 読みを入れる\n---\n1. 番号つき")
    assert b == [("h", "総評"), ("p", "先手が**15手目**で損をした。"), ("li", "読みを入れる"), ("p", "1. 番号つき")]
    assert report.inline("a **b** <i>") == "a <b>b</b> &lt;i&gt;"
    assert report.md_blocks("") == []


def test_html_escapes_user_text():
    k, d = _data("### 総評\n<img src=x onerror=alert(1)> **太字**")
    page = report.to_html(d, k)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<img" not in page and "<b>太字</b>" in page
    assert page.count("<svg") >= 1 + 12 and page.count('class="card"') == 12  # グラフ + 局面図6つ + 回答の図6つ
    # エンジンの最善手は、回答の見出しより後ろにだけ出る
    assert "最善手" not in page[page.index("<h1>"):page.index(ANSWER)].replace(report.BOARD_NOTE, "")
    assert page.count("エンジンの最善手</b>") == 6
    # ラベルごとに見出しを付ける（局面ごとのふりかえりと回答の両方）
    assert page.count("<h3>相手の手・予想外で驚いた</h3>") == 3  # 表、局面図、回答
    # 好手の表は回答より後ろ。本人のコメントは文字として埋め込む
    assert page.index("<h2>好手</h2>") > page.index(ANSWER)
    assert "<b>うまく受けた</b>" not in page and "&lt;b&gt;うまく受けた&lt;/b&gt;" in page
    assert "window.print()" in page and "@page" in page and "AIコーチの所感" in page
    # 読み筋は回答にだけ出る（解析の行にあるUSIの手順を日本語にしたもの）。無い手には欄を出さない
    front, back = page.split(ANSWER, 1)
    assert "読み筋" not in front and back.count("最善手からの読み筋</b>") == 1
    assert "<b>最善手からの読み筋</b>: ▲３四飛 △７六飛" in back
    assert "<b>実戦の手からの読み筋</b>: ▲６六角 △８九飛成 ▲２二角成" in back
    assert "AIコーチの所感" not in report.to_html(_data()[1], k)  # 講評が無ければ欄も出さない


def test_questions_and_answers_go_last():
    k, d = _data("### 総評\n講評", qa=QA)
    assert d["qa"][0] == ("15手目で<u>３四飛</u>なら どうだった？", [("h", "結論"), ("p", "▲３四飛なら**互角**でした。"), ("li", "先手の勝率 55%")])
    page = report.to_html(d, k)
    tail = page[page.index("AIコーチへの質問と回答"):]
    assert page.index("AIコーチへの質問と回答") > page.index("AIコーチの所感")  # 講評より後ろ（末尾）
    assert "<h3>質問: 15手目で&lt;u&gt;３四飛&lt;/u&gt;なら どうだった？</h3>" in tail and "<u>" not in page
    assert "<b>互角</b>" in tail and tail.index("もう1つ質問") > tail.index("先手の勝率 55%")
    assert "AIコーチへの質問と回答" not in report.to_html(_data("### 総評\n講評")[1], k)  # 質問が無ければ欄も出さない
    assert len(report.to_pdf(d, k)) > len(report.to_pdf(_data("### 総評\n講評")[1], k))


def test_print_link_embeds_page_as_base64():
    page = '<p>"</script><b>'
    frag = report.print_link(page)
    assert "</script><b>" not in frag  # 中身がそのまま断片に出ない
    assert base64.b64decode(re.search(r'atob\("([^"]+)"\)', frag)[1]).decode() == page


def test_pdf_has_embedded_font_and_pages():
    k, d = _data("### 総評\n先手が**15手目**で損をした。\n- 読みを入れる")
    pdf = report.to_pdf(d, k)
    assert pdf.startswith(b"%PDF") and b"FontFile2" in pdf  # 日本語フォントを埋め込んでいる
    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) >= 2
    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) >= 3  # 回答は別のページ
    assert report._safe("歩😀") == "歩□"  # フォントに無い文字は□
    # 印も悪手候補も無い対局でも作れる
    k2, d2 = _data()
    d2 = {**d2, "items": [], "series": [], "sections": [{**s, "rows": [], "cards": []} for s in d2["sections"]]}
    assert report.to_pdf(d2, k2).startswith(b"%PDF")


def test_answer_board_plays_engine_best():
    k, d = _data()
    m15 = next(m for m in d["items"] if m["ply"] == 15)
    board, mv = report.answer_board(k, m15)
    assert cshogi.move_to_usi(mv) == "2d3d" and board.move_number == 16  # 14手 + 最善手
    # 指せない手（投了など）は、直前の局面のまま（直前の手を強調）
    board3, mv3 = report.answer_board(k, {"ply": 3, "best_usi": "resign"})
    assert mv3 == k.moves[1] and board3.move_number == 3
