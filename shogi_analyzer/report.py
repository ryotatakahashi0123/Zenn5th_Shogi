"""ふりかえりノート（PDF と印刷用HTML）。エンジンもLLMも呼ばない。

画面に出ている数値と文章を並べ直すだけで、新しい数値は作らない。
PDF は日本語フォント（BIZ UDゴシック、SIL OFL 1.1）を埋め込むので、どの環境でも同じ形で印刷できる。
"""
from __future__ import annotations

import base64
import html
import io
import json
import re
from pathlib import Path

import cshogi
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table

from . import alts as alts_mod, regret, trend as trend_mod
from .kifu_analysis import GOOD_GAP, Kifu
from .service import PROMOTED_FILL, board_after, board_svg, lines_jp, to_svg

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT, FONT_B = "BIZUDGothic", "BIZUDGothic-Bold"

KINDS = [("match", "一致"), ("feeling_only", "感覚だけ"), ("data_only", "データだけ")]
GOOD_KINDS = [("good_match", "一致"), ("good_feeling_only", "感覚だけ"), ("good_data_only", "データだけ"),
              ("good_opponent", "相手の好手候補")]
GOOD_HEAD = ["区分", "手数", "指し手", "エンジンの判定", "次善手との差", "コメント"]
GOOD_NOTE = ("一致は、好手の印を付けた手がエンジンの最善手と同じ。感覚だけは、好手の印を付けたが最善手とは違う。"
             "データだけと相手の好手候補は、好手の印を付けていないエンジンの好手候補。"
             f"好手候補は、最善手を指し、次善手なら勝率が{GOOD_GAP:.0%}以上低かった手です（決まった規則で、AIの判断ではありません）。"
             "取り返すしかない局面の当然の手も入ります。エンジンによる参考値です。")
KIND_HELP = {"一致": "悔しかった手が、エンジンでも悪手・疑問手・緩手",
             "感覚だけ": "悔しかったが、エンジンでは勝率がほとんど下がっていない",
             "データだけ": "エンジンでは自分の悪手候補だが、悔しかった手の印を付けていない"}
RULE_NOTE = ("区分は決まった規則で分けています（AIの判断ではありません）。勝率の下落は指した側から見た値で、"
             "エンジン（やねうら王 + 水匠5）による参考値です。")
QA_NOTE = "AIコーチへの質問と、AI（Gemini）の回答です。回答はエンジンで確かめた結果をもとにしていますが、AIが書いた文章です。"
COACH_NOTE = "ここから下はAI（Gemini）が書いた文章です。上の表と図（エンジンの解析結果）とは分けて読んでください。"
BOARD_NOTE = ("図は、その手を指す直前の局面です（色の付いたマスは直前の手）。"
              "エンジンの最善手は、後ろの「回答」のページにあります。")
ANSWER_NOTE = ("図は、エンジン（やねうら王 + 水匠5）の最善手を指した局面です（色の付いたマスが最善手）。"
               "エンジンによる参考値で、ほかに良い手が無いという意味ではありません。"
               "読み筋は、エンジンが短い時間で読んだ想定手順です（その先の展開の一例）。")
MARK_FILL = {"悪手": "#000000", "疑問手": "#888888", "緩手": "#ffffff"}  # 白黒印刷でも区別できる濃淡
HEAD = ["区分", "手数", "指し手", "エンジンの判定", "勝率の下落", "原因", "コメント"]  # 最善手は「回答」に書く
MEMO = "気づいたこと・次に直すこと"


# ---------------- 内容（PDFとHTMLで共通） ----------------

def collect(kifu: Kifu, rows: list[dict], gap: dict, title: str = "", coach_text: str = "",
            created: str = "", qa: list[tuple[str, str]] = ()) -> dict:
    """rows: 全手解析の行。gap: regret.compare の結果。coach_text: AIコーチの講評（Markdown。無ければ空）。

    qa: AIコーチへの（質問, 回答）の並び。ノートの末尾に載せる。
    """
    by_ply = {r["ply"]: r for r in rows}

    def item(m, label):
        r = by_ply[m["ply"]]
        lines = lines_jp(kifu, r) if "move_usi" in r else {"best": [], "actual": []}
        return {**m, "kind": label, "best_usi": r["best_before_usi"],
                "best_line": " ".join(lines["best"]), "actual_line": " ".join(lines["actual"])}
    items = sorted([*(item(m, label) for key, label in KINDS for m in gap[key]),
                    *(item(m, regret.SURPRISE) for m in gap.get("surprise", []))], key=lambda m: m["ply"])
    return {
        "title": title, "created": created, "plies": len(kifu.moves),
        "names": [kifu.names[0] or "先手", kifu.names[1] or "後手"],
        "my_side": {"▲": "先手", "△": "後手"}.get(gap["my_side"], ""),
        "counts": [(label, len(gap[key])) for key, label in KINDS],
        "items": items,
        "good": sorted(({**m, "kind": label} for key, label in GOOD_KINDS for m in gap.get(key, [])),
                       key=lambda m: m["ply"]),
        "series": [(r["ply"], r["winrate_sente"], r["label"]) for r in rows],
        "coach": md_blocks(coach_text),
        "qa": [(" ".join(q.split()), md_blocks(a)) for q, a in qa],
    }


