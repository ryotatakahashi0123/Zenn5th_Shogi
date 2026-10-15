"""AIエージェントの講評を、局面図と連動させて見せるための形にする（発案者の指示 2026-10-14）。エンジンもLLMも呼ばない。

講評の中の「15手目」を押せる印にし、段落ごとに触れている手数を持たせる。画面の部品（assets/coach_text）は、
押された手を局面図に出し、局面図で選んでいる手に触れている段落に色を付ける。
AIの文章は必ずエスケープしてから印を入れる（文章の中のHTMLは解釈させない）。
"""
from __future__ import annotations

import re

from .report import esc, inline, md_blocks

PLY_RE = re.compile(r"([0-9０-９]+)手目")
_WIDE = str.maketrans("０１２３４５６７８９", "0123456789")


def plies_in(text: str, plies: int) -> list[int]:
    """文の中の「N手目」の手数（対局に無い手数は除く。重複なし、出てきた順）。"""
    out = []
    for m in PLY_RE.finditer(text):
        n = int(m.group(1).translate(_WIDE))
        if 1 <= n <= plies and n not in out:
            out.append(n)
    return out


def _link(html_text: str, plies: int) -> str:
    def rep(m):
        n = int(m.group(1).translate(_WIDE))
        return f'<a class="ply" data-ply="{n}">{m.group(0)}</a>' if 1 <= n <= plies else m.group(0)
    return PLY_RE.sub(rep, html_text)


def blocks(text: str, plies: int) -> list[dict]:
    """Markdown の講評を、段落ごとの {"kind": h/li/p, "html", "plies"} にする。

    見出し（「15手目 ▲６六角 → ▲３四飛」など）の手数は、次の見出しまでの段落にも引き継ぐ（その節の話なので）。
    """
    out, section = [], []
    for kind, s in md_blocks(text):
        own = plies_in(s, plies)
        if kind == "h":
            section = own
        out.append({"kind": kind, "html": _link(inline(s), plies), "plies": own or ([] if kind == "h" else section)})
    return out


def messages(chat: list[tuple[str, str]], plies: int, hide: str = "", error: str = "") -> list[dict]:
    """AIエージェントとのやり取りを、部品に渡す形にする。hide の依頼文（自動のふりかえり）は出さない。

    error で始まる回答（失敗）は、文のまま出す（手数の印は付けない）。
    """
    out = []
    for role, text in chat:
        if role == "user":
            if text != hide:
                out.append({"role": "user", "blocks": [{"kind": "p", "html": esc(text), "plies": plies_in(text, plies)}]})
        elif error and text.startswith(error):
            out.append({"role": "assistant", "blocks": [{"kind": "p", "html": esc(text), "plies": []}]})
        else:
            out.append({"role": "assistant", "blocks": blocks(text, plies)})
    return out
