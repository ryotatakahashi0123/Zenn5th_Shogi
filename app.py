"""ふりかえり将棋（Streamlit）。

ログイン（利用者名とパスワード）→
0. 初期画面（これまでの記録）の「棋譜を入力する」→ 棋譜入力モード: ファイルを読み込むか、テキストを貼り付けるか、盤面で1手ずつ指して入力する。保存は本人が同意したときだけ。ふりかえりの途中でも行き来できる
   棋譜を読み込んだ時点で、エンジンの解析を裏で始める（結果は印の入力が終わるまで見せない）。
   解析と印の入力が終わると、AIコーチのふりかえりも自動で始める。印を付けずに、すぐ解析結果を見ることもできる
1. 棋譜を読み込む → 自分の手番を確かめ、局面図を見ながら手に印を付ける（解析結果はまだ見せない）。
   自分の手: 悪手だと思った / ほかにも指す手があった / 好手だと思った。相手の手: 悪手だと思った / 予想外で驚いた / 好手だと思った
2. 入力を完了 → 将棋エンジンで全手を解析し、6つのラベルごとの局面と読み筋・形勢グラフ・悪手候補を表示
3. 「AIコーチにふりかえってもらう」→ ADK エージェント（Gemini）が自分で局面を選んで深掘りし、講評を書く
4. 対局について質問 → エージェントが必要に応じてエンジンで確かめてから答える

    streamlit run app.py
環境変数: ENGINE_URL（未設定ならプロセス内エンジン）、GOOGLE_CLOUD_PROJECT 等（Vertex AI）、BUCKET（任意）
"""
import hashlib
import io
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from pathlib import Path

import altair as alt
import cshogi
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from shogi_analyzer import alts, coach_view, entry, learn, regret, report
from shogi_analyzer.kifu_analysis import GOOD_GAP, detect_format, load_kifu
from shogi_analyzer.service import (board_svg, ext_key, extend_rows, line_positions, lines_jp, move_list, position_svg,
                                    sfen_svg)
from shogi_coach import auth, storage
from shogi_coach import trend as trend_job
from shogi_coach.engine_client import get_client

MAX_UPLOAD = 200_000  # bytes
# 棋譜入力の将棋盤（cshogi の図を表示し、押されたマスを返す画面部品）
board_input = components.declare_component("board_input", path=str(Path(__file__).parent / "assets" / "board_input"))
# AIコーチの講評（「N手目」を押すと局面図をその手にする画面部品）
coach_text = components.declare_component("coach_text", path=str(Path(__file__).parent / "assets" / "coach_text"))
OK_EXT = (".kif", ".kifu", ".csa", ".txt")
COACH_ERROR = "AIコーチの応答に失敗しました"
REVIEW = "この対局をふりかえってください。"
MARK_WAIT = 2 * 60 * 60  # 裏の処理が、印の入力の完了を待つ長さ（秒）
AI_READY = bool(os.environ.get("GOOGLE_CLOUD_PROJECT")) or os.environ.get("COACH_ENABLED") == "1"

TOOL_LABEL = {
    "analyze_game": lambda a: "全手の形勢を確認",
    "regret_gap": lambda a: "6つのラベルの印とエンジンの判定を突き合わせる",
    "past_tendencies": lambda a: "これまでの対局の記録を確認",
    "deep_dive": lambda a: f"{a.get('ply')}手目の局面を深く読む",
    "compare_move": lambda a: f"{a.get('ply')}手目で「{a.get('move')}」と比べる",
}

st.set_page_config(page_title="ふりかえり将棋", page_icon="☗", layout="wide")
st.title("ふりかえり将棋")
st.caption("棋譜を読み込み、答えを見る前に、まず自分の感覚で手に印を付けます。そのあと将棋エンジンの判定とのずれを見せ、"
           "AIコーチが勝負の分かれ目をふりかえります。")

JST = timezone(timedelta(hours=9))


# ---- ログインと利用者の登録（利用者名とパスワード。管理者は tools/add_user.py でも追加できる） ----
def login_page():
    st.markdown("#### ログイン")
    with st.form("login"):
        name = st.text_input("利用者名")
        password = st.text_input("パスワード", type="password")
        go = st.form_submit_button("ログイン", type="primary")
    if go:
        try:
            ok = auth.login(name, password)
        except ValueError as e:  # 続けて間違えて、止めているあいだ
            st.error(str(e))
        except Exception as e:
            st.error(f"ログインの確認に失敗しました: {e}")
        else:
            if ok:
                st.session_state["user"] = name.strip().lower()
                st.rerun()
            st.error("利用者名かパスワードが違います")
    st.button("新しい利用者を登録する", on_click=lambda: st.session_state.update(auth_page="signup"))


def signup_page():
    st.markdown("#### 新しい利用者の登録")
    st.button("◀ ログインに戻る", on_click=lambda: st.session_state.update(auth_page="login"))
    with st.form("signup"):
        name = st.text_input("利用者名", help="半角の英小文字・数字・_ の2〜20文字")
        password = st.text_input("パスワード", type="password", help=f"{auth.MIN_SIGNUP_PASSWORD}文字以上")
        confirm = st.text_input("パスワード（確認）", type="password")
        code = st.text_input("登録の合言葉", type="password") if auth.SIGNUP_CODE else ""
        go = st.form_submit_button("登録する", type="primary")
    st.caption("パスワードそのものは保存しません（照合用のハッシュだけを保存します）。パスワードを忘れたときに、画面から再設定する機能はありません。")
    if go:
        try:
            st.session_state["user"] = auth.register(name, password, confirm, code)
        except ValueError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"登録に失敗しました: {e}")
        else:
            st.rerun()  # 登録できたら、そのままログインした状態にする


def logout():
    st.session_state.clear()


if not st.session_state.get("user"):
    if st.session_state.get("auth_page") == "signup":  # 式のまま書くと、Streamlit が戻り値（None）を画面に出す
        signup_page()
    else:
        login_page()
    st.stop()
user = st.session_state["user"]


# ---- 自分の手番（先手／後手）の切り替え（発案者の指示 2026-10-15） ----
# 手番を変えると、同じ印でも呼び名と突き合わせが変わる（例: 2手目の「予想外で驚いた」が「ほかにも指す手があった」になる）。
# そこで、いまの手番で付けた印と入力途中の内容は消さずに手番ごとに取っておき（side_stash）、変えた先の手番の分を出す。
# 元の手番に戻せば、取っておいた印が元どおり出る。印か入力途中の内容があるときは、変える前に確認する（confirm_side）
def switch_side(new):
    ss = st.session_state
    old = ss.get("side_now", new)
    if new != old:
        stash = ss.setdefault("side_stash", {})
        stash[old] = {"marks": ss.get("marks", {}), "drafts": ss.get("drafts", {})}
        back = stash.pop(new, None) or {"marks": {}, "drafts": {}}
        ss.update(marks=back["marks"], drafts=back["drafts"], alt_edit=None)
    ss.update(side=new, side_now=new, side_change=None)


def request_side(widget):
    """手番の欄（サイドバー side・印の入力画面 side_confirm・棋譜入力 side_input）が変わったとき。"""
    ss = st.session_state
    new, old = ss.get(widget), ss.get("side_now")
    if new is None or new == old:
        return
    if widget != "side_input" and (ss.get("marks") or ss.get("drafts")):
        ss[widget] = old  # 確認が済むまで、元の手番のままにしておく
        ss["side_change"] = new
    else:  # 棋譜入力では確認を出さない（まだ印の画面に入っていない。取っておくので、戻せば元に戻る）
        switch_side(new)


@st.dialog("自分の手番を変える", dismissible=False)
def confirm_side():
    ss = st.session_state
    if not ss.get("side_change"):  # ボタンを押した（ダイアログの中だけが描き直されるので、画面全体を描き直して閉じる）
        st.rerun()
    new, old = ss["side_change"], ss["side_now"]
    n = len(ss.get("marks", {}))
    kept = (ss.get("side_stash") or {}).get(new) or {}
    st.write(f"自分の手番を **{old}** から **{new}** に変えますか？")
    st.write(f"変えると、{old}として付けた印（{n}件）" + ("と入力途中の内容" if ss.get("drafts") else "")
             + f"は、いったん画面から外れます。消えるわけではありません。{old}に戻せば、元どおり表示されます。")
    st.write(f"以前に{new}として付けた印（{len(kept.get('marks', {}))}件）があるので、それを表示します。" if kept.get("marks")
             else f"{new}として、印を最初から付けます。")
    b1, b2 = st.columns(2)
    b1.button(f"{new}に変える", type="primary", on_click=switch_side, args=(new,), use_container_width=True)
    b2.button(f"やめる（{old}のまま）", on_click=lambda: ss.update(side_change=None), use_container_width=True)


with st.sidebar:
    st.markdown(f"ログイン中: **{user}**")
    # どの画面からでも最初の画面に戻れる（付けた印・解析結果・裏の処理はそのまま残る）
    st.button("最初の画面に戻る", on_click=lambda: st.session_state.update(mode="home"), use_container_width=True,
              disabled=st.session_state.get("mode", "home") == "home")
    st.button("ログアウト", on_click=logout, use_container_width=True)
    st.markdown("---")
    # 保存した対局を開き直すとき、手番と思考時間をその対局のものに戻せるよう、値を session_state に持つ
    st.session_state.setdefault("side", "先手")
    st.session_state.setdefault("ms", 400)
    st.session_state.setdefault("side_now", st.session_state["side"])
    my_side = regret.SIDES[st.radio("自分の手番", list(regret.SIDES), horizontal=True, key="side",
                                    on_change=request_side, args=("side",))]
    ms = int(st.number_input("1手あたりの思考時間（ミリ秒）", 0, 3000, step=100, key="ms",
                             help="0 にするとエンジンで解析せず、印とコメントだけを記録します。解析するときは 100〜3000。"
                                  "解析のあとで変えると、解析をやり直します（AIコーチとのやり取りも消えます）。"))
    if 0 < ms < 100:
        ms = 100
        st.caption("解析するときの思考時間は100ミリ秒以上です。100として扱います。")
    elif ms == 0:
        st.caption("思考時間が0なので、エンジンの解析はしません。")
    st.markdown("---")
    st.markdown("**使用ソフトウェア**")
    st.markdown("- 探索: やねうら王 (GPLv3)\n- 評価関数: 水匠5（たややん氏）\n- 棋譜処理: cshogi (GPLv3)\n"
                "- AI: Gemini（Vertex AI）+ ADK")
    st.caption("評価はエンジンによる参考値です。対局者名はAIに送りません。")
if st.session_state.get("side_change"):
    confirm_side()


def tool_text(name, args):
    return TOOL_LABEL.get(name, lambda a: name)(args)


# ---- 0. 棋譜入力モード（ふりかえりの途中でも行き来できる。戻っても印と解析結果は残す） ----
st.session_state.setdefault("mode", "home")  # home（初期画面）/ input（棋譜入力モード）/ study（ふりかえり）
st.session_state.setdefault("src", None)  # 読み込んだ棋譜 {"id", "digest", "name", "label", "data", "consent", "saved"}