verdict = regret.verdict


def good_row(m: dict) -> list[str]:
    return [m["kind"], str(m["ply"]), m["side"] + m["move_jp"], verdict(m), pct(m["good_gap"]), m.get("note", "")]


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def table_row(m: dict) -> list[str]:
    return [m["kind"], str(m["ply"]), m["side"] + m["move_jp"], verdict(m), pct(m["winrate_drop"]),
            "、".join(m.get("tags", [])), m.get("note", "")]


def meta_line(d: dict) -> str:
    parts = [f'☗{d["names"][0]}　vs　☖{d["names"][1]}', f'{d["plies"]}手']
    if d["my_side"]:
        parts.append(f'自分の手番: {d["my_side"]}')
    return "　／　".join(parts + [x for x in (d["title"], d["created"]) if x])


def md_blocks(text: str) -> list[tuple[str, str]]:
    """Markdownを (種類, 文) の並びにする。種類は h（見出し）/ li（箇条書き）/ p（本文）。"""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or re.fullmatch(r"[-|:*_ ]+", s):
            continue
        if m := re.match(r"#{1,6}\s*(.+)", s):
            out.append(("h", m[1].strip("# ")))
        elif m := re.match(r"[-*・]\s+(.+)", s):
            out.append(("li", m[1]))
        else:
            out.append(("p", s))
    return out


def esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def inline(s: str) -> str:
    """本文1行を、HTMLと reportlab の両方で使えるマークアップにする（**太字** だけ対応）。"""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(s.replace("`", "")))


def chart_geom(series: list[tuple], w: float, h: float) -> dict:
    """形勢グラフの座標（左下が原点、単位は呼び出し側のもの）。"""
    left, right, top, bottom = 0.07 * w, 0.02 * w, 0.06 * h, 0.16 * h
    n = max([p for p, _, _ in series] + [1])
    fx = lambda p: left + (w - left - right) * (p - 1) / max(n - 1, 1)
    fy = lambda v: bottom + (h - top - bottom) * v
    step = 10 if n <= 100 else 20 if n <= 200 else 50
    return {
        "box": (left, bottom, w - left - right, h - top - bottom),
        "line": [(fx(p), fy(v)) for p, v, _ in series],
        "marks": [(fx(p), fy(v), lab) for p, v, lab in series if lab],
        "yticks": [(fy(v), pct(v)) for v in (0, 0.5, 1)],
        "xticks": [(fx(p), str(p)) for p in [1] + list(range(step, n + 1, step))],
    }


# ---------------- 印刷用HTML ----------------

CSS = """
@page { size: A4; margin: 14mm; }
* { box-sizing: border-box; }
body { font-family: "BIZ UDPGothic", "Hiragino Kaku Gothic ProN", "Yu Gothic", Meiryo, "Noto Sans JP", sans-serif;
       color: #000; background: #fff; font-size: 10.5pt; line-height: 1.6; max-width: 182mm; margin: 0 auto;
       padding: 8mm 4mm; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1 { font-size: 18pt; margin: 0 0 2mm; }
h2 { font-size: 13pt; border-bottom: 1.5px solid #000; margin: 8mm 0 3mm; padding-bottom: 1mm; break-after: avoid; }
h3 { font-size: 11pt; margin: 4mm 0 1mm; break-after: avoid; }
p { margin: 1mm 0; } ul { margin: 1mm 0; padding-left: 6mm; }
.note { font-size: 8.5pt; color: #444; }
.bar { background: #eee; border: 1px solid #999; padding: 3mm; margin-bottom: 6mm; display: flex; gap: 4mm;
       align-items: center; }
.bar button { font-size: 12pt; padding: 2mm 6mm; cursor: pointer; }
.counts { display: flex; gap: 4mm; margin: 3mm 0; }
.counts div { flex: 1; border: 1px solid #000; padding: 2mm 3mm; }
.counts b { font-size: 16pt; margin-right: 1mm; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; }
th, td { border: 1px solid #666; padding: 1mm 1.5mm; text-align: left; vertical-align: top; }
th { background: #e6e6e6; white-space: nowrap; } thead { display: table-header-group; } tr { break-inside: avoid; }
.chart { width: 100%; height: auto; }
.card { display: flex; gap: 6mm; break-inside: avoid; border-top: 1px solid #999; padding: 4mm 0; }
.card svg { width: 72mm; height: auto; flex: none; }
.card .txt { flex: 1; } .card h3 { margin-top: 0; }
.newpage { break-before: page; }
.kind { border: 1px solid #000; padding: 0 2mm; font-size: 9pt; margin-left: 2mm; font-weight: normal; }
.memo { margin-top: 3mm; font-size: 9pt; } .memo i { display: block; border-bottom: 1px solid #999; height: 8mm; }
@media print { .bar { display: none; } body { padding: 0; max-width: none; } }
"""


