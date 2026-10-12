"""PDF生成(ノート + 考える問題 + 解答編)。入力は構造化データ、出力は bytes。

日本語フォント: fonts/ に TrueType(.ttf)があれば埋め込む(環境変数 FONT_PATH でも指定可)。
なければ reportlab 組み込みのCIDフォント(HeiseiKakuGo-W5)を使う。CIDフォントは埋め込まれないため、
閲覧側に日本語フォントがない環境では表示されない可能性がある(docs/decisions.md D-5、U-6)。
フォントのライセンスは採用前に公式表記を確認すること。
"""
from __future__ import annotations

import io
import os
import pathlib
from typing import Optional

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from ..domain.board import FW, HAND_ORDER, KN
from ..domain.board_svg import hand_text, piece_label

ROOT = pathlib.Path(__file__).resolve().parents[2]
SIDE = {"b": "先手", "w": "後手"}
FEELING = {"regret": "悔しい", "unsure": "迷った", "ok": "問題なし"}
KIND = {"long": "長考", "low_remaining": "残り時間が少ない", "swing": "評価値の急落"}


def register_font() -> tuple[str, str]:
    """(フォント名, 方式) を返す。方式は "ttf"(埋め込み) か "cid"。"""
    path = os.environ.get("FONT_PATH")
    cands = [pathlib.Path(path)] if path else sorted((ROOT / "fonts").glob("*.ttf"))
    for p in cands:
        if p.exists():
            try:
                pdfmetrics.registerFont(TTFont("JP", str(p)))
                return "JP", "ttf"
            except Exception:  # 読めないフォントはCIDへ退避
                continue
    try:
        pdfmetrics.getFont("HeiseiKakuGo-W5")
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    return "HeiseiKakuGo-W5", "cid"


class _Doc:
    W, H = A4
    M = 40

    def __init__(self, title: str) -> None:
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=A4)
        self.c.setTitle(title)
        self.font, self.font_kind = register_font()
        self.y = self.H - self.M
        self.page_no = 1
        self.footer = ""

    def text(self, s: str, size: float = 10, x: Optional[float] = None, gap: float = 4, color=(0, 0, 0)) -> None:
        x = self.M if x is None else x
        maxw = self.W - self.M - x
        for line in self._wrap(s, size, maxw):
            self._need(size + gap)
            self.c.setFillColorRGB(*color)
            self.c.setFont(self.font, size)
            self.c.drawString(x, self.y - size, line)
            self.y -= size + gap
        self.c.setFillColorRGB(0, 0, 0)

    def _wrap(self, s: str, size: float, maxw: float) -> list[str]:
        out: list[str] = []
        for para in s.split("\n"):
            cur = ""
            for ch in para:
                if pdfmetrics.stringWidth(cur + ch, self.font, size) > maxw:
                    out.append(cur)
                    cur = ch
                else:
                    cur += ch
            out.append(cur)
        return out

    def _need(self, h: float) -> None:
        if self.y - h < self.M + 20:
            self.new_page()

    def space(self, h: float) -> None:
        self.y -= h

    def new_page(self) -> None:
        self._draw_footer()
        self.c.showPage()
        self.page_no += 1
        self.y = self.H - self.M

    def _draw_footer(self) -> None:
        self.c.setFont(self.font, 7)
        self.c.setFillColorRGB(0.35, 0.35, 0.35)
        for i, line in enumerate(self._wrap(self.footer, 7, self.W - 2 * self.M)[:2]):
            self.c.drawString(self.M, 24 - i * 9, line)
        self.c.drawRightString(self.W - self.M, 24, str(self.page_no))
        self.c.setFillColorRGB(0, 0, 0)

    def finish(self) -> bytes:
        self._draw_footer()
        self.c.save()
        return self.buf.getvalue()