def set_mode(mode):
    st.session_state["mode"] = mode


def read_input(up, text, played, resign=False) -> tuple[str, str, bytes]:
    """入力ゾーンの内容を（ファイル名, 表示名, 中身）にする。使えない入力は ValueError（文はそのまま画面に出す）。

    played: 盤面で入力した手順（USI）。
    """
    filled = [n for n, v in (("ファイル", up is not None), ("テキスト", bool(text.strip())), ("盤面", bool(played))) if v]
    if len(filled) > 1:
        raise ValueError(f"{'と'.join(filled)}の両方が入っています。どれか1つにしてください"
                         "（盤面の入力は「最初からにする」で消せます）")
    if played:
        name, label, data = "kifu.csa", "盤面で入力した棋譜", entry.to_csa(played, resign).encode("utf-8")
    elif up is not None:
        if not up.name.lower().endswith(OK_EXT):
            raise ValueError(f"対応していない種類のファイルです（{' / '.join(OK_EXT)} に対応しています）")
        name, label, data = up.name, up.name, up.getvalue()
    elif text.strip():
        name, label, data = "kifu." + detect_format(text), "貼り付けた棋譜", text.encode("utf-8")
    else:
        raise ValueError("棋譜ファイルを選ぶか、棋譜のテキストを貼り付けるか、盤面で手を入力してください")
    if len(data) > MAX_UPLOAD:
        raise ValueError("棋譜が大きすぎます（200KBまで）")
    load_kifu(data, name)  # 棋譜として読めなければ、ここで ValueError
    return name, label, data


def kifu_download(data: bytes, name: str, label: str, uid: str):
    """棋譜のダウンロード。ファイル名に押した時刻（秒まで）を付ける（例: kifu_20261014-153012.kif）。"""
    stem, ext = os.path.splitext(name)
    st.html(report.timed_download_link(data, stem, ext or ".kif", label, uid=uid), unsafe_allow_javascript=True)


def history(refresh=False) -> list[dict]:
    """この利用者が保存した対局の集計。保存先を毎回読まないよう、画面を開いているあいだ覚えておく。"""
    if refresh or "history" not in st.session_state:
        try:
            st.session_state["history"] = storage.load_summaries(user)
        except Exception:  # 読めなくても、ふりかえりは続ける
            st.session_state["history"] = []
    return st.session_state["history"]


def save_game(user_, src_, ms_, my_side_, rows_, marks_, chat_, actions_, revealed=True, alt_rows_=None, ext_=None):
    """この対局の集計と結果を保存する（あとから開き直せるように）。呼ぶのは、本人が保存に同意しているときだけ。

    revealed が False なら、印の入力がまだの対局（あとで続きから印を付ける）。rows_ が None なら、解析もまだ。
    alt_rows_: 迷った別の読みの評価（alts.key → 解析の行）。印の入力が終わった対局は、傾向まとめも作り直す。
    """
    day = datetime.now(JST).strftime("%Y-%m-%d")
    kifu_ = load_kifu(src_["data"], src_["name"])
    alt_rows_ = dict(alt_rows_ or {})
    if rows_:
        gap_ = regret.compare(rows_, marks_, my_side_, alts.summaries(kifu_, marks_, rows_, alt_rows_))
        summary = learn.summarize(rows_, gap_, day, kifu_)
    else:
        summary = learn.summarize_marks(marks_, len(kifu_.moves), my_side_, day,
                                        {m["ply"]: m["side"] for m in move_list(kifu_)})
    storage.save_json(user_, src_["id"], "summary", {**summary, "label": src_["label"], "pending": not revealed})
    storage.save_json(user_, src_["id"], "result",
                      {"label": src_["label"], "ms": ms_, "my_side": my_side_, "rows": rows_, "marks": marks_,
                       "chat": chat_, "actions": actions_, "revealed": revealed, "redone": bool(src_.get("redone")),
                       "alt_eval": alt_rows_, "ext_lines": dict(ext_ or {})})
    if revealed:
        trend_job.refresh_async(user_, AI_READY)


def start_pipe(kifu_, key_, ms_, src_, user_, marks_, my_side_, alt_cache_, ext_cache_):
    """棋譜を読み込んだら、裏で 解析 →（印の入力を待つ）→ AIコーチのふりかえり → 保存 まで進める。

    画面の操作を待たずに進み、別の画面に移っても止まらない。画面の部品には触らない（結果は pipe に置き、画面が取りに来る）。
    marks_ が None のあいだは印の入力中で、解析結果は画面に出さない。
    alt_cache_: 迷った別の読みの評価を入れる場所（画面と同じもの。{"key", "rows", "busy"}）。
    """
    pipe = {"key": key_, "rows": None, "error": None, "analyzed": False, "marks": marks_, "my_side": my_side_,
            "coach": None, "job": None, "taken": False, "done": False, "cancel": False}

    def work():
        try:
            pipe["rows"] = get_client().analyze_game(kifu_, ms_)["rows"] if ms_ else []
            if ms_ and AI_READY:
                from shogi_coach.session import CoachSession
                pipe["coach"] = CoachSession(kifu_, ms_, rows=pipe["rows"])
        except Exception as e:
            pipe.update(error=str(e), done=True)
            return
        pipe["analyzed"] = True
        if src_["consent"] and pipe["marks"] is None:  # 印の入力がまだでも、解析の結果は残す（あとで続きから印を付けられる）
            try:
                save_game(user_, src_, ms_, pipe["my_side"], pipe["rows"], src_.get("pending_marks", []), [], [], revealed=False)
            except Exception as e:
                src_["save_error"] = str(e)
        t0 = time.time()
        while pipe["marks"] is None:  # 印の入力が終わるのを待つ
            if pipe["cancel"] or time.time() - t0 > MARK_WAIT:
                pipe["done"] = True
                return
            time.sleep(0.5)
        try:
            rows_, chat_, actions_ = pipe["rows"], [], []
            if rows_ and alts.lines_of(pipe["marks"]):  # 迷った別の読みを先に評価する（AIコーチにも渡すため）
                alt_cache_["busy"] = True
                try:
                    alts.evaluate(get_client(), kifu_, pipe["marks"], ms_, alt_cache_["rows"], lambda: pipe["cancel"])
                finally:
                    alt_cache_["busy"] = False
            gap_ = regret.compare(rows_, pipe["marks"], pipe["my_side"],
                                  alts.summaries(kifu_, pipe["marks"], rows_, alt_cache_["rows"]))
            if pipe["coach"] and not pipe["cancel"]:
                job = {"prompt": REVIEW, "label": "AIコーチが局面を選んで調べています…", "tools": [], "answer": None,
                       "done": False}
                pipe["job"] = job
                try:
                    past_ = learn.aggregate([h for h in storage.load_summaries(user_) if h.get("game_id") != src_["id"]])
                    pipe["coach"].set_regret(gap_)
                    pipe["coach"].set_history(past_)
                    job["answer"] = pipe["coach"].ask(REVIEW, lambda n, a: job["tools"].append(tool_text(n, a)))
                except Exception as e:
                    job["answer"] = f"{COACH_ERROR}: {e}"
                job["done"] = True
                chat_, actions_ = [("user", REVIEW), ("assistant", job["answer"])], list(job["tools"])
            if src_["consent"] and not pipe["cancel"]:  # 画面を閉じていても、同意があれば結果を残す
                save_game(user_, src_, ms_, pipe["my_side"], rows_, pipe["marks"], chat_, actions_,
                          alt_rows_=alt_cache_["rows"], ext_=ext_cache_["rows"])
        except Exception as e:
            src_["save_error"] = str(e)
        pipe["done"] = True
    threading.Thread(target=work, daemon=True).start()
    return pipe


def open_game(game_id):
    """保存した対局を開き直す（解析はやり直さず、保存した結果・印・AIコーチとのやり取りを戻す）。"""
    ss = st.session_state
    try:
        g = storage.load_game(user, game_id)
        if not g or not g["result"]:
            raise ValueError("この対局の結果が保存されていません")
        if ss.get("pipe"):
            ss["pipe"]["cancel"] = True
        res, data = g["result"], g["data"]
        load_kifu(data, g["name"])
        digest, saved_ms = hashlib.sha256(data).hexdigest(), int(res.get("ms", 400))
        side = next((k for k, v in regret.SIDES.items() if v == res.get("my_side")), "先手")
        src = {"id": game_id, "digest": digest, "name": g["name"], "label": res.get("label") or game_id, "data": data,
               "saved": True, "consent": True, "save_error": None,
               "summary": ((digest, saved_ms), regret.SIDES[side], len(res.get("alt_eval") or {}),
                           len(res.get("ext_lines") or {}))}
        done = res.get("revealed", True)  # False なら、印の入力がまだ（続きから印を付ける。解析結果はまだ見せない）
        analyzed = res.get("rows") is not None
        src["pending_marks"] = list(res.get("marks", []))
        src["redone"] = bool(res.get("redone"))
        if not done:
            src["summary"] = None  # 印を付け終えたら、結果を保存し直す
        ss["alt_cache"] = {"key": (digest, saved_ms), "rows": dict(res.get("alt_eval") or {}), "busy": False}
        ss["ext_cache"] = {"key": (digest, saved_ms), "rows": dict(res.get("ext_lines") or {}), "busy": False}
        sides = {m["ply"]: m["side"] for m in move_list(load_kifu(data, g["name"]))}
        marks_ = {int(m["ply"]): {**m, "kind": regret.norm_kind(m.get("kind"), sides.get(int(m["ply"])) == regret.SIDES[side])}
                  for m in res.get("marks", [])}  # 以前の種類（regret）は、そのときの手番で読み替える
        ss.update(src=src, marks=marks_, drafts={},
                  revealed=done, mark_ply=0, ply_keep=0, key=(digest, saved_ms) if analyzed else None,
                  rows=res.get("rows") or [], coach=None,
                  chat=[tuple(c) for c in res.get("chat", [])], actions=list(res.get("actions", [])), saved=None,
                  note=None, job=None, pipe=None, ms=saved_ms, side=side, side_now=side, side_stash={},
                  side_change=None, mode="study", open_error=None)
    except Exception as e:
        ss["open_error"] = f"対局を開けませんでした: {e}"


def save_result():
    """同意があるとき、この対局の集計・解析結果・印・AIコーチとのやり取りを保存する（あとから開き直せるように）。"""
    save_game(user, src, ms, my_side, rows, list(marks.values()), st.session_state["chat"], st.session_state["actions"],
              alt_rows_=alt_cache["rows"], ext_=ext_cache["rows"])


