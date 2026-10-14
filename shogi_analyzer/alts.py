"""迷った別の読み（発案者の指示 2026-10-14）。

印を付けた手を指す直前の局面から、本人が「こう指すかも迷った」手順を入れ、エンジンでその先を確かめる。
読みは本人の入力で、エンジンやAIが代わりに作らない。入力は答えを見る前（印と一緒）、評価は印の入力を完了した後。

評価は、その局面を開始局面にした短い棋譜として全手解析（kifu_analysis.analyze_kifu）にかけるだけ。
読みの1手ずつに、エンジンの最善手・勝率の下落・判定（悪手など）が付き、最後の局面から先の読み筋も得られる。
数値はすべてその解析の行から取り、新しく作らない。
"""
from __future__ import annotations

import re

import cshogi

from .kifu_analysis import THRESHOLDS, Kifu
from .service import LINE_PLIES, board_after, extend_line, pv_jp

MAX_ALTS = 3        # 1つの印に入れられる読みの数
MAX_ALT_PLIES = 6   # 1つの読みの長さ（エンジンの解析は 読みの手数 + 1 回）
CLOSE = THRESHOLDS[-1][0]  # 実戦の手と比べて「同じくらい」とみなす勝率の差（緩手のしきい値と同じ 5%。仮の値）


def start_sfen(kifu: Kifu, ply: int) -> str:
    """ply手目を指す直前の局面（読みの開始局面）の SFEN。"""
    return board_after(kifu, ply - 1)[0].sfen()


def clean_line(kifu: Kifu, ply: int, line: list[str], others: list[list[str]] = ()) -> list[str]:
    """読みを検査する。空・長すぎる・指せない手・同じ読みの重複は ValueError。"""
    line = [str(u) for u in line]
    if not line:
        raise ValueError("読みの手を1手以上入れてください")
    if len(line) > MAX_ALT_PLIES:
        raise ValueError(f"読みは{MAX_ALT_PLIES}手までです")
    board = cshogi.Board(start_sfen(kifu, ply))
    for i, u in enumerate(line, start=1):
        m = board.move_from_usi(u)
        if not m or not board.is_legal(m):
            raise ValueError(f"読みの{i}手目 {u} は指せない手です")
        board.push(m)
    if any(list(o) == line for o in others):
        raise ValueError("同じ読みがすでに入っています")
    return line


def line_jp(kifu: Kifu, ply: int, line: list[str]) -> list[str]:
    """読みを日本語の手順にする（"▲２四歩" の並び）。"""
    return pv_jp(cshogi.Board(start_sfen(kifu, ply)), line, len(line))


def alt_kifu(kifu: Kifu, ply: int, line: list[str]) -> Kifu:
    """読みを、その局面から始まる短い棋譜にする（全手解析にそのまま渡せる）。対局者名は入れない。"""
    board = cshogi.Board(start_sfen(kifu, ply))
    moves = []
    for u in line:
        m = board.move_from_usi(u)
        board.push(m)
        moves.append(m)
    return Kifu(["", ""], start_sfen(kifu, ply), moves)


def key(ply: int, line: list[str]) -> str:
    """評価結果を覚えておくときの名前（同じ手・同じ読みなら同じ）。"""
    return f"{ply}:{' '.join(line)}"


def lines_of(marks: list[dict]) -> list[tuple[int, list[str]]]:
    """印に入っている読みを（手数, 読み）の並びにする。"""
    return [(int(m["ply"]), list(a)) for m in marks for a in m.get("alts", [])]


def evaluate(client, kifu: Kifu, marks: list[dict], ms: int, cache: dict, cancel=lambda: False) -> dict:
    """まだ評価していない読みをエンジンで解析し、cache（key → 解析の行）に入れる。cache を返す。

    client は analyze_game(kifu, ms) を持つもの（shogi_coach.engine_client）。1つ失敗しても、ほかの読みは続ける。
    """
    for ply, line in lines_of(marks):
        k = key(ply, line)
        if k in cache or cancel():
            continue
        try:
            rows = client.analyze_game(alt_kifu(kifu, ply, line), ms)["rows"]
        except Exception as e:  # 失敗は失敗として残す（画面に出す）
            cache[k] = {"error": str(e)}
            continue
        if rows and len(rows) == len(line):  # 読みの終わりから先の読み筋が短く途切れていたら、続きを読んで伸ばす
            pv = (rows[-1].get("pv") or "").split()
            try:
                full = extend_line(client, start_sfen(kifu, ply), line + pv, len(line) + LINE_PLIES)
                rows[-1] = {**rows[-1], "pv": " ".join(full[len(line):])}
            except Exception:
                pass  # 伸ばせなくても、元の読み筋のまま使う
        cache[k] = rows
    return cache