def _chart_svg(series: list[tuple], w: int = 680, h: int = 200) -> str:
    g = chart_geom(series, w, h)
    x, y, bw, bh = g["box"]
    f = lambda v: f"{v:.1f}"
    out = [f'<svg class="chart" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-size="11">',
           f'<rect x="{f(x)}" y="{f(h - y - bh)}" width="{f(bw)}" height="{f(bh)}" fill="none" stroke="#000"/>']
    for ty, lab in g["yticks"]:
        out.append(f'<text x="{f(x - 5)}" y="{f(h - ty + 4)}" text-anchor="end">{lab}</text>')
    mid = g["yticks"][1][0]
    out.append(f'<line x1="{f(x)}" x2="{f(x + bw)}" y1="{f(h - mid)}" y2="{f(h - mid)}" stroke="#000" '
               'stroke-dasharray="4 4" stroke-width="0.6"/>')
    for tx, lab in g["xticks"]:
        out.append(f'<text x="{f(tx)}" y="{f(h - y + 14)}" text-anchor="middle">{lab}</text>')
    pts = " ".join(f"{f(px)},{f(h - py)}" for px, py in g["line"])
    out.append(f'<polyline points="{pts}" fill="none" stroke="#000" stroke-width="1.4"/>')
    for px, py, lab in g["marks"]:
        out.append(f'<circle cx="{f(px)}" cy="{f(h - py)}" r="4" fill="{MARK_FILL[lab]}" stroke="#000"/>')
    return "".join(out) + "</svg>"


def _legend_html() -> str:
    dots = "　".join(f'<svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="{c}" stroke="#000"/></svg> {lab}'
                    for lab, c in MARK_FILL.items())
    return f'<p class="note">先手の勝率（上ほど先手が有利）。横軸は手数。{dots}</p>'


def _facts(m: dict) -> list[tuple[str, str]]:
    """局面ごとの欄に出す項目（見出し, 値）。エンジンの最善手と、迷った読みの評価はここに出さず、回答に回す。"""
    out = [("エンジンの判定", f'{verdict(m)}（勝率の下落 {pct(m["winrate_drop"])}）')]
    if m.get("alts"):
        out.append(("迷った別の読み（本人の入力）", " / ".join(" ".join(a.get("line_jp", [])) for a in m["alts"])))
    if m.get("tags"):
        out.append(("原因（本人の入力）", "、".join(m["tags"])))
    if m.get("note"):
        out.append(("コメント（本人の入力）", m["note"]))
    return out


def _answer_facts(m: dict) -> list[tuple[str, str]]:
    actual = f'{m["side"]}{m["move_jp"]}（{verdict(m)}、勝率の下落 {pct(m["winrate_drop"])}）'
    best_line = [("最善手からの読み筋", m["best_line"])] if m.get("best_line") else []
    alt = [("迷った別の読み", alts_mod.describe(a)) for a in m.get("alts", [])]
    if m["was_best"]:
        return [("エンジンの最善手", f'{m["side"]}{m["best_before_jp"]}（実戦の手と同じ）'), *best_line, *alt]
    actual_line = [("実戦の手からの読み筋", m["actual_line"])] if m.get("actual_line") else []
    return [("エンジンの最善手", m["side"] + m["best_before_jp"]), *best_line, ("実戦の手", actual), *actual_line, *alt]


def answer_board(kifu: Kifu, m: dict) -> tuple[cshogi.Board, int | None]:
    """ply手目の直前の局面でエンジンの最善手を指した局面と、その手。指せない手（投了など）なら直前の局面のまま。"""
    board, last = board_after(kifu, m["ply"] - 1)
    try:
        mv = board.move_from_usi(m.get("best_usi") or "")
    except Exception:
        mv = 0
    if not mv or not board.is_legal(mv):
        return board, last
    board.push(mv)
    return board, mv