def start_study(name, label, data, consent):
    """読み込んだ棋譜でふりかえりを始める。同じ棋譜なら印と解析結果を残し、違う棋譜なら最初からにする。"""
    digest = hashlib.sha256(data).hexdigest()
    src = st.session_state["src"]
    if not src or src["digest"] != digest:
        src = {"id": datetime.now(JST).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:12], "digest": digest, "name": name, "label": label, "data": data, "saved": None}
        st.session_state.update(src=src, marks={}, drafts={}, side_stash={}, revealed=False, mark_ply=0, ply_keep=0, key=None)
    st.session_state["entry_used"] = list(st.session_state.get("entry", []))  # この時点の盤面入力（次に別の棋譜を入れるとき、無視する）
    src.update(consent=consent, save_error=None)
    if consent and not src["saved"]:  # 保存は本人が同意したときだけ
        try:
            src["saved"] = storage.save_kifu(user, src["id"], name, data)
        except Exception as e:  # 保存に失敗しても、ふりかえりは続ける
            src["save_error"] = str(e)
    set_mode("study")


def resume_button(src):
    st.button(f"◀ ふりかえりに戻る（{src['label']}）", on_click=set_mode, args=("study",), use_container_width=True)


@st.dialog("対局の削除", dismissible=False)
def confirm_delete():
    h = st.session_state["delete"]
    st.write(f'{h.get("created", "")}　{h.get("label", "")}　{h.get("plies", "?")}手')
    st.write("この対局の棋譜・解析結果・印・AIコーチとのやり取りを、保存先から削除します。元に戻せません。削除しますか？")
    b1, b2 = st.columns(2)
    if b1.button("削除する", type="primary", use_container_width=True):
        try:
            storage.delete_game(user, h["game_id"])
            src = st.session_state["src"]
            if src and src["id"] == h["game_id"]:  # いま開いている対局なら、閉じる
                if st.session_state.get("pipe"):
                    st.session_state["pipe"]["cancel"] = True
                st.session_state.update(src=None, pipe=None, key=None)
            st.session_state["open_error"] = None
            trend_job.refresh_async(user, AI_READY)
        except Exception as e:
            st.session_state["open_error"] = f"削除に失敗しました: {e}"
        st.session_state["delete"] = None
        history(refresh=True)
        st.rerun()
    if b2.button("やめる", use_container_width=True):
        st.session_state["delete"] = None
        st.rerun()


def home():
    """初期画面。棋譜入力モードへの入口。"""
    st.markdown("1. 棋譜を入力する（ファイルを読み込む、テキストを貼り付ける、盤面で1手ずつ指す）\n"
                "2. 自分の手番を確かめ、自分の感覚で手に印を付ける（自分の手: 悪手だと思った／ほかにも指す手があった／好手だと思った、"
                "相手の手: 悪手だと思った／予想外で驚いた／好手だと思った）\n"
                "3. エンジンの解析と比べ、ラベルごとに局面とその後の読み筋をふりかえる")
    c1, c2 = st.columns(2)
    c1.button("棋譜を入力する", type="primary", on_click=set_mode, args=("input",), use_container_width=True)
    if st.session_state["src"]:
        with c2:
            resume_button(st.session_state["src"])
    games = sorted(history(), key=lambda h: h["game_id"], reverse=True)
    st.markdown(f"#### 保存した対局（{len(games)}局）")
    if st.session_state.get("open_error"):
        st.error(st.session_state["open_error"])
    if not games:
        st.caption("まだありません。棋譜入力で「保存に同意する」にチェックを入れて解析すると、ここから解析結果をいつでも開き直せます。")
    for h in games[:30]:
        c = h.get("counts", {})
        row = st.columns([6, 1, 1])
        row[0].markdown(f'**{h.get("created", "")}**　{h.get("label", "")}　{h.get("plies", "?")}手（{h.get("my_side", "")}）　'
                        f'印 {sum(c.get(k, 0) for k in regret.MARKED_KEYS)}／自分の悪手に気づけた {c.get("match", 0)}／'
                        f'気づかなかった自分の悪手 {c.get("data_only", 0)}')
        if h.get("pending"):
            row[0].caption("印の入力がまだです。「続ける」で、続きから印を付けられます（解析は済んでいます）。")
        row[1].button("続ける" if h.get("pending") else "開く", key=f'open_{h["game_id"]}', on_click=open_game,
                      args=(h["game_id"],), use_container_width=True)
        row[2].button("削除", key=f'del_{h["game_id"]}', use_container_width=True,
                      on_click=lambda g=h: st.session_state.update(delete=g))
    if st.session_state.get("delete"):
        confirm_delete()
    if games:
        st.caption("開くと、保存した解析結果・印・AIコーチとのやり取りをそのまま表示します（解析はやり直しません）。"
                   "ふりかえりノートのPDFも、そこからダウンロードできます。"
                   + ("保存から30日たった対局は自動的に削除されます。" if storage.BUCKET else ""))
    st.markdown("#### これまでの記録")
    agg = learn.aggregate(history())
    if not agg["games"]:
        st.caption("まだ記録がありません。棋譜の保存に同意して解析すると、ここに傾向がたまっていきます。")
        return
    m = st.columns(4)
    m[0].metric("保存した対局", f'{agg["games"]}局')
    m[1].metric("自分の悪手候補", agg["bad_moves"], help="エンジンの判定で、自分の悪手・疑問手・緩手だった手の合計")
    m[2].metric("うち、自分で気づけた数", agg["bad_noticed"], help="悪手候補のうち、解析を見る前に「悪手だと思った」印を付けていた数")
    m[3].metric("自分の好手候補", agg["good_moves"])
    t1, t2 = st.columns(2)
    with t1:
        st.markdown("**悪手だと思った自分の手で選んだ原因（多い順）**")
        if agg["tags"]:
            st.dataframe(pd.DataFrame([{"原因": t["tag"], "回数": t["count"]} for t in agg["tags"]]), hide_index=True,
                         use_container_width=True)
        else:
            st.caption("まだありません。")
    with t2:
        st.markdown("**悪手候補が出た段階**")
        st.dataframe(pd.DataFrame([{"段階": p, "悪手候補": n} for p, n in agg["bad_by_phase"].items()]), hide_index=True,
                     use_container_width=True)
    st.caption("保存に同意した対局だけを、決まった規則で数えています（AIの判断ではありません）。段階は、その対局の手数を3等分した"
               "序盤・中盤・終盤です。AIコーチは、この記録を見てふりかえりを書きます。"
               + ("保存から30日たった対局は、記録からも消えます。" if storage.BUCKET else ""))
    trend_section()


def load_trend():
    try:
        return storage.load_trend(user)
    except Exception:
        return None


@st.fragment(run_every=2.0 if trend_job.running(user) else None)
def trend_section():
    """大会に向けた傾向まとめ（対局を保存するたびに、裏で作り直す）。3ページ以内のPDFと印刷用ページ。"""
    from shogi_analyzer import trend as trend_rules
    st.markdown("#### 大会に向けた傾向まとめ")
    busy = trend_job.running(user)
    if st.session_state.get("trend_busy") and not busy:  # 作り終えた。1秒ごとの描き直しを止めるため、画面全体を描き直す
        st.session_state["trend_busy"] = False
        st.rerun()
    st.session_state["trend_busy"] = busy
    d = load_trend()
    if busy:
        st.info("傾向まとめを作り直しています…（保存した対局が増えるたびに、自動で作り直します）")
    if not d:
        if not busy:
            st.caption("まだありません。印の入力を終えた対局を保存すると、自動で作ります。")
            st.button("いま作る", on_click=trend_job.refresh_async, args=(user, AI_READY, True), key="trend_make")
        return
    st.caption(f'{d["created"]}　保存した対局 {d["games"]}局から作成')
    t1, t2 = st.columns(2)
    with t1:
        st.markdown("**記録から言えること**（決まった規則で数えた結果）")
        st.markdown("\n".join(f"- {f}" for f in d["facts"]))
    with t2:
        st.markdown("**AIの所感**（Gemini。記録に無い数字を含む行は除いています）")
        if d["ai"]:
            st.markdown("\n".join(f"- {b}" for b in d["ai"]))
        else:
            st.caption("AIの所感はありません。" + d.get("ai_error", ""))
    if d["examples"]:
        st.markdown("**局面例**（決まった規則で選んだもの。図はその手を指す直前の局面、赤い字は成り駒）")
        for row in range(0, len(d["examples"]), 2):
            cols = st.columns(2)
            for col, e in zip(cols, d["examples"][row:row + 2]):
                with col:
                    st.markdown(f"**{trend_rules.example_title(e)}**")
                    st.image(sfen_svg(e["sfen"]), width=330)
                    st.caption("　".join(f"{k}: {v}" for k, v in trend_rules.example_text(e)))
    ss = st.session_state
    if (ss.get("trend_doc") or (None,))[0] != d["digest"] + d["created"]:
        ss["trend_doc"] = (d["digest"] + d["created"], report.trend_pdf(d), report.trend_html(d))
    c1, c2, c3 = st.columns(3)
    c1.download_button("PDFをダウンロード", ss["trend_doc"][1], mime="application/pdf", file_name="keikou_matome.pdf",
                       on_click="ignore", use_container_width=True, key="trend_pdf")
    with c2:
        st.html(report.print_link(ss["trend_doc"][2]), unsafe_allow_javascript=True)
    c3.button("いま作り直す", on_click=trend_job.refresh_async, args=(user, AI_READY, True), key="trend_redo",
              disabled=busy, use_container_width=True,
              help="AIの所感も書き直します（Gemini を1回呼びます）。集計が変わっていなければ、ふだんは作り直しません。")


def entry_set(moves, sel=None, promo=None):
    st.session_state.update(entry=list(moves), entry_sel=sel, entry_promo=promo)


def entry_board() -> bool:
    """盤面での棋譜入力。盤の図（cshogi）のマスを押して指す（指せる手だけ）。投了のボタンが押されたら True。"""
    ss = st.session_state
    for k, v in (("entry", []), ("entry_sel", None), ("entry_promo", None)):
        ss.setdefault(k, v)
    played = ss["entry"]
    board, jp = entry.replay(played)
    turn = 0 if board.turn == cshogi.BLACK else 1
    sel, promo = ss["entry_sel"], ss["entry_promo"]
    goals = entry.targets(board, sel) if sel else set()
    st.markdown("**盤面で入力する**")
    st.caption("動かす駒を押し、次に行き先のマスを押すと1手進みます（持ち駒は盤の上下のボタン）。"
               "赤いマスが選んだ駒、緑のマスが行き先です。指せる手だけ入力できます。平手のみ。"
               "指し終えたら「投了」を押すと、そこまでの棋譜でふりかえりを始めます。")

    def hand(side):
        return [{"key": key, "label": sym + (str(n) if n > 1 else ""), "enabled": side == turn,
                 "selected": side == turn and sel == key} for key, sym, n in entry.hand(board, side)]
    before, _ = entry.replay(played[:-1])
    last = before.move_from_usi(played[-1]) if played else None
    ev = board_input(svg=entry.board_svg(board, last, sel, goals), geom=entry.GEOM, top=hand(1), bottom=hand(0),
                     locked=bool(promo), key="entry_board", default=None)
    if ev and ev.get("n") != ss.get("entry_seen"):  # 盤が押された（同じ知らせを2回処理しない）
        ss["entry_seen"] = ev["n"]
        entry_set(*entry.click(played, sel, str(ev.get("key", ""))))
        st.rerun()
    if promo:
        p1, p2, p3 = st.columns(3)
        p1.button("成る", on_click=entry_set, args=(played + [promo[1]],), type="primary", use_container_width=True)
        p2.button("成らない", on_click=entry_set, args=(played + [promo[0]],), use_container_width=True)
        p3.button("選び直す", on_click=entry_set, args=(played,), use_container_width=True)
    c1, c2, c3 = st.columns(3)
    c1.button("1手戻す", on_click=entry_set, args=(played[:-1],), disabled=not played, use_container_width=True)
    c2.button("最初からにする", on_click=entry_set, args=([],), disabled=not played, use_container_width=True)
    resign = c3.button("投了（ふりかえりを始める）", type="primary", disabled=not played or bool(promo),
                       use_container_width=True, help="ここまでの手順を棋譜にして、ふりかえりを始めます。")
    if not entry.legal(board):
        st.info("指せる手がありません（詰み）。「投了」を押すと、ここまでの手順でふりかえりを始めます。")
    st.caption(f"入力した手順（{len(jp)}手）: " + (" ".join(jp) if jp else "まだありません")
               + f"　／　次は{'☗先手' if turn == 0 else '☖後手'}の番です。")
    if played:
        kifu_download(entry.to_csa(played).encode("utf-8"), "kifu.csa", "盤面で入力した棋譜をダウンロード（CSA）", "entry")
    return resign


