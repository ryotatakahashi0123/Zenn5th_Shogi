"""対局ドキュメントの組み立てと、局面の取得(API層とスクリプトが共用)。"""
from __future__ import annotations

import pathlib
from collections import OrderedDict
from typing import Optional

from .domain.board import Position, build_positions
from .domain.kif_parser import parse_kif
from .domain.time_analysis import analyze
from .infra.config import Settings
from .infra.store import ROOT, Store, expire_at, new_id, now_iso

SAMPLE_TITLES = {
    "sample01": "サンプル1(負け局・先手)",  # 発案者の申告による(2026-10-10)
    "sample02": "サンプル2(勝ち局・先手)",
}
SAMPLES_DIR = ROOT / "samples" / "anon"


def detect_my_side(meta: dict, cfg: Settings, requested: Optional[str]) -> str:
    if requested in ("b", "w"):
        return requested
    if meta.get("後手") in cfg.my_ids and meta.get("先手") not in cfg.my_ids:
        return "w"
    return "b"


def build_game_doc(kif_text: str, owner: str, cfg: Settings, *, my_side: Optional[str] = None,
                   title: Optional[str] = None, sample: bool = False, doc_id: Optional[str] = None) -> dict:
    """KIFを解析して保存用ドキュメントを作る。KifError は呼び出し側で扱う。"""
    g = parse_kif(kif_text)
    moves = [m.to_dict() for m in g.moves]
    derived = analyze(moves, g.time_limit_sec, long_min=cfg.long_min_sec, ratio=cfg.long_ratio,
                      low_sec=cfg.low_remaining_sec, swing_cp=cfg.swing_cp,
                      sente_perspective=cfg.eval_sente_perspective)
    positions, perrs = build_positions(moves, g.meta)
    warnings = list(g.warnings)
    for e in perrs:
        warnings.append(f"局面の再現: {e['n']}手目 {e['text']} {e['reason']}")
    when = g.meta.get("開始日時", "")
    return {
        "id": doc_id or new_id(),
        "ownerUid": owner,
        "createdAt": now_iso(),
        "expireAt": None if sample else expire_at(cfg.ttl_days),
        "sample": sample,
        "title": (title or "").strip()[:60] or (f"対局 {when}" if when else "対局"),
        "mySide": detect_my_side(g.meta, cfg, my_side),
        "meta": g.meta,
        "result": g.result,
        "moves": moves,
        "derived": derived,
        "warnings": warnings,
        "positionCount": len(positions),
        "kifRaw": kif_text,
    }


def public_game(doc: dict) -> dict:
    """クライアントへ返す形(KIF原文と期限は除く)。"""
    d = {k: v for k, v in doc.items() if k not in ("kifRaw", "expireAt", "ownerUid")}
    d["totalMoves"] = len(doc["moves"])
    d["positionsAvailable"] = doc.get("positionCount", 0) == len(doc["moves"]) + 1
    return d


def list_item(doc: dict) -> dict:
    return {"id": doc["id"], "title": doc["title"], "createdAt": doc["createdAt"],
            "totalMoves": len(doc["moves"]), "result": doc.get("result"),
            "mySide": doc["mySide"], "sample": doc.get("sample", False),
            "warnings": len(doc.get("warnings", []))}


_CACHE: "OrderedDict[str, tuple[int, list[Position], list[dict]]]" = OrderedDict()


def positions_for(doc: dict) -> tuple[list[Position], list[dict]]:
    """局面は保存せず、指し手から再現する(小さなキャッシュ付き)。"""
    key = doc["id"]
    n = len(doc["moves"])
    hit = _CACHE.get(key)
    if hit and hit[0] == n:
        _CACHE.move_to_end(key)
        return hit[1], hit[2]
    pos, errs = build_positions(doc["moves"], doc["meta"])
    _CACHE[key] = (n, pos, errs)
    while len(_CACHE) > 64:
        _CACHE.popitem(last=False)
    return pos, errs


def seed_samples(store: Store, cfg: Settings, owner: str, directory: Optional[pathlib.Path] = None) -> int:
    """匿名化済みのサンプル(samples/anon)を入れる。固定IDなので何度実行しても重複しない。"""
    d = directory or SAMPLES_DIR
    n = 0
    for f in sorted(d.glob("*.kif")):
        gid = f"sample-{f.stem}"
        if store.get_game(gid):
            continue
        text = f.read_bytes().decode("utf-8")
        doc = build_game_doc(text, owner, cfg, title=SAMPLE_TITLES.get(f.stem, f.stem),
                             my_side="b", sample=True, doc_id=gid)
        store.create_game(doc)
        n += 1
    return n
