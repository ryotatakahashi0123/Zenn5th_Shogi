"""サンプル棋譜の匿名化(FR-45, T17)。公開物に対局者名・IDを出さないための置換。"""
from __future__ import annotations

import re
from typing import Iterable

HEADER_RE = re.compile(r"^(?P<key>先手|後手|下手|上手)：(?P<name>.*)$")
MASKED_RE = re.compile(r"^[?？]+$")


def anonymize_kif(text: str, my_ids: Iterable[str], my_label: str = "自分",
                  opp_label: str = "相手") -> str:
    """ヘッダの対局者名を置換し、他の場所に残る同じ名前も置換する。改行はそのまま保つ。"""
    my_ids = {i for i in my_ids if i}
    names: dict[str, str] = {}
    unknown = 0
    for line in text.splitlines():
        m = HEADER_RE.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        if not name or name in names:
            continue
        if name in my_ids:
            names[name] = my_label
        elif MASKED_RE.match(name):
            names[name] = opp_label
        else:
            unknown += 1
            names[name] = opp_label if unknown == 1 else f"{opp_label}{unknown}"
    out = text
    # 長い名前から置換(部分一致の取りこぼしを避ける)
    for name in sorted(names, key=len, reverse=True):
        out = out.replace(name, names[name])
    # 設定にだけある自分のID(ヘッダにない場合の対策。コメント内など)
    for i in my_ids:
        out = out.replace(i, my_label)
    return out