def begin(name, label, data, consent, how):
    """読み込んだ棋譜で始める。how: mark（印を付ける）/ quick（印を付けずに結果を見る）/ later（いったん閉じて、あとで印を付ける）。"""
    start_study(name, label, data, consent)
    if how == "quick":
        st.session_state["revealed"] = True
    if how == "later":
        st.session_state["close_after_start"] = True


@st.dialog("棋譜の保存", dismissible=False)
def confirm_save():
    """保存にチェックが無いまま進もうとしたときの確認。"""
    c = st.session_state["confirm"]
    later = c["how"] in ("later", "close")

    def go(consent):
        st.session_state["confirm"] = None
        if c["how"] == "close":  # 印の入力の途中で「いったん閉じる」を押した
            st.session_state["src"]["consent"] = True
            st.session_state["close_after_start"] = True
        else:
            begin(c["name"], c["label"], c["data"], consent, c["how"])
        st.rerun()
    if later:
        st.write("あとで続きから印を付けるには、棋譜の保存が必要です。保存して、いったん閉じますか？")
        b1, b2 = st.columns(2)
        if b1.button("保存して閉じる", type="primary", use_container_width=True):
            go(True)
        if b2.button("やめる", use_container_width=True):
            st.session_state["confirm"] = None
            st.rerun()
    else:
        st.write("保存にチェックが入っていないので、この棋譜と解析結果は保存されません（あとから開き直せません）。いいですか？")
        if st.button("はい（今回は保存しない）", use_container_width=True):
            go(False)
        if st.button("いいえ、棋譜を保存する", type="primary", use_container_width=True):
            go(True)
        if st.button("保存しない（この棋譜は本当にサーバー上に残りません）", use_container_width=True,
                     help="ログアウトするまで、この確認を出さずに保存しないで進めます。"):
            st.session_state["no_save_ok"] = True
            go(False)
        st.caption("保存しない場合、棋譜と結果は保存先に書き込みません。解析のあいだだけ処理用のメモリで扱い、ログアウトするか画面を閉じると消えます。"
                   "AIコーチには、指し手とエンジンの数値を送ります（対局者名は送りません）。")


def load_prefs() -> dict:
    try:
        return storage.load_prefs(user)
    except Exception:
        return {}


def keep_always():
    """「次回からも確認せずに保存する」を、利用者の設定として覚える。"""
    prefs = st.session_state.setdefault("prefs", {})
    prefs["always_save"] = bool(st.session_state["always_box"])
    try:
        storage.save_prefs(user, prefs)
    except Exception:
        pass


def kifu_zone():
    src = st.session_state["src"]
    st.markdown("#### 棋譜入力")
    c1, c2 = st.columns(2)
    c1.button("◀ 最初の画面に戻る", on_click=set_mode, args=("home",), use_container_width=True)
    if src:
        with c2:
            resume_button(src)
        st.caption("戻ると、付けた印と解析結果はそのまま残っています。別の棋譜を読み込むと、いまの印と解析結果は消えます。")
    with st.container(border=True):
        z1, z2 = st.columns([2, 3], gap="large")
        with z1:
            up = st.file_uploader("棋譜を読み込む（KIF / CSA のファイル）", key="kifu_file")
            text = st.text_area("または、棋譜のテキストを貼り付ける", height=220, key="kifu_text",
                                placeholder="将棋アプリで棋譜をコピーして、ここに貼り付けてください（KIF / CSA 形式）")
            if src:
                st.markdown(f"いま読み込んでいる棋譜: **{src['label']}**")
                kifu_download(src["data"], src["name"], "いまの棋譜をダウンロード", "current")
                st.caption("ファイルと貼り付けの欄は、画面を移ると空に戻ります。何も入れずに下のボタンを押すと、いまの棋譜を使います。")
        with z2:
            resign = entry_board()
        # 自分の手番は、棋譜を入れるときに必ず選ぶ（自分の手か相手の手かで、付けられるラベルと突き合わせが変わるため。
        # 発案者の指示 2026-10-15）。選んだ値はサイドバーの「自分の手番」と同じ side に入れる
        side_in = st.radio("あなたは先手ですか、後手ですか？（必須）", list(regret.SIDES), index=None, horizontal=True,
                           key="side_input", format_func=lambda k: f'{"☗" if k == "先手" else "☖"}{k}',
                           on_change=request_side, args=("side_input",),
                           help="自分の手か相手の手かで、付けられる印のラベルが変わります。印を付ける画面でも直せます。")
        prefs = st.session_state.setdefault("prefs", load_prefs())
        consent = st.checkbox("この棋譜を保存し、これまでの記録に加えることに同意する",
                              value=bool(prefs.get("always_save") or (src and src["consent"])))
        st.checkbox("次回からも確認せずに保存する（このチェックを入れたままにする）", value=bool(prefs.get("always_save")),
                    key="always_box", on_change=keep_always,
                    help="入れておくと、次にログインしたときも、上の同意のチェックが最初から入ります。外せば元に戻ります。")
        st.caption("同意した場合だけ、棋譜（対局者名を含みます）、付けた印とコメント、解析結果、AIコーチとのやり取りを保存します。"
                   "保存した対局は「保存した対局」からいつでも開き直せ、「これまでの記録」にも数えられます。"
                   + ("保存したものは30日で自動的に削除されます。" if storage.BUCKET else
                      f"保存先はこのコンピューターの {storage.where()} フォルダーで、自動では削除されません。")
                   + "同意しなくても、すべての機能を使えます。一度保存したものを、この画面から取り消すことはできません。")
        b1, b2, b3 = st.columns(3)
        start = b1.button("この棋譜でふりかえりを始める", type="primary", use_container_width=True,
                          help="まず自分の感覚で手に印を付けます。解析は裏で先に進めておきます。")
        quick = b2.button("印を付けずに、すぐ解析結果を見る", use_container_width=True,
                          help="棋譜だけで解析します。解析とAIコーチのふりかえりが自動で進みます。")
        later = b3.button("いったん閉じて、あとで印を付ける", use_container_width=True,
                          help="棋譜を保存して最初の画面に戻ります。解析は裏で進み、結果も保存されます。"
                               "あとで「保存した対局」の「続ける」から、手に印を付けられます。")
        if st.session_state.get("confirm"):
            confirm_save()
        if (resign or start or quick or later) and side_in is None:
            st.error("「あなたは先手ですか、後手ですか？」で、自分の手番を選んでください。")
        elif resign or start or quick or later:
            played = st.session_state["entry"]
            if (up is not None or text.strip()) and played == st.session_state.get("entry_used"):
                played = []  # 前の棋譜のときの盤面入力は、新しいファイルや貼り付けと重ねない
            try:
                if resign:  # 盤面で入力した手順だけを使う
                    name, label, data = read_input(None, "", st.session_state["entry"], resign=True)
                elif src and up is None and not text.strip() and (not played or played == st.session_state.get("entry_used")):
                    name, label, data = src["name"], src["label"], src["data"]  # 何も入れていなければ、いまの棋譜を使う
                else:
                    name, label, data = read_input(up, text, played)
            except Exception as e:
                st.error(f"棋譜を読み込めませんでした: {e}")
            else:
                how = "quick" if quick else "later" if later else "mark"
                if consent or (st.session_state.get("no_save_ok") and how != "later"):
                    begin(name, label, data, consent, how)
                else:  # 保存にチェックが無ければ、確かめる
                    st.session_state["confirm"] = {"name": name, "label": label, "data": data, "how": how}
                st.rerun()


if st.session_state["mode"] == "home":
    home()
    st.stop()
if st.session_state["mode"] == "input" or not st.session_state["src"]:
    kifu_zone()
    st.stop()

src = st.session_state["src"]
kifu = load_kifu(src["data"], src["name"])  # 入力ゾーンで読めることを確かめてある
base_name = os.path.splitext(src["name"])[0]
h1, h2, h3, h4 = st.columns([4, 1, 1, 1])
h4.button("最初の画面に戻る", key="home_top", on_click=set_mode, args=("home",), use_container_width=True)
h1.subheader(f"☗{kifu.names[0] or '先手'}　vs　☖{kifu.names[1] or '後手'}（{len(kifu.moves)}手）")
with h2:
    kifu_download(src["data"], src["name"], "棋譜をダウンロード", "study")
h3.button("棋譜入力に戻る", on_click=set_mode, args=("input",), use_container_width=True)
if src["save_error"]:
    st.caption(f"棋譜の保存に失敗しました（ふりかえりは続けられます）: {src['save_error']}")
elif src["saved"]:
    st.caption("この対局は、同意にもとづいて保存しています" + ("（30日で自動的に削除されます）。" if storage.BUCKET else "。")
               + "最初の画面の「保存した対局」から、解析結果をいつでも開き直せます。")

# ---- 1. 6つのラベルの印（本人の感覚。エンジンやAIは代わりに付けない） ----
# 答えを見ながらでは学習にならないので、入力を完了するまで解析結果を見せない（解析も始めない）。
marks = st.session_state["marks"]    # {手数: {"ply", "kind", "tags", "note"}}
drafts = st.session_state["drafts"]  # {手数: {"kind", "tags", "note"}} 入力途中の内容。別の手に移っても残す
moves = {m["ply"]: m for m in move_list(kifu)}

# 棋譜を読み込んだら、裏で解析を始める（思考時間を変えたら、その値でやり直す）。保存した対局を開き直したときは始めない
key = (src["digest"], ms)
# 迷った別の読みの評価（alts.key → 解析の行）。棋譜か思考時間が変わったら空にする。裏の処理と画面で同じものを使う
alt_cache = st.session_state.get("alt_cache")
if not alt_cache or alt_cache["key"] != key:
    alt_cache = st.session_state["alt_cache"] = {"key": key, "rows": {}, "busy": False}
