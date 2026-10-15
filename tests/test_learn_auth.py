"""過去の対局の集計、ログイン、保存先（手元のフォルダー）。エンジン不要。

    python -m pytest tests/test_learn_auth.py
"""
import json
from datetime import date

import pytest

from shogi_analyzer import learn, regret
from shogi_coach import auth, storage


B = date(2000, 1, 1)  # 画面からの登録に要る生年月日


def _row(ply, label="", good=False):
    return {"ply": ply, "side": "▲" if ply % 2 else "△", "move_usi": "a", "move_jp": f"手{ply}", "label": label,
            "winrate_drop": 0.3 if label else 0.0, "best_before_usi": "a" if not label else "b", "best_before_jp": "最善",
            "good": good, "good_gap": 0.2 if good else 0.0}


ROWS = [_row(1, good=True), _row(2, "悪手"), _row(3, "悪手"), _row(4), _row(5, "緩手"), _row(6), _row(7), _row(8), _row(9, "疑問手")]


def _gap():
    marks = [regret.clean_mark(3, ["読み抜け", "手拍子"], "<b>メモ</b>", 9), regret.clean_mark(7, ["読み抜け"], "", 9),
             regret.clean_mark(1, [], "", 9, kind="good")]
    return regret.compare(ROWS, marks, "▲")


def test_summarize_counts_only_numbers():
    s = learn.summarize(ROWS, _gap(), "2026-10-13")
    assert s["counts"] == {"match": 1, "feeling_only": 1, "data_only": 2, "hesitate": 0,
                           "good_match": 1, "good_feeling_only": 0, "good_data_only": 0,
                           "opp_match": 0, "opp_feeling_only": 0, "opp_data_only": 1, "surprise": 0,
                           "opp_good_match": 0, "opp_good_feeling_only": 0, "good_opponent": 0}
    assert s["tags"] == {"読み抜け": 2, "手拍子": 1} and s["good"] == 1
    # 自分（▲）の悪手候補だけ。9手の対局は 1〜3 序盤、4〜6 中盤、7〜9 終盤
    assert s["bad"] == [{"ply": 3, "label": "悪手", "phase": "序盤", "noticed": True},
                        {"ply": 5, "label": "緩手", "phase": "中盤", "noticed": False},
                        {"ply": 9, "label": "疑問手", "phase": "終盤", "noticed": False}]
    assert "メモ" not in json.dumps(s, ensure_ascii=False)  # コメントの文は入れない
    assert [learn.phase(p, 100) for p in (1, 34, 35, 67, 68, 100)] == ["序盤", "序盤", "中盤", "中盤", "終盤", "終盤"]


def test_aggregate():
    s = learn.summarize(ROWS, _gap())
    a = learn.aggregate([s, s, {"counts": {}}])  # 欄の欠けた古い集計が混じっても動く
    assert a["games"] == 3 and a["counts"]["match"] == 2 and a["counts"]["data_only"] == 4
    assert a["tags"] == [{"tag": "読み抜け", "count": 4}, {"tag": "手拍子", "count": 2}]
    assert a["bad_moves"] == 6 and a["bad_noticed"] == 2 and a["good_moves"] == 2
    assert a["bad_by_phase"] == {"序盤": 2, "中盤": 2, "終盤": 2} and a["bad_by_label"]["悪手"] == 2
    # 割合は、自分（▲）が指した手（1,3 / 5 / 7,9）のうち悪手候補だった手。順番は序盤・中盤・終盤で固定
    assert a["moves_by_phase"] == {"序盤": 4, "中盤": 2, "終盤": 4} and list(a["bad_rate_by_phase"]) == learn.PHASES
    assert a["bad_rate_by_phase"] == {"序盤": 0.5, "中盤": 1.0, "終盤": 0.5} and a["bad_rate"] == 0.6
    assert a["noticed_rate"] == 2 / 6
    old = {k: v for k, v in s.items() if k != "moves_by_phase"}  # 分母を持たない古い集計は、手数の偶奇から数える
    assert learn.aggregate([old])["moves_by_phase"] == {"序盤": 2, "中盤": 1, "終盤": 2}
    assert learn.aggregate([{"counts": {}}])["bad_rate_by_phase"] == {"序盤": None, "中盤": None, "終盤": None}
    assert learn.aggregate([])["games"] == 0
    assert learn.aggregate([s, {**s, "pending": True}])["games"] == 1  # 印の入力がまだの対局は、記録に数えない
    # エンジンで解析しなかった対局は、悪手だと思った自分の手で選んだ原因だけを数える
    marks = [regret.clean_mark(3, ["読み抜け"], "", 9), regret.clean_mark(5, ["欲張り"], "", 9, kind="good"),
             regret.clean_mark(4, ["手拍子"], "", 9), {"ply": 7, "kind": "regret", "tags": ["読み抜け"], "note": ""}]
    m = learn.summarize_marks(marks, 9, "▲", "2026-10-13")
    assert m["tags"] == {"読み抜け": 2} and m["analyzed"] is False and m["bad"] == [] and m["counts"]["match"] == 0
    a2 = learn.aggregate([s, m])
    assert a2["games"] == 2 and a2["games_analyzed"] == 1 and a2["tags"][0] == {"tag": "読み抜け", "count": 4}