def _draw_board(d: _Doc, pos: dict, persp: str, x: float, y_top: float, cell: float = 30) -> float:
    """局面図を描く。y_top は上端。描き終えた下端の y を返す。"""
    c = d.c
    opp = "w" if persp == "b" else "b"
    mark = {"b": "☗", "w": "☖"}
    d.c.setFont(d.font, 9)
    c.drawString(x, y_top - 10, f"{mark[opp]}持駒 {hand_text(pos['hands'].get(opp, {}))}")
    gy = y_top - 22  # 盤の上端
    c.setLineWidth(0.6)
    for i in range(10):
        c.line(x + i * cell, gy, x + i * cell, gy - 9 * cell)
        c.line(x, gy - i * cell, x + 9 * cell, gy - i * cell)
    c.setFont(d.font, 8)
    for col in range(9):
        f = 9 - col if persp == "b" else col + 1
        c.drawCentredString(x + col * cell + cell / 2, gy + 3, FW[f])
    for r in range(9):
        k = r + 1 if persp == "b" else 9 - r
        c.drawString(x + 9 * cell + 3, gy - r * cell - cell / 2 - 3, KN[k])
    lt = pos.get("lastTo")
    for p in pos["pieces"]:
        col = (9 - p["file"]) if persp == "b" else (p["file"] - 1)
        row = (p["rank"] - 1) if persp == "b" else (9 - p["rank"])
        cx = x + col * cell + cell / 2
        cy = gy - row * cell - cell / 2
        if lt and [p["file"], p["rank"]] == lt:
            c.setFillColorRGB(0.97, 0.85, 0.4)
            c.rect(x + col * cell, gy - (row + 1) * cell, cell, cell, stroke=0, fill=1)
        promoted = p["piece"] in ("と", "成香", "成桂", "成銀", "馬", "龍")
        c.setFillColorRGB(0.7, 0.15, 0.1) if promoted else c.setFillColorRGB(0, 0, 0)
        label = piece_label(p["side"], p["piece"])
        size = cell * 0.62 if len(label) == 1 else cell * 0.42
        c.saveState()
        c.translate(cx, cy)
        if p["side"] != persp:
            c.rotate(180)
        c.setFont(d.font, size)
        c.drawCentredString(0, -size * 0.35, label)
        c.restoreState()
    c.setFillColorRGB(0, 0, 0)
    c.setFont(d.font, 9)
    c.drawString(x, gy - 9 * cell - 14, f"{mark[persp]}持駒 {hand_text(pos['hands'].get(persp, {}))}")
    return gy - 9 * cell - 20


def _time_chart(d: _Doc, game: dict) -> None:
    """手ごとの消費時間の棒グラフ(自分=濃い青、相手=灰)。"""
    moves = game["moves"]
    spents = [m.get("spentSec") or 0 for m in moves]
    if not spents or max(spents) == 0:
        return
    w, h = d.W - 2 * d.M, 70
    d._need(h + 24)
    x0, ytop = d.M, d.y
    base = ytop - h - 6
    mx = max(spents)
    bw = w / len(moves)
    th = game["derived"].get("longThreshold")
    for m, s in zip(moves, spents):
        bh = h * s / mx
        mine = m["side"] == game["mySide"]
        d.c.setFillColorRGB(*((0.13, 0.33, 0.62) if mine else (0.65, 0.65, 0.65)))
        d.c.rect(x0 + (m["n"] - 1) * bw, base, max(bw - 0.6, 0.4), bh, stroke=0, fill=1)
    if th:
        ly = base + h * th / mx
        d.c.setStrokeColorRGB(0.8, 0.2, 0.1)
        d.c.setDash(2, 2)
        d.c.line(x0, ly, x0 + w, ly)
        d.c.setDash()
        d.c.setFillColorRGB(0.8, 0.2, 0.1)
        d.c.setFont(d.font, 7)
        d.c.drawString(x0 + w - 70, ly + 2, f"長考の目安 {int(th)}秒")
    d.c.setStrokeColorRGB(0, 0, 0)
    d.c.setFillColorRGB(0, 0, 0)
    d.c.setFont(d.font, 7)
    d.c.drawString(x0, base - 9, "1手目")
    d.c.drawRightString(x0 + w, base - 9, f"{len(moves)}手目   濃い青=自分 / 灰=相手   最大{mx}秒")
    d.y = base - 18