def summarize(kifu: Kifu, ply: int, line: list[str], alt_rows, actual_row: dict | None) -> dict:
    """読み1つの評価をまとめる。alt_rows: 読みを全手解析した行。actual_row: 実戦のその手の全手解析の行。

    勝率は、印を付けた手を指す側（読みの1手目を指す側）から見た値。
    verdict: 実戦の手と比べて、読みの終わりの局面が 良い / 同じくらい / 悪い（差が CLOSE 未満は同じくらい）。
    """
    out = {"ply": ply, "line": list(line), "line_jp": line_jp(kifu, ply, line)}
    if isinstance(alt_rows, dict) or not alt_rows:  # 評価に失敗した（{"error": ...}）か、行が無い
        out["error"] = alt_rows.get("error", "") if isinstance(alt_rows, dict) else "評価できませんでした"
        return out
    mover = alt_rows[0]["side"]
    for_mover = lambda wr_sente: wr_sente if mover == "▲" else 1 - wr_sente
    board = cshogi.Board(start_sfen(kifu, ply))
    steps = []
    for r in alt_rows:
        steps.append({"move_jp": r["side"] + _strip(r["move_jp"]), "side": r["side"], "label": r["label"],
                      "winrate_drop": r["winrate_drop"], "was_best": r["move_usi"] == r["best_before_usi"],
                      "best_jp": r["side"] + _strip(r["best_before_jp"]),
                      "winrate_for_mover": round(for_mover(r["winrate_sente"]), 3)})
        board.push(board.move_from_usi(r["move_usi"]))
    last = alt_rows[-1]
    final = round(for_mover(last["winrate_sente"]), 3)
    out.update(steps=steps, final_winrate=final, mover=mover,
               after=pv_jp(board, (last.get("pv") or "").split(), LINE_PLIES))  # 読みの終わりの局面から先の、エンジンの読み筋
    if actual_row:
        actual = round(for_mover(actual_row["winrate_sente"]), 3)
        diff = round(final - actual, 3)
        out.update(actual_jp=actual_row["side"] + _strip(actual_row["move_jp"]), actual_winrate=actual, diff=diff,
                   verdict="良い" if diff >= CLOSE else "悪い" if diff <= -CLOSE else "同じくらい")
    # 読みの1手目は、その局面のエンジンの最善手か（迷った手が正解だったか）
    out["first_best"] = steps[0]["was_best"]
    out["first_best_jp"] = steps[0]["best_jp"]
    return out


def summaries(kifu: Kifu, marks: list[dict], rows: list[dict], cache: dict) -> dict:
    """手数 → その印の読みの評価の並び。評価がまだの読みは pending: True。"""
    by_ply = {r["ply"]: r for r in rows}
    out: dict[int, list[dict]] = {}
    for ply, line in lines_of(marks):
        k = key(ply, line)
        if k not in cache:
            out.setdefault(ply, []).append({"ply": ply, "line": line, "line_jp": line_jp(kifu, ply, line), "pending": True})
        else:
            out.setdefault(ply, []).append(summarize(kifu, ply, line, cache[k], by_ply.get(ply)))
    return out


def end_board(kifu: Kifu, ply: int, line: list[str]) -> tuple[cshogi.Board, int | None]:
    """読みを最後まで指した局面と、最後の手（局面図用）。"""
    board = cshogi.Board(start_sfen(kifu, ply))
    last = None
    for u in line:
        last = board.move_from_usi(u)
        board.push(last)
    return board, last


def _strip(jp: str) -> str:
    return re.sub(r"\(\d\d\)$", "", jp)


def describe(a: dict) -> str:
    """読み1つの評価を1行の文にする（画面・ノート・傾向まとめで共通。数はすべて評価の値）。"""
    head = " ".join(a.get("line_jp") or a.get("line", []))
    if a.get("pending"):
        return f"{head}（評価中）"
    if a.get("error"):
        return f"{head}（評価できませんでした: {a['error']}）"
    s = f"{head} → 読みの終わりの勝率 {a['final_winrate'] * 100:.0f}%"
    if "verdict" in a:
        s += f"（実戦 {a['actual_jp']} の後は {a['actual_winrate'] * 100:.0f}%。実戦より{a['verdict']}）" if a["verdict"] != "同じくらい" \
            else f"（実戦 {a['actual_jp']} の後は {a['actual_winrate'] * 100:.0f}%。実戦と同じくらい）"
    s += "。1手目は" + ("エンジンの最善手と同じ" if a["first_best"] else f"最善手ではない（最善手は {a['first_best_jp']}）")
    bad = [f"{st['move_jp']}（{st['label']}）" for st in a["steps"] if st["label"]]
    if bad:
        s += "。読みの中の悪手候補: " + "、".join(bad)
    if a.get("after"):
        s += "。その先のエンジンの読み筋: " + " ".join(a["after"])
    return s