# 伸ばした読み筋（service.ext_key → 指す前の局面からの USI の手順）。読み筋が短く途切れた局面の続きを読む
ext_cache = st.session_state.get("ext_cache")
if not ext_cache or ext_cache["key"] != key:
    ext_cache = st.session_state["ext_cache"] = {"key": key, "rows": {}, "busy": False}
pipe = st.session_state.get("pipe")
if st.session_state.get("key") != key and (not pipe or pipe["key"] != key):
    if pipe:
        pipe["cancel"] = True
    pipe = start_pipe(kifu, key, ms, src, user, list(marks.values()) if st.session_state["revealed"] else None, my_side,
                      alt_cache, ext_cache)
    st.session_state["pipe"] = pipe


def close_later():
    """いったん閉じる。棋譜と、ここまでの印（と、済んでいれば解析結果）を保存して最初の画面に戻る。解析は裏で続く。"""
    done = st.session_state.get("key") == key
    rows_ = st.session_state["rows"] if done else pipe["rows"] if pipe and pipe["key"] == key and pipe["analyzed"] else None
    src["pending_marks"] = list(marks.values())  # 裏の解析が終わったとき、この印と一緒に保存する
    try:
        if not src["saved"]:
            src["saved"] = storage.save_kifu(user, src["id"], src["name"], src["data"])
        save_game(user, src, ms, my_side, rows_, src["pending_marks"], [], [], revealed=False)
        history(refresh=True)
    except Exception as e:
        src["save_error"] = str(e)
    set_mode("home")


@st.dialog("ふりかえりをやり直す", dismissible=False)
def confirm_redo():
    st.write("印の入力からやり直します。エンジンの解析結果は残しますが、AIコーチとのやり取りは消えます。"
             "すでに解析結果を見ているので、この対局には「解析結果を見たあとで印を付け直した」と記録します。")

    def redo(keep):
        ss = st.session_state
        if not keep:
            ss["marks"] = {}
        if ss.get("pipe"):
            ss["pipe"]["cancel"] = True
        src.update(redone=True, summary=None)
        ss.update(revealed=False, drafts={}, chat=[], actions=[], job=None, coach=None, note=None, pipe=None, redo=None)
        st.rerun()
    if st.button("いまの印を残して、付け直す", type="primary", use_container_width=True):
        redo(True)
    if st.button("印をすべて消して、最初から付ける", use_container_width=True):
        redo(False)
    if st.button("やめる", use_container_width=True):
        st.session_state["redo"] = None
        st.rerun()


if st.session_state.pop("close_after_start", False):
    close_later()
    st.rerun()
if st.session_state.get("confirm"):
    confirm_save()


def mark_label(p, kind=None):
    """印の呼び名（例: 「自分の手・悪手だと思った」）。自分の手か相手の手かは、サイドバーの自分の手番で決まる。"""
    return regret.mark_title(kind or marks[p]["kind"], moves[p]["side"], my_side)


def mine(p) -> bool:
    return moves[p]["side"] == my_side


def mark_kind(p, d) -> str:
    """入力途中・印の種類を bad / other / good にそろえる（以前の regret も読み替える）。"""
    return regret.norm_kind(d.get("kind"), mine(p))


def move_text(p):
    return f'{p}手目 {moves[p]["side"]}{moves[p]["move_jp"]}' if p else "初期盤面"


def cur_ply() -> int:
    """選んでいる手数（0 は初期盤面）。ブラウザから表示用の文字列（"15手目 ▲…"）で戻ることがあるので、数に直す。"""
    # 棋譜入力モードにいるあいだは手を選ぶ欄が無く、その値が消えるので、控え（ply_keep）から戻す
    v = st.session_state.get("mark_ply", st.session_state.get("ply_keep", 0))
    if not isinstance(v, int):
        m = re.search(r"(\d+)手目", str(v))
        v = int(m.group(1)) if m else 0
    return min(max(v, 0), len(moves))


def step(d):
    st.session_state["mark_ply"] = min(max(cur_ply() + d, 0), len(moves))


def board_view() -> int:
    """手送りと局面図。選んでいる手数を返す。図は選んだ手を指した局面で、その手のマスに色が付く。"""
    st.session_state["mark_ply"] = cur_ply()
    b1, b2, b3 = st.columns([1, 3, 1])
    b1.button("◀ 前", on_click=step, args=(-1,), use_container_width=True, shortcut="Left")
    b3.button("次 ▶", on_click=step, args=(1,), use_container_width=True, shortcut="Right")
    with b2:
        # 表示文字列は印の有無で変えない（変えると、選択中の値がブラウザ側の古い文字列と合わなくなる）
        ply = st.selectbox("手を選ぶ", [0, *moves], key="mark_ply", label_visibility="collapsed",
                           format_func=move_text)
        ply = ply if isinstance(ply, int) else cur_ply()
    st.session_state["ply_keep"] = ply
    if ply in marks:
        st.caption(f'★ この手には印を付けています（{mark_label(ply)}）')
    st.image(board_svg(kifu, ply), width=460)
    st.caption((f"{move_text(ply)} を指した局面です（色の付いたマスがこの手）。" if ply else "対局開始の局面です。")
               + "キーボードの ← → でも手を送れます。")
    return ply


def pick_row(table_key, plies):
    """表で選んだ行の手を、局面図に出す。"""
    sel = st.session_state[table_key].selection.rows
    if sel:
        st.session_state["mark_ply"] = plies[sel[0]]


def pick_point():
    """形勢グラフで押した手を、局面図に出す。"""
    pts = st.session_state["wr_chart"].selection.get("pick") or []
    if pts:
        st.session_state["mark_ply"] = int(pts[0]["ply"])


def keep_draft(p, alt_lines=None):
    old = drafts.get(p) or marks.get(p) or {}
    # ラベルは最初は選ばれていない（None）。選ばれていなければ None のまま持つ（印にはならない）
    k = st.session_state.get(f"kind_{p}_{my_side}") if f"kind_{p}_{my_side}" in st.session_state else old.get("kind")
    drafts[p] = {"kind": regret.norm_kind(k, mine(p)) if k is not None else None,
                 "tags": list(st.session_state.get(f"tags_{p}", old.get("tags", []))),
                 "note": st.session_state.get(f"note_{p}", old.get("note", "")),
                 "alts": [list(a) for a in (old.get("alts", []) if alt_lines is None else alt_lines)]}


def alt_set(**kw):
    st.session_state["alt_edit"].update(kw)


def add_alt(p):
    """入力中の読みを、この手の迷った別の読みに加える（印のボタンを押すまでは入力途中）。"""
    ed, cur = st.session_state["alt_edit"], drafts.get(p) or marks.get(p) or {}
    lines = [list(a) for a in cur.get("alts", [])]
    try:
        lines.append(alts.clean_line(kifu, p, ed["moves"], lines))
    except ValueError as e:
        ed["error"] = str(e)
        return
    keep_draft(p, lines)
    ed.update(moves=[], sel=None, promo=None, error=None)


def drop_alt(p, i):
    lines = [list(a) for a in (drafts.get(p) or marks.get(p) or {}).get("alts", [])]
    keep_draft(p, lines[:i] + lines[i + 1:])


def alt_editor(p):
    """迷った別の読みの入力。その手を指す直前の局面の盤で、駒のマス → 行き先のマスを押して手順を入れる。

    エンジンの評価は、印の入力を完了したあとで見せる（ここでは何も評価しない）。
    """
    ss = st.session_state
    lines = (drafts.get(p) or marks.get(p) or {}).get("alts", [])
    sfen = alts.start_sfen(kifu, p)
    ed = ss.get("alt_edit")
    if not ed or ed["ply"] != p:
        ed = ss["alt_edit"] = {"ply": p, "moves": [], "sel": None, "promo": None, "error": None}
    board, jp = entry.replay(ed["moves"], sfen)
    turn = 0 if board.turn == cshogi.BLACK else 1
    sel, promo = ed["sel"], ed["promo"]
    full = len(ed["moves"]) >= alts.MAX_ALT_PLIES
    goals = entry.targets(board, sel) if sel else set()

    def hand(side):
        return [{"key": k, "label": sym + (str(n) if n > 1 else ""), "enabled": side == turn and not full,
                 "selected": side == turn and sel == k} for k, sym, n in entry.hand(board, side)]
    if ed["moves"]:
        before, _ = entry.replay(ed["moves"][:-1], sfen)
        last = before.move_from_usi(ed["moves"][-1])
    else:
        last = kifu.moves[p - 2] if p > 1 else None
    st.caption(f"{move_text(p)} を指す直前の局面です。実戦の手のかわりに迷った手から、"
               f"{alts.MAX_ALT_PLIES}手まで指せます（相手の応手も入れられます）。1つの印に{alts.MAX_ALTS}つまで。")
    ev = board_input(svg=entry.board_svg(board, last, sel, goals), geom=entry.GEOM, top=hand(1), bottom=hand(0),
                     locked=bool(promo) or full, key=f"alt_board_{p}", default=None)
    if ev and ev.get("n") != ss.get(f"alt_seen_{p}"):  # 盤が押された（同じ知らせを2回処理しない）
        ss[f"alt_seen_{p}"] = ev["n"]
        if not full:
            mv, sel_, promo_ = entry.click(ed["moves"], sel, str(ev.get("key", "")), sfen)
            ed.update(moves=mv, sel=sel_, promo=promo_, error=None)
        st.rerun()
    if promo:
        q1, q2, q3 = st.columns(3)
        q1.button("成る", key="alt_pro", on_click=alt_set, kwargs={"moves": ed["moves"] + [promo[1]], "sel": None, "promo": None},
                  type="primary", use_container_width=True)
        q2.button("成らない", key="alt_nopro", on_click=alt_set,
                  kwargs={"moves": ed["moves"] + [promo[0]], "sel": None, "promo": None}, use_container_width=True)
        q3.button("選び直す", key="alt_resel", on_click=alt_set, kwargs={"sel": None, "promo": None}, use_container_width=True)
    st.caption("入力中の読み: " + (" ".join(jp) if jp else "（まだありません）"))
    a1, a2, a3 = st.columns(3)
    a1.button("この読みを加える", key="alt_add", on_click=add_alt, args=(p,), type="primary", use_container_width=True,
              disabled=not ed["moves"] or len(lines) >= alts.MAX_ALTS)
    a2.button("1手戻す", key="alt_back", on_click=alt_set, kwargs={"moves": ed["moves"][:-1], "sel": None, "promo": None},
              disabled=not ed["moves"], use_container_width=True)
    a3.button("入力中の読みを消す", key="alt_clear", on_click=alt_set, kwargs={"moves": [], "sel": None, "promo": None},
              disabled=not ed["moves"], use_container_width=True)
    if ed.get("error"):
        st.error(ed["error"])
    for i, line in enumerate(lines):
        r1, r2 = st.columns([5, 1])
        r1.markdown(f"読み{i + 1}: " + " ".join(alts.line_jp(kifu, p, line)))
        r2.button("外す", key=f"alt_drop_{p}_{i}", on_click=drop_alt, args=(p, i), use_container_width=True)


