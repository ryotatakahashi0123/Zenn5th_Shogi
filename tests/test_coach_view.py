"""講評と局面図の連動、時刻付きの棋譜ダウンロード（エンジン不要）。

    python -m pytest tests/test_coach_view.py
"""
from shogi_analyzer import coach_view, report

REVIEW = "### 総評\n序盤は互角。\n### 分岐点\n#### 15手目 ▲６六角 → ▲３四飛\n- 勝率が大きく下がった。\n- ２１手目も疑問。\n### 次に活かすこと\n- 999手目は無い手数。<script>x</script>"


def test_plies_and_sections():
    b = coach_view.blocks(REVIEW, 40)
    assert [x["kind"] for x in b] == ["h", "p", "h", "h", "li", "li", "h", "li"]
    assert b[3]["plies"] == [15] and b[4]["plies"] == [15]  # 見出しの手数を、その節の段落にも引き継ぐ
    assert b[5]["plies"] == [21]  # 全角の数字も読む。自分の手数があれば、そちらを使う
    assert b[7]["plies"] == [] and "999手目" in b[7]["html"] and 'data-ply="999"' not in b[7]["html"]  # 対局に無い手数は押せない
    assert '<a class="ply" data-ply="15">15手目</a>' in b[3]["html"]
    assert "<script>" not in b[7]["html"] and "&lt;script&gt;" in b[7]["html"]  # AIの文章はエスケープする
    assert coach_view.plies_in("3手目と3手目、5手目", 4) == [3]


def test_messages_hide_review_prompt_and_errors():
    chat = [("user", "ふりかえって"), ("assistant", REVIEW), ("user", "15手目で<b>３四飛</b>は？"),
            ("assistant", "失敗しました: 15手目")]
    m = coach_view.messages(chat, 40, hide="ふりかえって", error="失敗しました")
    assert [x["role"] for x in m] == ["assistant", "user", "assistant"]
    assert m[1]["blocks"][0]["plies"] == [15] and "&lt;b&gt;" in m[1]["blocks"][0]["html"]
    assert m[2]["blocks"][0]["plies"] == [] and "data-ply" not in m[2]["blocks"][0]["html"]


def test_timed_download_link():
    h = report.timed_download_link("棋譜".encode("utf-8"), 'my "game"/1', ".kif", "棋譜をダウンロード", uid="Study-1")
    assert 'id="furikaeri-dl-study1"' in h and 'a.download = "my_game_1" + "_"' in h  # ファイル名に使えない文字は _ にする
    assert "getSeconds()" in h and '".kif"' in h and "棋譜をダウンロード" in h
