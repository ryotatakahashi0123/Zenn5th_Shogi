"""棋譜（KIF / CSA）を読み込み、全局面をUSIエンジンで解析してCSV用の行を作る。

棋譜パースは cshogi（GPL-3.0）を使う。
"""
from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

import cshogi
import cshogi.KIF
import cshogi.CSA

from .usi_engine import UsiEngine, MATE_CP

# 悪手判定は「指した側から見た勝率の下落幅」で行う。
# 評価値(cp)のままだと、既に勝負が決まった局面（±数千点）での小さな差まで悪手扱いになるため。
WINRATE_SCALE = 600  # 勝率 = 1 / (1 + exp(-cp / 600))。将棋ソフトで広く使われる近似
THRESHOLDS = [(0.20, "悪手"), (0.10, "疑問手"), (0.05, "緩手")]
# 好手候補は「最善手を指し、次善手なら勝率がこれ以上低かった」手。最善手と同じだけでは序盤の普通の手まで入るため、
# 「その手でなければ損をしていた」局面に絞る。値は疑問手のしきい値に合わせた仮の値（発案者の確認待ち）
GOOD_GAP = 0.10
ANALYZE_MULTIPV = 2  # 次善手との差を出すため、全手解析では候補手を2つ読む
MAX_PLIES = 400      # これより長い棋譜は受け付けない（解析時間の上限）
PV_PLIES = 8         # 行に残す読み筋の長さ


def winrate(cp_sente: int) -> float:
    """先手視点の評価値を先手の勝率(0〜1)に変換する。"""
    return 1.0 / (1.0 + math.exp(-cp_sente / WINRATE_SCALE))


@dataclass
class Kifu:
    names: list[str]          # [先手名, 後手名]
    start_sfen: str
    moves: list[int]          # cshogi の move(int)


@dataclass
class MoveRow:
    ply: int                  # 手数（1手目=1）
    side: str                 # ▲ / △
    move_usi: str
    move_jp: str              # 例: ７六歩(77)
    eval_sente: int           # この手を指した後の局面の評価値（先手視点, cp）
    best_before_usi: str      # 指す前の局面での最善手
    best_before_jp: str
    winrate_sente: float      # この手を指した後の先手勝率（0〜1）
    eval_drop: int            # 指した側から見た評価値の下落幅（正=損をした, cp）
    winrate_drop: float       # 指した側から見た勝率の下落幅（判定に使う）
    label: str                # 悪手 / 疑問手 / 緩手 / ""
    pv: str
    good_gap: float = 0.0     # 指す前の局面での、最善手と次善手の勝率の差（指す側から見た値。次善手が無ければ 0）
    good: bool = False        # 好手候補（最善手を指し、good_gap が GOOD_GAP 以上）
    best_pv: str = ""         # 指す前の局面での、最善手から始まる読み筋（USI、空白区切り）。pv は実戦の手の後の読み筋


def is_good(played_best: bool, gap: float) -> bool:
    return played_best and gap >= GOOD_GAP


def second_gap(result) -> float:
    """最善手と次善手の勝率の差（手番側から見た値）。次善手が無い局面（指せる手が1つ等）は 0。"""
    c = result.candidates
    if len(c) < 2 or not c[1].bestmove:
        return 0.0
    return max(0.0, winrate(c[0].score_for_sente(True)) - winrate(c[1].score_for_sente(True)))