def test_password_hash_and_lockout():
    rec = auth.hash_password("himitsu")
    assert auth.verify(rec, "himitsu") and not auth.verify(rec, "himitsu ") and "himitsu" not in json.dumps(rec)
    assert auth.hash_password("himitsu")["hash"] != rec["hash"]  # 塩が毎回違う
    users = {"taro": rec}
    auth._fails.clear()
    assert auth.login("Taro ", "himitsu", now=0, users=users)          # 名前の大文字・前後の空白は無視
    assert not auth.login("jiro", "himitsu", now=0, users=users)       # いない利用者
    assert not auth.login("../x", "himitsu", now=0, users=users)
    for _ in range(auth.MAX_FAILS):
        assert not auth.login("taro", "wrong", now=10, users=users)
    with pytest.raises(ValueError, match="しばらく"):
        auth.login("taro", "himitsu", now=11, users=users)             # 正しくても、止めているあいだは入れない
    assert auth.login("taro", "himitsu", now=11 + auth.LOCK_SECONDS, users=users)


def test_local_store_users_and_games(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "BUCKET", "")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    auth._fails.clear()
    assert auth.load_users() == {}
    auth.add_user("taro", "pass1")
    assert auth.login("taro", "pass1") and not auth.login("taro", "pass2")
    assert "pass1" not in (tmp_path / "auth" / "users.json").read_text()
    for name, pw in (("Taro", "pass1"), ("a", "pass1"), ("taro", "123")):
        with pytest.raises(ValueError):
            auth.add_user(name, pw)
    # 画面からの登録: すでにある名前は上書きしない。パスワードは8文字以上、確認と一致、上限あり
    assert auth.register(" Hanako ", "longpassword", "longpassword", birth=B) == "hanako" and auth.login("hanako", "longpassword")
    for args, word in (((("taro", "longpassword", "longpassword")), "すでに"), (("jiro", "short", "short"), "8文字"),
                       (("jiro", "longpassword", "longpassworx"), "一致"), (("ジロー", "longpassword", "longpassword"), "利用者名")):
        with pytest.raises(ValueError, match=word):
            auth.register(*args, birth=B)
    assert auth.login("taro", "pass1")  # 上書きされていない
    monkeypatch.setattr(auth, "MAX_USERS", 2)
    with pytest.raises(ValueError, match="上限"):
        auth.register("jiro", "longpassword", "longpassword", birth=B)
    monkeypatch.setattr(auth, "MAX_USERS", 30)
    monkeypatch.setattr(auth, "SIGNUP_CODE", "aikotoba")
    with pytest.raises(ValueError, match="合言葉"):
        auth.register("jiro", "longpassword", "longpassword", "x", birth=B)
    assert auth.register("jiro", "longpassword", "longpassword", "aikotoba", birth=B) == "jiro"
    # 対局は利用者ごとに分かれる
    storage.save_kifu("taro", "20261013-ab12", "x.csa", b"V2.2\n")
    storage.save_json("taro", "20261013-ab12", "summary", {"plies": 9})
    storage.save_json("taro", "20261013-ab12", "result", {"rows": []})
    storage.save_json("saburo", "20261013-cd34", "summary", {"plies": 5})
    assert storage.load_summaries("taro") == [{"plies": 9, "game_id": "20261013-ab12"}]
    assert (tmp_path / "games" / "taro" / "20261013-ab12" / "kifu.csa").is_file()
    # あとから開き直す: 棋譜と結果が同じ形で戻る。ほかの利用者の対局は開けない
    assert storage.load_game("taro", "20261013-ab12") == {"name": "kifu.csa", "data": b"V2.2\n", "result": {"rows": []}}
    assert storage.load_game("saburo", "20261013-ab12") is None
    storage.save_kifu("taro", "20261013-ef56", "x.kif", b"k")
    assert storage.load_game("taro", "20261013-ef56") == {"name": "kifu.kif", "data": b"k", "result": None}
    with pytest.raises(ValueError):
        storage.save_kifu("../etc", "x", "x.kif", b"")
    with pytest.raises(ValueError):
        storage.load_game("taro", "../../auth")
    # 削除: その対局だけが消える。ほかの対局・ほかの利用者のものは残る
    assert storage.delete_game("taro", "20261013-ef56") == 1 and storage.load_game("taro", "20261013-ef56") is None
    assert storage.delete_game("taro", "20261013-ef56") == 0
    assert storage.load_game("taro", "20261013-ab12") and storage.load_summaries("saburo")
    with pytest.raises(ValueError):
        storage.delete_game("taro", "../saburo")
    # 利用者ごとの設定
    assert storage.load_prefs("taro") == {}
    storage.save_prefs("taro", {"always_save": True})
    assert storage.load_prefs("taro") == {"always_save": True} and storage.load_prefs("saburo") == {}
    with pytest.raises(ValueError):
        storage.save_prefs("../x", {})