def unmark(p):
    """印を取り消し、入力欄も空に戻す（迷った別の読みも消す）。"""
    marks.pop(p, None)
    drafts.pop(p, None)
    st.session_state["alt_edit"] = None
    st.session_state[f"kind_{p}_{my_side}"], st.session_state[f"tags_{p}"], st.session_state[f"note_{p}"] = None, [], ""


def pending() -> list[int]:
    """入力途中で、まだ印になっていない手。"""
    def differs(p, d):
        note = " ".join(d["note"].split())[:regret.MAX_NOTE]
        tags, lines = d["tags"], d.get("alts", [])
        if p not in marks:
            return bool(d.get("kind") or tags or note or lines)
        m = marks[p]
        kind = mark_kind(p, d) if d.get("kind") else mark_kind(p, m)
        return (kind, tags, note, lines) != (mark_kind(p, m), m["tags"], m["note"], m.get("alts", []))
    return sorted(p for p, d in drafts.items() if differs(p, d))


def marks_table():
    st.dataframe(
        pd.DataFrame([{"手数": p, "指し手": moves[p]["side"] + moves[p]["move_jp"],
                       "印": mark_label(p),
                       "原因": "、".join(marks[p]["tags"]), "コメント": marks[p]["note"],
                       "迷った別の読み": f'{len(marks[p].get("alts", []))}つ' if marks[p].get("alts") else ""}
                      for p in sorted(marks)]),
        hide_index=True, use_container_width=True)


if not st.session_state["revealed"]:
    st.markdown("#### 1. 自分の感覚で、手に印を付ける")
    # まず自分の手番を確かめる（自分の手か相手の手かで、選べるラベルが変わる）。サイドバーの「自分の手番」と同じ値
    st.session_state["side_confirm"] = st.session_state["side"]
    st.radio("あなたは先手ですか、後手ですか？", list(regret.SIDES), horizontal=True, key="side_confirm",
             on_change=request_side, args=("side_confirm",), format_func=lambda k: f'{"☗" if k == "先手" else "☖"}{k}（{kifu.names[0 if k == "先手" else 1] or k}）')
    st.caption("自分の手には「悪手だと思った／ほかにも指す手があった／好手だと思った」、相手の手には"
               "「悪手だと思った／予想外で驚いた／好手だと思った」の印を付けられます。印は気になった手にだけ付ければよく、"
               "ラベルを選ばない手は「印なし」です。原因・コメント・迷った別の読みは、どれも任意です。"
               "解析結果は、入力を完了してから表示します。"
               + ("エンジンの解析は、裏で先に進めています。" if ms else "思考時間が0なので、エンジンの解析はしません（印とコメントだけを記録します）。"))
    c_board, c_form = st.columns([1, 1], gap="large")
    with c_board:
        ply = board_view()
    with c_form:
        if ply == 0:
            st.info("手を進めて、印を付ける手を選んでください。「次 ▶」かキーボードの → で進みます。")
        else:
            # 入力欄は手ごとに持つ。別の手に移ると欄の値は消えるので、戻ったときは drafts を初期値にして出す
            cur = drafts.get(ply) or marks.get(ply) or {}
            who = "自分の手" if mine(ply) else "相手の手"
            st.markdown(f"**{move_text(ply)}**（{who}）")
            kinds = regret.MARK_KINDS
            # 最初は何も選ばない（発案者の指示 2026-10-15）。選ばない手は「印なし」で、全部の手に印を付ける必要はない
            kind = st.radio("この手をどう思いましたか？", kinds,
                            index=kinds.index(mark_kind(ply, cur)) if cur.get("kind") or ply in marks else None,
                            format_func=lambda k: regret.kind_label(k, moves[ply]["side"], my_side), key=f"kind_{ply}_{my_side}",
                            on_change=keep_draft, args=(ply,),
                            help="自分の手か相手の手かで、選べるラベルが変わります（上の「先手ですか、後手ですか？」で決まります）。")
            tags = st.multiselect("思い当たる原因（任意。複数選べます）", regret.CAUSE_TAGS,
                                  default=cur.get("tags", []), key=f"tags_{ply}", on_change=keep_draft, args=(ply,))
            note = st.text_area("コメント（任意）", value=cur.get("note", ""), max_chars=regret.MAX_NOTE,
                                key=f"note_{ply}", on_change=keep_draft, args=(ply,),
                                placeholder={"good": "例: 受けに回ったのが良かった",
                                             "other": "例: ３四飛と迷ったが、攻めを急いだ" if mine(ply) else "例: この手は全く読んでいなかった",
                                             "bad": "例: 攻め合いで勝てると思い込んでいた" if mine(ply) else "例: ここで咎められると思った"
                                             }.get(kind, "例: 気になったことを書いておく"))
            n_alts = len(cur.get("alts", []))
            with st.expander(f"迷った別の読み（{n_alts}つ）" if n_alts else "迷った別の読みを入れる（任意）",
                             expanded=bool(n_alts) or (st.session_state.get("alt_edit") or {}).get("ply") == ply
                             and bool(st.session_state["alt_edit"]["moves"])):
                alt_editor(ply)
            if ply in pending():
                st.caption("入力途中です（別の手に移っても残ります）。下のボタンを押すと印になります。")
            if kind is None:
                st.caption("印を付けるときは、上でラベルを選んでください。気にならない手は、何も選ばずに次の手へ進んでかまいません。")
            if st.button("印を更新する" if ply in marks else "この印を付ける", type="primary", disabled=kind is None):
                try:
                    same = [p for p, m in marks.items() if p != ply and mine(p) == mine(ply) and mark_kind(p, m) == kind]
                    if len(same) >= regret.MAX_MARKS:
                        raise ValueError(f"「{mark_label(ply, kind)}」の印は1局に{regret.MAX_MARKS}個までです")
                    lines = (drafts.get(ply) or marks.get(ply) or {}).get("alts", [])
                    marks[ply] = regret.clean_mark(ply, tags, note, len(kifu.moves), kind,
                                                   alts=[alts.clean_line(kifu, ply, a) for a in lines])
                    drafts.pop(ply, None)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            if ply in marks:
                st.button("この手の印を取り消す", on_click=unmark, args=(ply,))

        st.markdown(f"**付けた印（{len(marks)}件）**")
        if marks:
            marks_table()
        st.divider()
        if pending():
            st.warning("入力途中で、まだ印になっていない手があります: " + "、".join(f"{p}手目" for p in pending())
                       + "。印にするには、その手で「この印を付ける」を押してください。")
        if st.button("入力を完了して解析結果を見る" if ms else "入力を完了して記録する", type="primary", use_container_width=True):
            # 裏の処理がこの対局のふりかえりを持っていなければ（保存した対局の続きなど）、画面側で自動で始める
            st.session_state.update(revealed=True, auto_review=not (pipe and pipe["key"] == key))
            st.rerun()
        if st.button("いったん閉じる（あとで続きから印を付ける）", use_container_width=True,
                     help="ここまでの印と棋譜を保存して、最初の画面に戻ります。解析は裏で進み、結果も保存されます。"
                          "入力途中で、まだ印になっていない内容は保存されません。"):
            if src["consent"]:
                st.session_state["close_after_start"] = True
            else:
                st.session_state["confirm"] = {"how": "close"}
            st.rerun()
        st.caption("完了すると、6つのラベルごとに、エンジンの解析結果（悪手候補と好手候補、その後の読み筋）とあなたの印を並べて表示し、"
                   "AIコーチのふりかえりも自動で始めます。"
                   + ("" if marks else "印が無いまま進むこともできます。"))
    st.stop()

# ---- 2. エンジン解析の結果を受け取る（解析は裏で進めてある。印の入力が終わったので、ここから結果を見せる） ----
if st.session_state.get("key") != key:
    if pipe["marks"] is None:  # 印の入力が終わったことを、裏の処理に知らせる（AIコーチのふりかえりが始まる）
        pipe["my_side"] = my_side
        pipe["marks"] = list(marks.values())
    if pipe["error"]:
        st.error(f"エンジンの解析に失敗しました: {pipe['error']}")
        st.stop()
    if not pipe["analyzed"]:
        @st.fragment(run_every=1.0)
        def wait_engine():
            if pipe["analyzed"] or pipe["error"]:
                st.rerun()
            st.info("将棋エンジンで全手を解析しています…（裏で進めているので、別の画面に移っても止まりません）")
        wait_engine()
        st.stop()
    st.session_state.update(key=key, rows=pipe["rows"], coach=pipe["coach"], chat=[], actions=[], saved=None, note=None,
                            job=None)
rows = st.session_state["rows"]


def alt_missing() -> bool:
    return any(alts.key(p, line) not in alt_cache["rows"] for p, line in alts.lines_of(list(marks.values())))


def eval_alts():
    """迷った別の読みのうち、まだ評価していないものを裏で評価する（裏の処理がこの対局で評価するときは任せる）。"""
    if not rows or not alt_missing() or alt_cache["busy"] or (pipe and pipe["key"] == key and not pipe["done"]):
        return
    alt_cache["busy"] = True
    marks_ = list(marks.values())

    def work():
        try:
            alts.evaluate(get_client(), kifu, marks_, ms, alt_cache["rows"])
        finally:
            alt_cache["busy"] = False
    threading.Thread(target=work, daemon=True).start()


eval_alts()
alt_sums = alts.summaries(kifu, list(marks.values()), rows, alt_cache["rows"]) if rows else {}
gap = regret.compare(rows, list(marks.values()), my_side, alt_sums) if rows else None


def ext_wanted() -> list[int]:
    """読み筋を伸ばす手: いま局面図に出している手と、ノートに載る手（6つのラベルで印を付けた手と、自分の悪手候補）。"""
    if not gap:
        return []
    plies = [cur_ply()] + [m["ply"] for k in report.CARD_GROUPS for m in gap[k]]
    return [p for p in dict.fromkeys(plies) if p > 0]


def ext_missing() -> bool:
    return any(ext_key(p, w) not in ext_cache["rows"] for p in ext_wanted() for w in ("best", "actual"))


def extend_lines():
    """読み筋を伸ばす（裏で動かす。エンジンを1つの読み筋につき最大数回呼ぶ）。"""
    if not rows or ext_cache["busy"] or not ext_missing():
        return
    ext_cache["busy"] = True
    plies = ext_wanted()

    def work():
        try:
            extend_rows(get_client(), kifu, rows, plies, ext_cache["rows"])
        finally:
            ext_cache["busy"] = False
    threading.Thread(target=work, daemon=True).start()


extend_lines()
st.button("ふりかえりをやり直す（印を付け直す）", on_click=lambda: st.session_state.update(redo=True),
          help="印の入力からやり直します。エンジンの解析結果は残ります。")
if st.session_state.get("redo"):
    confirm_redo()
if src.get("redone"):
    st.caption("この対局は、解析結果を見たあとで印を付け直しています。")