def to_html(d: dict, kifu: Kifu) -> str:
    """印刷用のページ（1ファイルで完結）。本人のコメントやAIの文章は文字として埋め込み、HTMLとして解釈させない。"""
    o = ['<!doctype html><html lang="ja"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         f"<title>ふりかえりノート {esc(d['title'])}</title><style>{CSS}</style></head><body>",
         '<div class="bar"><button onclick="window.print()">印刷する</button>'
         "<span>印刷画面で用紙サイズ A4 を選んでください。</span></div>",
         f"<h1>ふりかえりノート</h1><p>{esc(meta_line(d))}</p>",
         "<h2>感覚とエンジンのずれ</h2><div class='counts'>"]
    o += [f"<div><b>{n}</b>{lab}<br><span class='note'>{KIND_HELP[lab]}</span></div>" for lab, n in d["counts"]]
    o.append("</div>")
    if d["items"]:
        o.append("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in HEAD) + "</tr></thead><tbody>")
        o += ["<tr>" + "".join(f"<td>{esc(c)}</td>" for c in table_row(m)) + "</tr>" for m in d["items"]]
        o.append("</tbody></table>")
    else:
        o.append("<p>印を付けた手も、しきい値を超える自分の悪手候補もありませんでした。</p>")
    o.append(f"<p class='note'>{RULE_NOTE}</p>")
    if d["series"]:
        o.append("<h2>形勢グラフ</h2>" + _chart_svg(d["series"]) + _legend_html())
    if d["items"]:
        o.append(f"<h2>局面ごとのふりかえり</h2><p class='note'>{BOARD_NOTE}</p>")
    for m in d["items"]:
        o.append(f'<div class="card">{board_svg(kifu, m["ply"] - 1, scale=1.4, background=None)}<div class="txt">'
                 f'<h3>{m["ply"]}手目 {esc(m["side"] + m["move_jp"])}<span class="kind">{m["kind"]}</span></h3>')
        o += [f"<p><b>{k}</b>: {esc(v)}</p>" for k, v in _facts(m)]
        o.append(f'<div class="memo">{MEMO}<i></i><i></i></div></div></div>')
    if d["items"]:
        o.append(f"<h2 class='newpage'>回答（エンジンの最善手）</h2><p class='note'>{ANSWER_NOTE}</p>")
    for m in d["items"]:
        board, mv = answer_board(kifu, m)
        o.append(f'<div class="card">{to_svg(board, mv, 1.4)}<div class="txt">'
                 f'<h3>{m["ply"]}手目の回答<span class="kind">{m["kind"]}</span></h3>')
        o += [f"<p><b>{k}</b>: {esc(v)}</p>" for k, v in _answer_facts(m)]
        o.append("</div></div>")
    if d.get("good"):  # 実戦の手が最善手だったかが分かるので、回答より後ろに置く
        o.append("<h2>好手</h2><table><thead><tr>" + "".join(f"<th>{h}</th>" for h in GOOD_HEAD) + "</tr></thead><tbody>")
        o += ["<tr>" + "".join(f"<td>{esc(c)}</td>" for c in good_row(m)) + "</tr>" for m in d["good"]]
        o.append(f"</tbody></table><p class='note'>{GOOD_NOTE}</p>")
    if d["coach"]:
        o.append(f"<h2>AIコーチの所感</h2><p class='note'>{COACH_NOTE}</p>")
        for kind, text in d["coach"]:
            o.append({"h": "<h3>{}</h3>", "li": "<ul><li>{}</li></ul>", "p": "<p>{}</p>"}[kind].format(inline(text)))
    if d.get("qa"):
        o.append(f"<h2>AIコーチへの質問と回答</h2><p class='note'>{QA_NOTE}</p>")
        for q, blocks in d["qa"]:
            o.append(f"<h3>質問: {esc(q)}</h3>")
            for kind, text in blocks:  # 回答の中の見出しは、質問より目立たないよう太字の行にする
                o.append({"h": "<p><b>{}</b></p>", "li": "<ul><li>{}</li></ul>", "p": "<p>{}</p>"}[kind].format(inline(text)))
    return "".join(o).replace("</ul><ul>", "") + "</body></html>"


def print_link(page_html: str, label: str = "印刷用ページを開く") -> str:
    """印刷用ページを新しいタブで開くリンク（st.html に JavaScript を許可して渡す）。

    ページは blob としてブラウザ内に作る。中身は base64 にして埋め込むので、この断片の中でHTMLとして解釈されない。
    """
    b64 = base64.b64encode(page_html.encode("utf-8")).decode("ascii")
    return f"""<a id="furikaeri-print" target="_blank" rel="noopener" style="display:block;text-align:center;
padding:0.45rem 0.75rem;border:1px solid rgba(128,128,128,0.45);border-radius:0.5rem;text-decoration:none;
color:inherit;cursor:pointer;">{esc(label)}</a>
<script>
(function () {{
  var bytes = Uint8Array.from(atob("{b64}"), function (c) {{ return c.charCodeAt(0); }});
  var a = document.getElementById("furikaeri-print");
  if (!a) return;
  if (window.__furikaeriPrintUrl) URL.revokeObjectURL(window.__furikaeriPrintUrl);
  window.__furikaeriPrintUrl = URL.createObjectURL(new Blob([bytes], {{type: "text/html;charset=utf-8"}}));
  a.href = window.__furikaeriPrintUrl;
  a.target = "_blank";
}})();
</script>"""


def timed_download_link(data: bytes, stem: str, ext: str, label: str, mime: str = "text/plain", uid: str = "kifu") -> str:
    """押した時刻（秒まで）をファイル名に付けてダウンロードするリンク（st.html に JavaScript を許可して渡す）。

    例: kifu_20261014-153012.kif。時刻は押したときのブラウザの時計。Streamlit のダウンロードボタンは
    ファイル名が画面を描いた時点で決まるので、ブラウザ側で名前を付ける（発案者の指示 2026-10-14）。
    """
    b64 = base64.b64encode(data).decode("ascii")
    uid = re.sub(r"[^a-z0-9_]", "", uid.lower()) or "dl"
    stem = re.sub(r'[\\/:*?"<>|\s]+', "_", stem).strip("_") or "kifu"
    return f"""<a id="furikaeri-dl-{uid}" style="display:block;text-align:center;
padding:0.45rem 0.75rem;border:1px solid rgba(128,128,128,0.45);border-radius:0.5rem;text-decoration:none;
color:inherit;cursor:pointer;">{esc(label)}</a>
<script>
(function () {{
  var a = document.getElementById("furikaeri-dl-{uid}");
  if (!a) return;
  var bytes = Uint8Array.from(atob("{b64}"), function (c) {{ return c.charCodeAt(0); }});
  var url = URL.createObjectURL(new Blob([bytes], {{type: "{mime}"}}));
  a.href = url;
  a.addEventListener("click", function () {{
    var d = new Date(), p = function (n) {{ return String(n).padStart(2, "0"); }};
    a.download = {json.dumps(stem)} + "_" + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "-"
               + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds()) + {json.dumps(ext)};
  }});
}})();
</script>"""


# ---------------- PDF ----------------

_glyphs: set[int] = set()


def _fonts():
    if not _glyphs:
        for name, file in ((FONT, "BIZUDGothic-Regular.ttf"), (FONT_B, "BIZUDGothic-Bold.ttf")):
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
        pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_B, italic=FONT, boldItalic=FONT_B)
        _glyphs.update(pdfmetrics.getFont(FONT).face.charToGlyph)