def test_six_labels_in_record_and_facts():
    from shogi_analyzer import trend
    # 2手目（△の悪手候補）に「相手の手・悪手だと思った」、4手目に「相手の手・予想外で驚いた」、7手目に「ほかにも指す手があった」
    marks = [regret.clean_mark(2, [], "", 9), regret.clean_mark(4, [], "", 9, kind="other"),
             regret.clean_mark(7, [], "", 9, kind="other"), regret.clean_mark(1, [], "", 9, kind="good")]
    s = learn.summarize(ROWS, regret.compare(ROWS, marks, "▲"))
    assert s["counts"]["opp_match"] == 1 and s["counts"]["opp_data_only"] == 0 and s["counts"]["hesitate"] == 1
    assert s["surprise"]["total"] == 1 and s["counts"]["good_match"] == 1
    f = trend.facts(learn.aggregate([s]))
    assert "相手の悪手候補は1回。そのうち悪手だと気づけたのは1回（相手の悪手だと思ったが、エンジンでは勝率がほとんど下がっていなかった手は0回）。" in f
    assert "ほかにも指す手があったと思いながら指した自分の手: 1回。" in f
    assert "好手だと思った自分の手は1回。そのうちエンジンの最善手と同じだったのは1回。" in f


def test_reset_password_with_birth(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setattr(storage, "BUCKET", "")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    auth._fails.clear()
    with pytest.raises(ValueError, match="生年月日"):
        auth.register("hanako", "longpassword", "longpassword")  # 生年月日は必須
    with pytest.raises(ValueError, match="正しくありません"):
        auth.register("hanako", "longpassword", "longpassword", birth=date(2999, 1, 1))
    auth.register("hanako", "longpassword", "longpassword", birth=date(2000, 2, 29))
    assert "2000" not in json.dumps(auth.load_users())  # 生年月日そのものは保存しない
    with pytest.raises(ValueError, match="利用者名か生年月日"):
        auth.reset_password("hanako", date(2000, 3, 1), "newpassword", "newpassword", now=0)
    with pytest.raises(ValueError, match="利用者名か生年月日"):
        auth.reset_password("nobody", date(2000, 2, 29), "newpassword", "newpassword", now=0)  # いない利用者も同じ文
    with pytest.raises(ValueError, match="確認"):
        auth.reset_password("hanako", date(2000, 2, 29), "newpassword", "other", now=0)
    assert auth.reset_password(" Hanako ", date(2000, 2, 29), "newpassword", "newpassword", now=0) == "hanako"
    assert auth.login("hanako", "newpassword") and not auth.login("hanako", "longpassword")
    assert auth.reset_password("hanako", date(2000, 2, 29), "again12345", "again12345", now=0)  # 生年月日は残っている
    auth.add_user("hanako", "admin")  # 管理者がパスワードを変えても、生年月日は残す
    assert auth.reset_password("hanako", date(2000, 2, 29), "again12345", "again12345", now=0)
    auth.add_user("taro", "pass1")  # 生年月日の無い利用者は、画面から初期化できない
    with pytest.raises(ValueError, match="利用者名か生年月日"):
        auth.reset_password("taro", date(2000, 2, 29), "newpassword", "newpassword", now=0)
    auth._fails.clear()
    for _ in range(auth.MAX_FAILS):  # 生年月日の総当たりは、続けて間違えると止める
        with pytest.raises(ValueError, match="利用者名か生年月日"):
            auth.reset_password("hanako", date(1999, 1, 1), "newpassword", "newpassword", now=10)
    with pytest.raises(ValueError, match="しばらく"):
        auth.reset_password("hanako", date(2000, 2, 29), "newpassword", "newpassword", now=11)
    assert auth.reset_password("hanako", date(2000, 2, 29), "newpassword", "newpassword", now=11 + auth.LOCK_SECONDS)


def test_set_birth_for_existing_user(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "BUCKET", "")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    auth._fails.clear()
    auth.add_user("ryota", "oldpass")  # 生年月日の無い、以前の利用者
    assert not auth.has_birth("ryota")
    with pytest.raises(ValueError, match="パスワードが違います"):
        auth.set_birth("ryota", "wrong", date(1990, 5, 1), now=0)
    with pytest.raises(ValueError, match="生年月日"):
        auth.set_birth("ryota", "oldpass", None, now=0)
    auth.set_birth("ryota", "oldpass", date(1990, 5, 1), now=0)
    assert auth.has_birth("ryota") and auth.login("ryota", "oldpass")
    assert auth.reset_password("ryota", date(1990, 5, 1), "newpassword", "newpassword", now=0) == "ryota"
    for _ in range(auth.MAX_FAILS):  # 続けて間違えると止める
        with pytest.raises(ValueError, match="パスワードが違います"):
            auth.set_birth("ryota", "wrong", date(1990, 5, 1), now=10)
    with pytest.raises(ValueError, match="しばらく"):
        auth.set_birth("ryota", "newpassword", date(1990, 5, 1), now=11)