if not rows:  # 思考時間 0。エンジンで解析せず、印とコメントだけを記録する
    st.markdown("#### 印の記録（エンジンの解析なし）")
    st.info("思考時間が0なので、エンジンの解析はしていません。サイドバーの思考時間を100以上にすると、この印のまま解析します。")
    if src["consent"] and src.get("summary") != (key, my_side):
        try:
            save_result()
            src["summary"] = (key, my_side)
            history(refresh=True)
        except Exception as e:
            src["save_error"] = str(e)
    c_board, c_marks = st.columns([1, 1], gap="large")
    with c_board:
        board_view()
    with c_marks:
        st.markdown(f"**付けた印（{len(marks)}件）**")
        if marks:
            marks_table()
        else:
            st.caption("印はありません。")
    st.stop()

alt_done = len(alt_cache["rows"])  # 評価が増えたら保存し直す
ext_done = len(ext_cache["rows"])  # 伸ばした読み筋が増えたら保存し直す（ノートも作り直す）
if src["consent"] and src.get("summary") != (key, my_side, alt_done, ext_done):  # 同意があれば、この対局の集計を記録に加える（手番を変えたら書き直す）
    try:
        save_result()
        src["summary"] = (key, my_side, alt_done, ext_done)
        history(refresh=True)
    except Exception as e:
        src["save_error"] = str(e)
past = learn.aggregate([h for h in history() if h.get("game_id") != src["id"]])  # 今回の対局は含めない


def finish(job):
    """裏で動かしたAIコーチへの依頼の結果を、やり取りに加える。"""
    st.session_state["chat"] += [("user", job["prompt"]), ("assistant", job["answer"])]
    st.session_state["actions"] += job["tools"]
    st.session_state["job"] = None
    if src["consent"]:  # 保存は本人が同意したときだけ
        try:
            save_result()
        except Exception as e:  # 保存に失敗しても画面は続ける
            src["save_error"] = str(e)


# 裏の処理が始めたAIコーチのふりかえりを、この画面の依頼として受け取る
if pipe and pipe["key"] == key and pipe["job"] and not pipe["taken"]:
    pipe["taken"] = True
    st.session_state["job"] = pipe["job"]
auto_pending = bool(pipe and pipe["key"] == key and pipe["coach"] and not pipe["taken"] and not pipe["done"])

# 終わった依頼は、画面を描く前に取り込む（ふりかえりノートにも、その回のうちに入るように）
if st.session_state.get("job") and st.session_state["job"]["done"]:
    finish(st.session_state["job"])
df = pd.DataFrame(rows)
df["judge"] = [r["label"] or ("好手候補" if r.get("good") else "") for r in rows]  # グラフの点

LINE_NAMES = {"best": "最善手からの読み筋", "actual": "実戦の手からの読み筋"}


def open_lines(p, which="best"):
    """読み筋のポップアップを開く（開く手と、最初に見せる読み筋）。"""
    st.session_state.update(line_pop=p, line_which=which, line_step=0)


def close_lines():
    st.session_state["line_pop"] = None


def line_step(d, n):
    st.session_state["line_step"] = min(max(st.session_state.get("line_step", 0) + d, 0), n)


@st.dialog("読み筋を盤で見る", width="large", on_dismiss=close_lines)
def line_dialog():
    """最善手・実戦の手からの読み筋を、1手ずつ盤で見る（エンジンは呼ばない。伸ばした読み筋があればそれを使う）。"""
    ss = st.session_state
    p = ss["line_pop"]
    r = next((r for r in rows if r["ply"] == p), None)
    pos = line_positions(kifu, r, ext_cache["rows"]) if r else {"best": [], "actual": []}
    choices = [w for w in LINE_NAMES if pos[w]]
    st.markdown(f"**{move_text(p)}** を指す直前の局面から（エンジンの判定: {regret.verdict(r) if r else '解析なし'}）")
    if not choices:
        st.info("この手には読み筋がありません。")
        return
    if ss.get("line_which") not in choices:
        ss["line_which"] = choices[0]
    which = st.radio("読み筋", choices, format_func=LINE_NAMES.get, horizontal=True, key="line_which",
                     on_change=lambda: ss.update(line_step=0), label_visibility="collapsed")
    steps = pos[which]
    n = len(steps) - 1
    i = min(ss.get("line_step", 0), n)
    c1, c2, c3, c4 = st.columns([1, 1, 3, 1])
    c1.button("最初", key="line_first", on_click=line_step, args=(-n, n), disabled=i == 0, use_container_width=True)
    c2.button("◀ 前", key="line_prev", on_click=line_step, args=(-1, n), disabled=i == 0, use_container_width=True)
    c4.button("次 ▶", key="line_next", on_click=line_step, args=(1, n), disabled=i == n, use_container_width=True)
    c3.markdown(f"**{i}手目 / {n}手**　{steps[i]['move_jp'] or '（指す前の局面）'}")
    st.image(position_svg(steps[i]["sfen"], steps[i]["move_usi"], steps[i]["before"]), width=460)
    st.markdown("　".join((f"**[{k}] {s['move_jp']}**" if k == i else f"[{k}] {s['move_jp']}")
                         for k, s in enumerate(steps) if k))
    st.caption("色の付いたマスが、いま指した手です。読み筋は、エンジンが読んだ想定手順です（その先の展開の一例。最大12手）。"
               "読み筋の続きをエンジンで読んでいる途中なら、閉じて開き直すと伸びていることがあります。")


# PC では、上段の左に形勢グラフと局面図、右にAIコーチを並べ、表はその下に全幅で置く（発案者の指示 2026-10-14。空白を作らない）。
# スマホの幅では Streamlit が横並びを縦に積むので、局面図 → AIコーチ → 表 の順になる
if st.session_state.get("coach_pick") is not None:  # 講評の「N手目」が押された（手を選ぶ欄を描く前に移す）
    st.session_state["mark_ply"] = st.session_state.pop("coach_pick")