def _safe(s: str) -> str:
    """フォントに無い文字（絵文字など）は□にする。そのままだとPDFで空白になり、抜けに気づけない。"""
    return "".join(c if ord(c) in _glyphs or c.isspace() else "□" for c in str(s))


class BoardDiagram(Flowable):
    """白黒の局面図。先手が手前。last（cshogi の手）の移動先のマスに薄い色を付ける。"""

    def __init__(self, board: cshogi.Board, last: int | None = None, cell: float = 6.3 * mm):
        super().__init__()
        self.pieces, self.hands, self.last, self.c = list(board.pieces), board.pieces_in_hand, last, cell
        self.width, self.height = cell * 9.8, cell * 11.5

    def wrap(self, *_):
        return self.width, self.height

    @staticmethod
    def _hand(counts) -> str:
        s = "".join(cshogi.HAND_PIECE_JAPANESE_SYMBOLS[i] + (cshogi.NUMBER_JAPANESE_KANJI_SYMBOLS[n] if n > 1 else "")
                    for i, n in reversed(list(enumerate(counts))) if n)
        return s or "なし"

    def draw(self):
        c, cv = self.c, self.canv
        y0, top = c * 0.9, c * 9.9
        if self.last is not None:
            f, r = divmod(cshogi.move_to(self.last), 9)
            cv.setFillGray(0.8)
            cv.rect((8 - f) * c, top - (r + 1) * c, c, c, stroke=0, fill=1)
        cv.setFillGray(0)
        cv.setLineWidth(0.4)
        for i in range(1, 9):
            cv.line(i * c, y0, i * c, top)
            cv.line(0, y0 + i * c, 9 * c, y0 + i * c)
        cv.setLineWidth(1.1)
        cv.rect(0, y0, 9 * c, 9 * c, stroke=1, fill=0)
        cv.setFont(FONT, c * 0.42)
        for i in range(9):
            cv.drawCentredString((8 - i) * c + c / 2, top + c * 0.18, "１２３４５６７８９"[i])
            cv.drawCentredString(9 * c + c * 0.42, top - i * c - c * 0.65, "一二三四五六七八九"[i])
        fs = c * 0.74
        cv.setFont(FONT, fs)
        for sq, p in enumerate(self.pieces):
            if not p:
                continue
            f, r = divmod(sq, 9)
            cv.saveState()
            cv.translate((8 - f) * c + c / 2, top - r * c - c / 2)
            if p >= 16:  # 後手の駒は逆さに描く
                cv.rotate(180)
            if p % 16 >= 9:  # 成り駒は赤（白黒で印刷すると灰色になる）
                cv.setFillColor(colors.HexColor(PROMOTED_FILL))
            cv.drawCentredString(0, -fs * 0.36, cshogi.PIECE_JAPANESE_SYMBOLS[p % 16])
            cv.restoreState()
        cv.setFont(FONT, c * 0.5)
        cv.drawString(0, top + c * 0.95, "☖後手の持駒　" + self._hand(self.hands[1]))
        cv.drawString(0, c * 0.15, "☗先手の持駒　" + self._hand(self.hands[0]))


