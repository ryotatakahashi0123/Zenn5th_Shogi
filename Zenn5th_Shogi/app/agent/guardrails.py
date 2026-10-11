"""LLM出力の検証(04の7.3)。通ったものだけを使う。落ちたら理由を返す。"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Iterable, Optional

from pydantic import ValidationError

from .schemas import CandidateOutput, NarrationOutput

# 評価値なしの局では良し悪しを断定できない。最善手・悪手などの断定語を出させない(08の5.2, 5.3)
BANNED_PHRASES = ["悪手", "最善", "正解は", "間違いない", "必ず勝", "必ず負"]
MAX_REASON = 80
MAX_COMMENT = 120
MAX_SUMMARY = 240
MAX_FOCUS = 80


def strip_fence(raw: str) -> str:
    s = raw.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
    return m.group(1) if m else s


def numbers_in(text: str) -> set[int]:
    t = unicodedata.normalize("NFKC", text)
    return {int(x) for x in re.findall(r"\d+", t)}


def numbers_ok(text: str, allowed: set[int]) -> Optional[str]:
    """文中の数字がすべて、与えたデータに由来するか。外れた数字を返す。"""
    bad = sorted(numbers_in(text) - allowed)
    return f"与えていない数字を含む: {bad}" if bad else None


def _banned(text: str) -> Optional[str]:
    for w in BANNED_PHRASES:
        if w in text:
            return f"断定語「{w}」を含む"
    return None


def _check_text(label: str, text: str, limit: int, allowed_numbers: set[int], reasons: list[str]) -> None:
    if not text.strip():
        reasons.append(f"{label}が空")
        return
    if len(text) > limit:
        reasons.append(f"{label}が長すぎる({len(text)}字 > {limit})")
    b = _banned(text)
    if b:
        reasons.append(f"{label}: {b}")
    nb = numbers_ok(text, allowed_numbers)
    if nb:
        reasons.append(f"{label}: {nb}")


def validate_candidates(raw: str, total_moves: int, allowed_ns: Iterable[int],
                        allowed_numbers: set[int], max_n: int = 5) -> tuple[Optional[CandidateOutput], list[str]]:
    reasons: list[str] = []
    try:
        out = CandidateOutput.model_validate(json.loads(strip_fence(raw)))
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        return None, [f"JSONまたはスキーマが不正: {type(e).__name__}"]
    allowed = set(allowed_ns)
    seen: set[int] = set()
    if len(out.marks) > max_n:
        reasons.append(f"件数超過({len(out.marks)} > {max_n})")
    for m in out.marks:
        if not 1 <= m.n <= total_moves:
            reasons.append(f"手番号{m.n}が範囲外(1〜{total_moves})")
        elif m.n not in allowed:
            reasons.append(f"手番号{m.n}は与えた候補にない")
        if m.n in seen:
            reasons.append(f"手番号{m.n}が重複")
        seen.add(m.n)
        _check_text(f"{m.n}手目の理由", m.reason, MAX_REASON, allowed_numbers, reasons)
    return (None, reasons) if reasons else (out, [])


def validate_narration(raw: str, allowed_ns: Iterable[int], allowed_numbers: set[int]
                       ) -> tuple[Optional[NarrationOutput], list[str]]:
    reasons: list[str] = []
    try:
        out = NarrationOutput.model_validate(json.loads(strip_fence(raw)))
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        return None, [f"JSONまたはスキーマが不正: {type(e).__name__}"]
    allowed = set(allowed_ns)
    _check_text("summary", out.summary, MAX_SUMMARY, allowed_numbers, reasons)
    seen: set[int] = set()
    for it in out.items:
        if it.n not in allowed:
            reasons.append(f"手番号{it.n}は与えたデータにない")
        if it.n in seen:
            reasons.append(f"手番号{it.n}が重複")
        seen.add(it.n)
        _check_text(f"{it.n}手目のコメント", it.comment, MAX_COMMENT, allowed_numbers | {it.n}, reasons)
    if len(out.next_focus) > 3:
        reasons.append("次に意識することが4件以上")
    for i, f in enumerate(out.next_focus):
        _check_text(f"次に意識すること{i + 1}", f, MAX_FOCUS, allowed_numbers, reasons)
    return (None, reasons) if reasons else (out, [])