top_l, right = st.columns([3, 2], gap="large")
bottom = st.container()
with bottom:
    st.markdown("#### ラベルごとのふりかえり")
    if not regret.has_marks(gap):
        st.info("印を付けずに完了しました。各ラベルの「データだけ」は、エンジンが見つけた悪手候補・好手候補です。")
    secs = regret.sections(gap)
    pct_cols = {c: st.column_config.NumberColumn(format="percent") for c in ("勝率の下落", "次善手との差")}
    for sec, tab in zip(secs, st.tabs([f'{s["title"]}（{len(s["items"])}）' for s in secs])):
        with tab:
            good_sec = sec["kind"] == "good"
            st.caption("　".join(f"{name}: {tip}" if name else tip for _, name, tip in sec["groups"]))
            if sec["counts"]:
                for col, (name, n) in zip(st.columns(len(sec["counts"])), sec["counts"]):
                    col.metric(name, n)
            if not sec["items"]:
                st.caption("この対局にはありません。")
                continue
            table = [{"区分": m["group"] or "印", "手数": m["ply"], "指し手": m["side"] + m["move_jp"],
                      "エンジンの判定": regret.verdict(m), "勝率の下落": m["winrate_drop"],
                      **({"次善手との差": m["good_gap"]} if good_sec else {}), "最善手": m["side"] + m["best_before_jp"],
                      "原因": "、".join(m.get("tags", [])), "コメント": m.get("note", "")} for m in sec["items"]]
            st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True, column_config=pct_cols,
                         key=f'sec_{sec["key"]}', selection_mode="single-row",
                         on_select=lambda k=f'sec_{sec["key"]}', t=table: pick_row(k, [g["手数"] for g in t]))
            # 局面と、その後の読み筋（印を付けた手と、自分の悪手候補。ふりかえりノートと同じ手）
            shown_items = [m for m in sec["items"] if m["group_key"] in report.CARD_GROUPS]
            for m in shown_items:
                r_ = next((r for r in rows if r["ply"] == m["ply"]), None)
                lines = lines_jp(kifu, r_, ext_cache["rows"]) if r_ else {"best": [], "actual": []}
                st.markdown(f'**{m["ply"]}手目 {m["side"]}{m["move_jp"]}**（{m["group"] or "印"}・{regret.verdict(m)}、'
                            f'勝率の下落 {m["winrate_drop"]:.0%}）')
                out = [f"- 最善手からの読み筋（{len(lines['best'])}手）: " + " ".join(lines["best"])] if lines["best"] else []
                if lines["actual"] and not m["was_best"]:
                    out.append(f"- 実戦の手からの読み筋（{len(lines['actual'])}手）: " + " ".join(lines["actual"]))
                out += [f"- 迷った読み{i + 1}: " + alts.describe(a) for i, a in enumerate(m.get("alts", []))]
                st.markdown("\n".join(out) if out else "- 読み筋はありません。")
                if lines["best"] or lines["actual"]:
                    st.button("読み筋を盤で見る", key=f'lp_{sec["key"]}_{m["ply"]}', on_click=open_lines, args=(m["ply"],),
                              help="最善手・実戦の手からの読み筋を、ポップアップで1手ずつ盤で確かめます。")
            if shown_items:
                st.caption("表の行を押すと、その手の局面を上の局面図に出します。読み筋は、エンジンが読んだ想定手順です（その先の展開の一例。最大12手）。")
            if good_sec:
                st.caption(f"好手候補は、エンジンの最善手を指し、次善手なら勝率が{GOOD_GAP:.0%}以上低かった手です。"
                           "取り返すしかない局面の当然の手も入ります。")
    st.caption("区分は決まった規則で分けています（AIの判断ではありません）。勝率の下落は指した側から見た値で、"
               "エンジンによる参考値です。思考時間が短いと変わることがあります。")

    if alts.lines_of(list(marks.values())):
        st.markdown("#### 迷った別の読み")

        @st.fragment(run_every=1.0 if alt_missing() else None)
        def alt_view():
            sums = alts.summaries(kifu, list(marks.values()), rows, alt_cache["rows"])
            if len(alt_cache["rows"]) != alt_done or (
                    alt_missing() and not alt_cache["busy"] and not (pipe and pipe["key"] == key and not pipe["done"])):
                st.rerun()  # 評価が進んだ（ノートと保存に入れる）か、評価を始める。画面全体を描き直す
            for p in sorted(sums):
                st.markdown(f"**{move_text(p)}**（{mark_label(p)}）")
                for i, a in enumerate(sums[p]):
                    st.markdown(f"- 読み{i + 1}: " + alts.describe(a))
            if alt_missing():
                st.caption("迷った別の読みを、エンジンで評価しています…（1つの読みに、手数 + 1 回の解析をします）")
            elif sums.keys() and any(a.get("steps") for v in sums.values() for a in v):
                with st.expander("読みの1手ずつの判定"):
                    st.dataframe(pd.DataFrame([{"手数": p, "読み": i + 1, "指し手": stp["move_jp"],
                                                "エンジンの判定": stp["label"] or ("最善手と同じ" if stp["was_best"] else "判定なし"),
                                                "勝率の下落": stp["winrate_drop"], "最善手": stp["best_jp"]}
                                               for p in sorted(sums) for i, a in enumerate(sums[p])
                                               for stp in a.get("steps", [])]),
                                 hide_index=True, use_container_width=True,
                                 column_config={"勝率の下落": st.column_config.NumberColumn(format="percent")})
            st.caption("勝率は、印を付けた手を指す側から見た値です。読みの終わりの局面をエンジンで読み、実戦の手の後の局面と比べています"
                       f"（差が{alts.CLOSE:.0%}未満なら「同じくらい」。決まった規則で、AIの判断ではありません）。")
        alt_view()

    with top_l:
        st.markdown("#### 形勢グラフ（先手の勝率）")
        base = alt.Chart(df).encode(x=alt.X("ply:Q", title="手数"))
        line = base.mark_line().encode(
            y=alt.Y("winrate_sente:Q", title="先手勝率", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%")),
            tooltip=["ply", "side", "move_jp", alt.Tooltip("winrate_sente:Q", format=".0%")],
        )
        points = base.transform_filter(alt.datum.judge != "").mark_point(filled=True, size=90).encode(
            y="winrate_sente:Q",
            color=alt.Color("judge:N", title="判定",
                            scale=alt.Scale(domain=["悪手", "疑問手", "緩手", "好手候補"],
                                            range=["#c0392b", "#e08e0b", "#3f8f6b", "#2e6fba"])),
            tooltip=["ply", "side", "move_jp", "judge", "best_before_jp"],
        )
        mid = alt.Chart(pd.DataFrame({"y": [0.5]})).mark_rule(strokeDash=[4, 4], opacity=0.5).encode(y="y:Q")
        # グラフを押すと、いちばん近い手を選ぶ（見えない点を全手に置いて、押した位置を拾う）
        pick = alt.selection_point(name="pick", fields=["ply"], on="click", nearest=True, empty=False)
        hit = base.mark_point(size=80, opacity=0).encode(y="winrate_sente:Q").add_params(pick)
        now = alt.Chart(pd.DataFrame({"ply": [cur_ply()]})).mark_rule(color="#888", strokeWidth=1.5).encode(x="ply:Q")
        st.altair_chart((mid + now + line + points + hit).properties(height=300), use_container_width=True,
                        key="wr_chart", on_select=pick_point)

        st.markdown("#### 局面図")
        st.caption("形勢グラフ、AIコーチの講評の「N手目」、または下の表（ラベルごとのふりかえり・悪手候補）の行を押すと、"
                   "その手の局面を表示します。グラフの縦線が表示中の手です。")
        shown = board_view()
        r = next((r for r in rows if r["ply"] == shown), None)
        if r:
            st.caption(f'エンジンの判定: {regret.verdict(r)}（勝率の下落 {r["winrate_drop"]:.0%}）　'
                       f'エンジンの最善手: {r["side"]}{r["best_before_jp"]}')
            @st.fragment(run_every=1.0 if ext_missing() else None)
            def line_view():
                if len(ext_cache["rows"]) != ext_done or (ext_missing() and not ext_cache["busy"]):
                    st.rerun()  # 読み筋が伸びた（ノートと保存にも入れる）か、伸ばす仕事を始める
                lines = lines_jp(kifu, r, ext_cache["rows"])
                for label, line in (("最善手からの読み筋", lines["best"]), ("実戦の手からの読み筋", lines["actual"])):
                    if line:
                        st.markdown(f"**{label}**（{len(line)}手）: " + " ".join(line))
                if ext_missing():
                    st.caption("読み筋の続きを、エンジンで読んでいます…")
                if lines["best"] or lines["actual"]:
                    if st.button("読み筋を盤で見る", key="lp_main",
                                 help="最善手・実戦の手からの読み筋を、ポップアップで1手ずつ盤で確かめます。"):
                        open_lines(r["ply"])
                        st.rerun()
                if not ext_missing() and (lines["best"] or lines["actual"]):
                    st.caption("読み筋は、エンジンが読んだ想定手順です（その先の展開の一例）。短く途切れたときは、"
                               "終わりの局面を読み直して続きをつなげています（最大12手）。")
            line_view()

    st.markdown("#### 悪手候補")
    bad = df[df.label != ""][["ply", "side", "move_jp", "label", "winrate_drop", "best_before_jp"]]
    if bad.empty:
        st.success("しきい値を超える悪手はありませんでした。")
    else:
        st.dataframe(
            bad.rename(columns={"ply": "手数", "side": "", "move_jp": "指し手", "label": "判定",
                                "winrate_drop": "勝率の下落", "best_before_jp": "最善手"}),
            hide_index=True, use_container_width=True,
            column_config={"勝率の下落": st.column_config.NumberColumn(format="percent")},
            key="bad_table", selection_mode="single-row", on_select=lambda: pick_row("bad_table", [int(p) for p in bad.ply]),
        )

    buf = io.StringIO()
    df.drop(columns="judge").to_csv(buf, index=False)
    st.download_button("解析結果をCSVでダウンロード", buf.getvalue().encode("utf-8-sig"),
                       file_name=f"{base_name}_analysis.csv", mime="text/csv")

    st.markdown("#### ふりかえりノート（印刷用）")
    chat = st.session_state["chat"]  # [(user, 依頼), (assistant, 講評), (user, 質問), (assistant, 回答), ...]
    review = "" if not chat or chat[1][1].startswith(COACH_ERROR) else chat[1][1]
    qa = [(chat[i][1], chat[i + 1][1]) for i in range(2, len(chat) - 1, 2) if not chat[i + 1][1].startswith(COACH_ERROR)]
    note_key = (key, my_side, review, tuple(qa), alt_done, ext_done)
    if (st.session_state.get("note") or (None,))[0] != note_key:
        d = report.collect(kifu, rows, gap, coach_text=review, qa=qa, ext=ext_cache["rows"],
                           created=datetime.now(JST).strftime("%Y-%m-%d 作成"))
        st.session_state["note"] = (note_key, report.to_pdf(d, kifu), report.to_html(d, kifu))
    n1, n2 = st.columns(2)
    n1.download_button("PDFをダウンロード", st.session_state["note"][1], mime="application/pdf",
                       file_name=f"{base_name}_furikaeri.pdf", on_click="ignore",
                       use_container_width=True)
    with n2:
        st.html(report.print_link(st.session_state["note"][2]), unsafe_allow_javascript=True)
    st.caption("6つのラベルごとの表と局面図、形勢グラフをA4にまとめます。エンジンの最善手とその後の読み筋は、後ろのページに図つきの回答として載せます。「印刷用ページを開く」は新しいタブで開きます。"
               + ("AIコーチのふりかえりも入っています。" if review else "AIコーチにふりかえってもらうと、その文章も入ります。")
               + (f"AIコーチへの質問と回答（{len(qa)}件）は末尾に入っています。" if qa else "AIコーチへの質問と回答は、末尾に入ります。"))

if st.session_state.get("line_pop"):
    line_dialog()

# ---- 3. AIコーチ ----
with right:
    st.markdown("#### AIコーチのふりかえり")
    if not AI_READY:
        st.info("AIコーチは、環境変数 GOOGLE_CLOUD_PROJECT（Google Cloud のプロジェクトID）が無いと使えません。"
                "設定してからアプリを起動し直してください。エンジンの解析結果は、このまま使えます。")
        st.stop()

    from shogi_coach.session import CoachSession

    if st.session_state.get("coach") is None:
        st.session_state["coach"] = CoachSession(kifu, ms, rows=rows)
    coach: CoachSession = st.session_state["coach"]

    def ask(prompt: str, label: str):
        """AIコーチへの依頼を裏で動かす。待っているあいだに別の操作をしても、やり取りが消えないようにする。"""
        job = {"prompt": prompt, "label": label, "tools": [], "answer": None, "done": False, "key": key}

        def work(coach=coach, gap=gap, past=past):
            try:
                coach.set_regret(gap)
                coach.set_history(past)
                job["answer"] = coach.ask(prompt, lambda name, args: job["tools"].append(tool_text(name, args)))
            except Exception as e:
                job["answer"] = f"{COACH_ERROR}: {e}"
            job["done"] = True
        st.session_state["job"] = job
        threading.Thread(target=work, daemon=True).start()

    job = st.session_state.get("job")

    if not st.session_state["chat"] and not job and not auto_pending and st.session_state.pop("auto_review", False):
        ask(REVIEW, "AIコーチが局面を選んで調べています…")  # 保存した対局の続きで印を付け終えたとき
        st.rerun()
    if not st.session_state["chat"] and not job and not auto_pending:  # 自動のふりかえりが無いとき（保存した対局を開いた、など）
        if st.button("AIコーチにふりかえってもらう", type="primary", use_container_width=True):
            ask(REVIEW, "AIコーチが局面を選んで調べています…")
            st.rerun()

    if st.session_state["chat"]:
        # 講評の「N手目」を押すと、横の局面図をその手にする。局面図で選んでいる手に触れている段落には色が付く
        ev = coach_text(messages=coach_view.messages(st.session_state["chat"], len(kifu.moves), REVIEW, COACH_ERROR),
                        current=cur_ply(), key="coach_text", default=None)
        if ev and ev.get("n") != st.session_state.get("coach_seen"):
            st.session_state["coach_seen"] = ev["n"]
            st.session_state["coach_pick"] = int(ev["ply"])
            st.rerun()
        st.caption("講評の「N手目」を押すと、局面図がその手に移ります。局面図で選んでいる手に触れている段落には色が付きます。")

    @st.fragment(run_every=1.0 if job or auto_pending else None)
    def waiting():
        """依頼が終わるのを待つ欄。ここだけを1秒ごとに描き直し、終わったら画面全体を描き直す。"""
        j = st.session_state.get("job")
        if not j:
            if auto_pending:
                if pipe["job"] or pipe["done"]:
                    st.rerun()
                st.caption("AIコーチのふりかえりを自動で始めます…")
            return
        if j["done"]:
            st.rerun()
        if j["prompt"] != REVIEW:
            with st.chat_message("user"):
                st.markdown(j["prompt"])
        with st.status(j["label"], expanded=True):
            for t in list(j["tools"]):
                st.write("・" + t)
            st.caption("別の操作をしても、ここでの依頼は続きます。")
    waiting()

    if st.session_state["chat"] and not job:
        if st.button("AIコーチのふりかえりをやり直す", help="いまの講評と質問・回答を消して、AIコーチにもう一度ふりかえってもらいます。"):
            st.session_state.update(chat=[], actions=[], coach=None, note=None, auto_review=True)
            st.rerun()
    if st.session_state["chat"]:
        with st.expander(f"エージェントの行動ログ（{len(st.session_state['actions'])}件）"):
            for a in st.session_state["actions"]:
                st.write("・" + a)

if st.session_state.get("chat") or st.session_state.get("job"):
    q = st.chat_input("この対局について質問（例: 15手目で３四飛ならどうだった？）",
                      disabled=bool(st.session_state.get("job")))
    if q:
        with right:
            ask(q, "確認しています…")
        st.rerun()
