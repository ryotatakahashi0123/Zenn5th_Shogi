"""過去の対局の集計、ログイン、保存先（手元のフォルダー）。エンジン不要。

    python -m pytest tests/test_learn_auth.py
"""
import json

import pytest

from shogi_analyzer import learn, regret
from shogi_coach import auth, storage


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
    assert s["counts"] == {"match": 1, "feeling_only": 1, "data_only": 2, "surprise": 0,
                           "good_match": 1, "good_feeling_only": 0, "good_data_only": 0}
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
    assert learn.aggregate([])["games"] == 0
    assert learn.aggregate([s, {**s, "pending": True}])["games"] == 1  # 印の入力がまだの対局は、記録に数えない
    # エンジンで解析しなかった対局は、選んだ原因だけを数える（好手の印に原因は無い）
    marks = [regret.clean_mark(3, ["読み抜け"], "", 9), regret.clean_mark(5, [], "", 9, kind="good")]
    m = learn.summarize_marks(marks, 9, "▲", "2026-10-13")
    assert m["tags"] == {"読み抜け": 1} and m["analyzed"] is False and m["bad"] == [] and m["counts"]["match"] == 0
    a2 = learn.aggregate([s, m])
    assert a2["games"] == 2 and a2["games_analyzed"] == 1 and a2["tags"][0] == {"tag": "読み抜け", "count": 3}


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
    assert auth.register(" Hanako ", "longpassword", "longpassword") == "hanako" and auth.login("hanako", "longpassword")
    for args, word in (((("taro", "longpassword", "longpassword")), "すでに"), (("jiro", "short", "short"), "8文字"),
                       (("jiro", "longpassword", "longpassworx"), "一致"), (("ジロー", "longpassword", "longpassword"), "利用者名")):
        with pytest.raises(ValueError, match=word):
            auth.register(*args)
    assert auth.login("taro", "pass1")  # 上書きされていない
    monkeypatch.setattr(auth, "MAX_USERS", 2)
    with pytest.raises(ValueError, match="上限"):
        auth.register("jiro", "longpassword", "longpassword")
    monkeypatch.setattr(auth, "MAX_USERS", 30)
    monkeypatch.setattr(auth, "SIGNUP_CODE", "aikotoba")
    with pytest.raises(ValueError, match="合言葉"):
        auth.register("jiro", "longpassword", "longpassword", "x")
    assert auth.register("jiro", "longpassword", "longpassword", "aikotoba") == "jiro"
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