class WinrateChart(Flowable):
    def __init__(self, series: list[tuple], width: float, height: float = 48 * mm):
        super().__init__()
        self.series, self.width, self.height = series, width, height

    def wrap(self, *_):
        return self.width, self.height

    def draw(self):
        cv, g = self.canv, chart_geom(self.series, self.width, self.height)
        x, y, w, h = g["box"]
        cv.setLineWidth(0.6)
        cv.rect(x, y, w, h)
        cv.setFont(FONT, 7.5)
        for ty, lab in g["yticks"]:
            cv.drawRightString(x - 3, ty - 2.5, lab)
        for tx, lab in g["xticks"]:
            cv.drawCentredString(tx, y - 10, lab)
        cv.saveState()
        cv.setDash(3, 3)
        cv.setLineWidth(0.4)
        cv.line(x, g["yticks"][1][0], x + w, g["yticks"][1][0])
        cv.restoreState()
        if len(g["line"]) > 1:
            path = cv.beginPath()
            path.moveTo(*g["line"][0])
            for pt in g["line"][1:]:
                path.lineTo(*pt)
            cv.setLineWidth(1.1)
            cv.drawPath(path)
        cv.setLineWidth(0.7)
        for px, py, lab in g["marks"]:
            cv.setFillColor(colors.HexColor(MARK_FILL[lab]))
            cv.circle(px, py, 2.6, stroke=1, fill=1)


class _Legend(Flowable):
    def __init__(self, width: float):
        super().__init__()
        self.width, self.height = width, 4 * mm

    def wrap(self, *_):
        return self.width, self.height

    def draw(self):
        cv, x = self.canv, 0.0
        cv.setFont(FONT, 8)
        text = "先手の勝率（上ほど先手が有利）。横軸は手数。"
        cv.drawString(x, 2, text)
        x += pdfmetrics.stringWidth(text, FONT, 8) + 8
        for lab, col in MARK_FILL.items():
            cv.setFillColor(colors.HexColor(col))
            cv.circle(x + 3, 5, 2.6, stroke=1, fill=1)
            cv.setFillGray(0)
            cv.drawString(x + 8, 2, lab)
            x += 8 + pdfmetrics.stringWidth(lab, FONT, 8) + 10