def decode_kifu(data: bytes) -> str:
    """UTF-8（BOM有無）→ Shift_JIS(cp932) の順で文字コードを判定する。"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("棋譜の文字コードを判定できません（UTF-8 / Shift_JIS のみ対応）")


def detect_format(text: str) -> str:
    """棋譜テキストの形式（"csa" か "kif"）。CSA は「+7776FU」のような指し手の行か、版数・初期局面の行で見分ける。"""
    for line in text.splitlines():
        if re.match(r"[+-]\d{4}[A-Z]{2}\b|V\d|PI|P1", line.strip()):
            return "csa"
    return "kif"


def load_kifu_text(text: str, fmt: str = "") -> Kifu:
    """棋譜テキスト（貼り付けなど）を読み込む。fmt が空なら内容から KIF / CSA を見分ける。

    読めないもの・指し手が無いもの・長すぎるものは ValueError（画面にそのまま出せる日本語の文）。
    """
    if not text.strip():
        raise ValueError("棋譜が空です")
    fmt = fmt or detect_format(text)
    try:
        p = cshogi.CSA.Parser.parse_str(text)[0] if fmt == "csa" else cshogi.KIF.Parser.parse_str(text)
        kifu = Kifu(list(p.names), p.sfen, list(p.moves))
    except Exception as e:
        raise ValueError(f"棋譜として読み取れませんでした（KIF / CSA 形式に対応しています）: {e}") from e
    if not kifu.moves:
        raise ValueError("指し手を1つも読み取れませんでした。KIF または CSA 形式の棋譜か確認してください"
                         "（「▲７六歩 △３四歩」と並べた KI2 形式には対応していません）")
    if len(kifu.moves) > MAX_PLIES:
        raise ValueError(f"{MAX_PLIES}手を超える棋譜には対応していません（{len(kifu.moves)}手）")
    validate_kifu(kifu)
    return kifu


def load_kifu(source: str | Path | bytes, filename: str = "") -> Kifu:
    """KIF / CSA を拡張子で判別して読み込む（.csa / .kif / .kifu 以外は内容から見分ける）。

    source: ファイルパス、またはアップロードされたバイト列（Streamlit 用）。
    """
    if isinstance(source, bytes):
        data, name = source, filename
    else:
        data, name = Path(source).read_bytes(), filename or Path(source).name
    ext = Path(name).suffix.lower()
    return load_kifu_text(decode_kifu(data), {".csa": "csa", ".kif": "kif", ".kifu": "kif"}.get(ext, ""))


def validate_kifu(kifu: Kifu):
    """全手が合法か確認する。不正な手をエンジンに送るとエンジンが停止するため必須。"""
    board = cshogi.Board(kifu.start_sfen)
    for i, mv in enumerate(kifu.moves, start=1):
        if not board.is_legal(mv):
            raise ValueError(f"{i}手目 {cshogi.move_to_usi(mv)} は不正な手です（棋譜を確認してください）")
        board.push(mv)


def analyze_kifu(kifu: Kifu, engine: UsiEngine, byoyomi_ms: int = 500,
                 progress: Callable[[int, int], None] | None = None) -> list[MoveRow]:
    board = cshogi.Board(kifu.start_sfen)
    usi_moves: list[str] = []
    n = len(kifu.moves)

    # 0手目（開始局面）の解析
    def analyze_current():
        r = engine.analyze(f"sfen {kifu.start_sfen}", usi_moves, byoyomi_ms, multipv=ANALYZE_MULTIPV)
        return r, r.score_for_sente(board.turn == cshogi.BLACK)

    before, before_eval = analyze_current()
    rows: list[MoveRow] = []
    for i, mv in enumerate(kifu.moves, start=1):
        mover_is_sente = board.turn == cshogi.BLACK
        best_jp = _usi_to_jp(board, before.bestmove)
        best_pv = before.pv[:PV_PLIES] if before.pv[:1] == [before.bestmove] else []
        move_usi = cshogi.move_to_usi(mv)
        move_jp = _usi_to_jp(board, move_usi)

        board.push(mv)
        usi_moves.append(move_usi)
        if board.is_game_over():
            after_eval = -MATE_CP if board.turn == cshogi.BLACK else MATE_CP
            after = None
        else:
            after, after_eval = analyze_current()

        sign = 1 if mover_is_sente else -1
        drop = max(0, sign * (before_eval - after_eval))
        wr_drop = max(0.0, sign * (winrate(before_eval) - winrate(after_eval)))
        label = next((lab for th, lab in THRESHOLDS if wr_drop >= th), "")
        if move_usi == before.bestmove:
            label = ""  # 最善手と一致したら印を付けない
        gap = second_gap(before)
        rows.append(MoveRow(
            ply=i, side="▲" if mover_is_sente else "△", move_usi=move_usi, move_jp=move_jp,
            eval_sente=after_eval, best_before_usi=before.bestmove, best_before_jp=best_jp,
            winrate_sente=round(winrate(after_eval), 3),
            eval_drop=drop, winrate_drop=round(wr_drop, 3), label=label, pv=" ".join(after.pv[:PV_PLIES]) if after else "",
            good_gap=round(gap, 3), good=is_good(move_usi == before.bestmove, gap), best_pv=" ".join(best_pv),
        ))
        if progress:
            progress(i, n)
        if after is None:
            break
        before, before_eval = after, after_eval
    return rows


def write_csv(rows: list[MoveRow], path: str | Path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # Excelで文字化けしないようBOM付き
        w = csv.DictWriter(f, fieldnames=list(MoveRow.__dataclass_fields__))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))


def _usi_to_jp(board: cshogi.Board, usi: str) -> str:
    if usi in ("resign", "win", "none", ""):
        return {"resign": "投了", "win": "勝ち宣言"}.get(usi, usi)
    try:
        return cshogi.KIF.move_to_kif(board.move_from_usi(usi))
    except Exception:
        return usi