def build_pdf(game: dict, report: dict, marks: list[dict]) -> bytes:
    """report は run_report の output。"""
    my = game["mySide"]
    d = _Doc(f"ふりかえりノート {game['title']}")
    d.footer = report.get("disclaimer", "")
    der = game["derived"]

    # ---- 表紙・ノート ----
    d.text("ふりかえりノート", 20, gap=8)
    d.text(game["title"], 12)
    meta = game["meta"]
    res = game.get("result")
    res_s = "不明"
    if res:
        res_s = f"{res['n']}手目に{res['word']}"
        if res.get("winner"):
            res_s += f"({SIDE[res['winner']]}の勝ち)"
    d.text(f"日時: {meta.get('開始日時', '不明')} / 手合割: {meta.get('手合割', '不明')} / 持ち時間: {meta.get('持ち時間', '不明')}", 9)
    d.text(f"自分の手番: {SIDE[my]} / 手数: {len(game['moves'])} / 結果: {res_s}", 9)
    d.space(8)

    d.text("【データ】時間の使い方", 12, gap=6)
    ts = der["totalSec"]
    d.text(f"使用時間: 先手 {ts['b']}秒 / 後手 {ts['w']}秒", 9)
    if der["limitSec"]:
        re_ = der["remainingEnd"]
        d.text(f"終局時の残り: 先手 {re_['b']}秒 / 後手 {re_['w']}秒(持ち時間 {der['limitSec']}秒)", 9)
    ps = der["phaseShare"]
    d.text(f"消費時間の配分(序盤1〜30手 / 中盤31〜70手 / 終盤71手〜): 先手 {ps['b']}% / 後手 {ps['w']}%", 9)
    lt = der["longThinks"]
    if lt:
        d.text("時間を使った手(長考の目安以上): " + "、".join(
            f"{x['n']}手目({SIDE[x['side']]}・{x['spentSec']}秒)" for x in lt), 9)
    lr = der["lowRemaining"].get(my)
    if lr:
        d.text(f"自分の残り時間が{der['lowSec']}秒を切った最初の手: {lr}手目", 9)
    _time_chart(d, game)
    if not der["evalAvailable"]:
        d.text("評価値なし(この棋譜には評価値・読み筋が含まれていない)。時間を使った手は、悪手とは限らない。", 8, color=(0.4, 0.4, 0.4))
    d.space(6)

    d.text("【データ】感覚とのずれ", 12, gap=6)
    cmp_ = report["compare"]
    if report.get("noMarks"):
        d.text("本人の印がない。以下は時間のデータだけから作った。", 9)
    else:
        d.text(f"本人の印: {cmp_['markCount']}件", 9)
        for label, key in (("一致(印とデータが重なった)", "coincide"), ("感覚だけ(印はあるがデータでは目立たない)", "feelingOnly"),
                           ("時間の癖", "timeHabit")):
            xs = cmp_[key]
            if xs:
                d.text(f"{label}: " + "、".join(
                    f"{x['n']}手目" + (f"({'・'.join(x['causeTags'])})" if x.get("causeTags") else "") for x in xs), 9)
    if cmp_["dataOnly"]:
        d.text("データだけ(自分の手で時間を使ったが、印がない): " + "、".join(f"{x['n']}手目" for x in cmp_["dataOnly"]), 9)
    d.space(6)

    nar = report["narration"]
    d.text("【AI所感】" if nar["source"] == "ai" else "【規則による要約】(AIは使っていない)", 12, gap=6)
    d.text(nar["summary"], 9)
    for it in nar.get("items", []):
        d.text(f"・{it['n']}手目: {it['comment']}", 9)
    if nar.get("nextFocus"):
        d.text("次に意識すること", 10)
        for f in nar["nextFocus"]:
            d.text(f"・{f}", 9)
    d.space(6)

    if report.get("cards"):
        d.text("参照先(プロ棋士の解説。要約は登録者の言葉。内容は元の動画で確認する)", 12, gap=6)
        for cd in report["cards"]:
            d.text(f"{cd['proName']}「{cd['videoTitle']}」{cd.get('channel', '')}", 9)
            d.text(cd["url"], 8, color=(0.1, 0.2, 0.7))
            if cd.get("summaryOwnWords"):
                d.text(cd["summaryOwnWords"], 8)

    # ---- 問題 ----
    probs = report.get("problems", [])
    for i, p in enumerate(probs, start=1):
        d.new_page()
        origin = "本人の印" if p["origin"] == "user_mark" else "候補(本人の印ではない)"
        d.text(f"考える問題 {i} / {len(probs)}", 16, gap=6)
        d.text(f"{p['n']}手目の直前の局面 / 手番: {SIDE[p['toMove']]} / 出典: {origin}", 9)
        d.space(6)
        bottom = _draw_board(d, p["position"], my, d.M + 10, d.y)
        d.y = bottom - 6
        d.text(p["question"] + "?", 12, gap=6)
        d.text("最善手は未取得。正解を示す問題ではない。自分の見立てを書き、あとで別の手段(解析ソフトなど)で確かめる。", 8, color=(0.4, 0.4, 0.4))
        d.space(8)
        d.text("自分の見立て:", 9)
        for _ in range(6):
            d._need(20)
            d.c.setStrokeColorRGB(0.7, 0.7, 0.7)
            d.c.line(d.M, d.y - 10, d.W - d.M, d.y - 10)
            d.c.setStrokeColorRGB(0, 0, 0)
            d.y -= 20

    # ---- 解答編 ----
    if probs:
        d.new_page()
        d.text("解答編", 16, gap=8)
        d.text("実戦で指された手と、その次の手を載せる。実戦の手の良し悪しは判定していない。最善手は未取得。", 8,
               color=(0.4, 0.4, 0.4))
        d.space(6)
        for i, p in enumerate(probs, start=1):
            sp = f"(消費{p['playedSpentSec']}秒)" if p.get("playedSpentSec") is not None else ""
            nxt = f" / 次の手: {p['nextMove']}" if p.get("nextMove") else ""
            d.text(f"問題{i}({p['n']}手目): 実戦の手: {p['played']}{sp}{nxt}", 10)
            d.text("最善手: 未取得", 9, x=d.M + 12, color=(0.4, 0.4, 0.4))
    return d.finish()