def to_pdf(d: dict, kifu: Kifu) -> bytes:
    """A4縦のPDF。フォントを埋め込む。"""
    _fonts()
    base = dict(fontName=FONT, wordWrap="CJK", textColor=colors.black)
    body = ParagraphStyle("body", fontSize=10, leading=15.5, **base)
    small = ParagraphStyle("small", fontSize=8, leading=11.5, **base)
    cell = ParagraphStyle("cell", fontSize=8.5, leading=11.5, **base)
    h1 = ParagraphStyle("h1", fontSize=18, leading=24, **{**base, "fontName": FONT_B})
    h2 = ParagraphStyle("h2", fontSize=13, leading=18, spaceBefore=14, spaceAfter=5, **{**base, "fontName": FONT_B})
    h3 = ParagraphStyle("h3", fontSize=11, leading=15, spaceBefore=6, spaceAfter=2, **{**base, "fontName": FONT_B})
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=12, firstLineIndent=-10)
    P = lambda text, style=body: Paragraph(esc(_safe(text)), style)
    grid = [("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.4, 0.4, 0.4)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=17 * mm, title=_safe(f"ふりかえりノート {d['title']}"), author="ふりかえり将棋")
    W = doc.width
    rule = lambda: Table([[""]], colWidths=[W], rowHeights=[1], style=[("LINEABOVE", (0, 0), (-1, 0), 1.2, colors.black)])
    section = lambda text: [P(text, h2), rule(), Spacer(1, 5)]

    s: list = [P("ふりかえりノート", h1), P(meta_line(d)), *section("感覚とエンジンのずれ")]
    count = ParagraphStyle("count", parent=body, leading=21)
    s.append(Table([[[Paragraph(f'<font name="{FONT_B}" size="16">{n}</font>　{lab}', count), P(KIND_HELP[lab], small)]
                     for lab, n in d["counts"]]], colWidths=[W / 3] * 3,
                   style=grid + [("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    s.append(Spacer(1, 6))
    if d["items"]:
        widths = [x * W / 180 for x in (20, 10, 28, 26, 19, 34, 43)]
        data = [[P(h, cell) for h in HEAD]] + [[P(c, cell) for c in table_row(m)] for m in d["items"]]
        s.append(Table(data, colWidths=widths, repeatRows=1,
                       style=grid + [("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9))]))
    else:
        s.append(P("印を付けた手も、しきい値を超える自分の悪手候補もありませんでした。"))
    s += [Spacer(1, 4), P(RULE_NOTE, small)]
    if d["series"]:
        s.append(KeepTogether([*section("形勢グラフ"), WinrateChart(d["series"], W), _Legend(W)]))
    card = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.Color(0.5, 0.5, 0.5))]
    lead = [*section("局面ごとのふりかえり"), P(BOARD_NOTE, small), Spacer(1, 4)]  # 見出しだけがページ末に残らないよう、最初の図と一緒に置く
    for m in d["items"]:
        board, last = board_after(kifu, m["ply"] - 1)
        diagram = BoardDiagram(board, last)
        text = [Paragraph(f'{esc(_safe(str(m["ply"]) + "手目 " + m["side"] + m["move_jp"]))}'
                          f'　<font name="{FONT}" size="9">［{m["kind"]}］</font>', h3)]
        text += [Paragraph(f"<b>{k}</b>: {esc(_safe(v))}", body) for k, v in _facts(m)]
        tw = W - diagram.width - 6 * mm
        text += [Spacer(1, 6), P(MEMO, small),
                 Table([[""], [""]], colWidths=[tw], rowHeights=[8 * mm] * 2,
                       style=[("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.Color(0.5, 0.5, 0.5))])]
        s.append(KeepTogether(lead + [Table([[diagram, text]], colWidths=[diagram.width + 6 * mm, tw], style=card)]))
        lead = []
    # 回答は後ろのページに分ける（問題の図を見ているあいだ、答えが目に入らないように）
    if d["items"]:
        s.append(PageBreak())
    lead = [*section("回答（エンジンの最善手）"), P(ANSWER_NOTE, small), Spacer(1, 4)]
    for m in d["items"]:
        diagram = BoardDiagram(*answer_board(kifu, m))
        text = [Paragraph(f'{m["ply"]}手目の回答　<font name="{FONT}" size="9">［{m["kind"]}］</font>', h3)]
        text += [Paragraph(f"<b>{k}</b>: {esc(_safe(v))}", body) for k, v in _answer_facts(m)]
        s.append(KeepTogether(lead + [Table([[diagram, text]], style=card,
                                            colWidths=[diagram.width + 6 * mm, W - diagram.width - 6 * mm])]))
        lead = []
    if d.get("good"):  # 実戦の手が最善手だったかが分かるので、回答より後ろに置く
        widths = [x * W / 180 for x in (28, 10, 30, 26, 22, 64)]
        data = [[P(h, cell) for h in GOOD_HEAD]] + [[P(c, cell) for c in good_row(m)] for m in d["good"]]
        s += [*section("好手"), Table(data, colWidths=widths, repeatRows=1,
                                    style=grid + [("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9))]),
              Spacer(1, 4), P(GOOD_NOTE, small)]
    if d["coach"]:
        paras = [Paragraph("・" + inline(_safe(text)) if kind == "li" else inline(_safe(text)),
                           {"h": h3, "li": bullet, "p": body}[kind]) for kind, text in d["coach"]]
        s += [KeepTogether([*section("AIコーチの所感"), P(COACH_NOTE, small), Spacer(1, 4), *paras[:2]]), *paras[2:]]
    if d.get("qa"):
        lead = [*section("AIコーチへの質問と回答"), P(QA_NOTE, small), Spacer(1, 4)]
        for q, blocks in d["qa"]:
            paras = [Paragraph("・" + inline(_safe(text)) if kind == "li" else
                               f"<b>{inline(_safe(text))}</b>" if kind == "h" else inline(_safe(text)),
                               bullet if kind == "li" else body) for kind, text in blocks]
            s += [KeepTogether(lead + [P("質問: " + q, h3), *paras[:1]]), *paras[1:]]
            lead = []

    def footer(cv, doc_):
        cv.setFont(FONT, 8)
        cv.drawCentredString(A4[0] / 2, 9 * mm, f"ふりかえり将棋　{doc_.page}")

    doc.build(s, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ---------------- 傾向まとめ（複数の対局。shogi_analyzer.trend.collect の内容） ----------------

TREND_TITLE = "大会に向けた傾向まとめ"
TREND_FACT_NOTE = "保存した対局を、決まった規則で数えた結果です（AIの判断ではありません）。勝率はエンジンによる参考値です。"
TREND_AI_NOTE = ("ここはAI（Gemini）が書いた所感です。上の記録にある数だけを使うよう指示し、記録に無い数字を含む行は"
                 "取り除いています。")
TREND_EX_NOTE = ("局面例は、決まった規則で選んでいます（種類ごとに、勝率の下落などがいちばん大きいもの）。"
                 "図はその手を指す直前の局面です。赤い字は成り駒です。")


def trend_html(d: dict) -> str:
    """傾向まとめの印刷用ページ。AIの文章は文字として埋め込み、HTMLとして解釈させない。"""
    o = ['<!doctype html><html lang="ja"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         f"<title>{TREND_TITLE}</title><style>{CSS}</style></head><body>",
         '<div class="bar"><button onclick="window.print()">印刷する</button>'
         "<span>印刷画面で用紙サイズ A4 を選んでください（3ページ以内）。</span></div>",
         f"<h1>{TREND_TITLE}</h1><p>{esc(d['created'])}　保存した対局 {d['games']}局</p>",
         "<h2>記録から言えること</h2><ul>", *(f"<li>{esc(f)}</li>" for f in d["facts"]), "</ul>",
         f"<p class='note'>{TREND_FACT_NOTE}</p>", "<h2>AIの所感</h2>"]
    if d["ai"]:
        o += ["<ul>", *(f"<li>{esc(b)}</li>" for b in d["ai"]), "</ul>", f"<p class='note'>{TREND_AI_NOTE}</p>"]
    else:
        o.append(f"<p class='note'>AIの所感はありません。{esc(d.get('ai_error', ''))}</p>")
    for i, e in enumerate(d["examples"]):
        if i % 2 == 0:
            o.append(f"<h2 class='newpage'>局面例</h2><p class='note'>{TREND_EX_NOTE}</p>" if i == 0 else
                     "<div class='newpage'></div>")
        board = cshogi.Board(e["sfen"])
        o.append(f'<div class="card">{to_svg(board, None, 1.4)}<div class="txt"><h3>{esc(trend_mod.example_title(e))}</h3>')
        o += [f"<p><b>{k}</b>: {esc(v)}</p>" for k, v in trend_mod.example_text(e)]
        o.append(f'<div class="memo">{MEMO}<i></i><i></i></div></div></div>')
    return "".join(o) + "</body></html>"


def trend_pdf(d: dict) -> bytes:
    """傾向まとめのPDF（A4縦、3ページ以内）。1ページ目に記録とAIの所感、2・3ページ目に局面例を2つずつ。"""
    _fonts()
    base = dict(fontName=FONT, wordWrap="CJK", textColor=colors.black)
    body = ParagraphStyle("body", fontSize=10, leading=15, **base)
    small = ParagraphStyle("small", fontSize=8, leading=11.5, **base)
    h1 = ParagraphStyle("h1", fontSize=17, leading=23, **{**base, "fontName": FONT_B})
    h2 = ParagraphStyle("h2", fontSize=13, leading=18, spaceBefore=10, spaceAfter=4, **{**base, "fontName": FONT_B})
    h3 = ParagraphStyle("h3", fontSize=10.5, leading=14, spaceAfter=2, **{**base, "fontName": FONT_B})
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=12, firstLineIndent=-10)
    P = lambda text, style=body: Paragraph(esc(_safe(text)), style)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=16 * mm, title=TREND_TITLE, author="ふりかえり将棋")
    W = doc.width
    rule = lambda: Table([[""]], colWidths=[W], rowHeights=[1], style=[("LINEABOVE", (0, 0), (-1, 0), 1.2, colors.black)])
    section = lambda text: [P(text, h2), rule(), Spacer(1, 4)]
    s: list = [P(TREND_TITLE, h1), P(f"{d['created']}　保存した対局 {d['games']}局"), *section("記録から言えること")]
    s += [P("・" + f, bullet) for f in d["facts"]] + [Spacer(1, 3), P(TREND_FACT_NOTE, small), *section("AIの所感")]
    s += ([P("・" + b, bullet) for b in d["ai"]] + [Spacer(1, 3), P(TREND_AI_NOTE, small)] if d["ai"] else
          [P("AIの所感はありません。" + d.get("ai_error", ""), small)])
    card = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.Color(0.5, 0.5, 0.5))]
    for i, e in enumerate(d["examples"]):
        if i % 2 == 0:
            s.append(PageBreak())
            if i == 0:
                s += [*section("局面例"), P(TREND_EX_NOTE, small), Spacer(1, 4)]
        diagram = BoardDiagram(cshogi.Board(e["sfen"]))
        tw = W - diagram.width - 6 * mm
        text = [P(trend_mod.example_title(e), h3)] + [Paragraph(f"<b>{k}</b>: {esc(_safe(v))}", body)
                                                      for k, v in trend_mod.example_text(e)]
        text += [Spacer(1, 6), P(MEMO, small), Table([[""], [""]], colWidths=[tw], rowHeights=[8 * mm] * 2,
                                                     style=[("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.Color(0.5, 0.5, 0.5))])]
        s.append(Table([[diagram, text]], colWidths=[diagram.width + 6 * mm, tw], style=card))

    def footer(cv, doc_):
        cv.setFont(FONT, 8)
        cv.drawCentredString(A4[0] / 2, 9 * mm, f"ふりかえり将棋　{TREND_TITLE}　{doc_.page}")

    doc.build(s, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
