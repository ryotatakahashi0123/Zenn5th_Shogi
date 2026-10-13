"""USIエンジンを別プロセスで起動し、局面の評価値と最善手を取得する最小クライアント。

外部ライブラリに依存しない（標準ライブラリのみ）。エンジン本体（やねうら王, GPLv3）とは
標準入出力のUSIプロトコルでのみ通信する。
"""
from __future__ import annotations

import subprocess
import threading
import queue
from dataclasses import dataclass, field
from pathlib import Path

MATE_CP = 30000  # 詰みを評価値に換算するときの値


@dataclass
class AnalysisResult:
    sfen_or_moves: str
    bestmove: str
    ponder: str | None
    score_cp: int | None        # 手番側から見た評価値（センチポーン）
    score_mate: int | None      # 詰み手数（正=手番側が詰ます、負=詰まされる）
    depth: int | None
    nodes: int | None
    pv: list[str] = field(default_factory=list)
    candidates: list["AnalysisResult"] = field(default_factory=list)

    def score_for_sente(self, side_to_move_is_sente: bool) -> int:
        """先手から見た評価値に変換（詰みは±MATE_CP）。"""
        if self.score_mate is not None:
            v = MATE_CP if self.score_mate > 0 else -MATE_CP
        else:
            v = self.score_cp or 0
        return v if side_to_move_is_sente else -v


class UsiEngine:
    def __init__(self, path: str | Path, options: dict[str, str | int] | None = None,
                 timeout: float = 30.0):
        self.path = Path(path).resolve()
        self.timeout = timeout
        # やねうら王は作業ディレクトリ基準で eval/ を読むので cwd をエンジンの場所にする
        self.proc = subprocess.Popen(
            [str(self.path)], cwd=self.path.parent,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        self._lines: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()  # 複数ユーザーが同じエンジンを使っても命令が混ざらないように
        self._multipv = int((options or {}).get("MultiPV", 1))
        threading.Thread(target=self._reader, daemon=True).start()

        self.name = ""
        self._send("usi")
        for line in self._read_until("usiok"):
            if line.startswith("id name "):
                self.name = line[len("id name "):]
        for k, v in (options or {}).items():
            self._send(f"setoption name {k} value {v}")
        self._send("isready")
        self._read_until("readyok", timeout=120)  # 評価関数の読み込みに時間がかかる
        self._send("usinewgame")

    # ---- 低レベル ----
    def _reader(self):
        assert self.proc.stdout
        for line in self.proc.stdout:
            self._lines.put(line.rstrip("\n"))

    def _send(self, cmd: str):
        assert self.proc.stdin
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def _read_until(self, prefix: str, timeout: float | None = None):
        out = []
        while True:
            try:
                line = self._lines.get(timeout=timeout or self.timeout)
            except queue.Empty:
                raise TimeoutError(f"USIエンジンから '{prefix}' が返りません")
            out.append(line)
            if line.startswith(prefix):
                return out

    # ---- 解析 ----
    def analyze(self, position: str = "startpos", moves: list[str] | None = None,
                byoyomi_ms: int = 1000, multipv: int = 1) -> AnalysisResult:
        """position: 'startpos' または 'sfen <SFEN>'。moves: USI形式の指し手列。

        multipv > 1 のとき、候補手ごとの結果を AnalysisResult.candidates に入れる（1番目が最善）。
        """
        cmd = f"position {position}"
        if moves:
            cmd += " moves " + " ".join(moves)
        with self._lock:
            if self.proc.poll() is not None:
                raise RuntimeError("USIエンジンが終了しています")
            if multipv != self._multipv:
                self._send(f"setoption name MultiPV value {multipv}")
                self._multipv = multipv
            self._send(cmd)
            self._send(f"go byoyomi {byoyomi_ms}")
            lines = self._read_until("bestmove", timeout=byoyomi_ms / 1000 + self.timeout)

        # multipv番号ごとに最後（=最も深い）の info を残す
        by_pv: dict[int, dict] = {}
        for line in lines:
            if line.startswith("info ") and " score " in line and " pv " in line:
                d = _parse_info(line)
                by_pv[d.get("multipv", 1)] = d
        bm = lines[-1].split()
        cands = [self._result(cmd, by_pv[k]) for k in sorted(by_pv)]
        best = by_pv.get(1, {})
        res = self._result(cmd, best)
        res.bestmove = bm[1]
        res.ponder = bm[3] if len(bm) >= 4 and bm[2] == "ponder" else None
        res.candidates = cands
        return res

    @staticmethod
    def _result(cmd: str, d: dict) -> "AnalysisResult":
        pv = d.get("pv", [])
        return AnalysisResult(
            sfen_or_moves=cmd, bestmove=pv[0] if pv else "", ponder=pv[1] if len(pv) > 1 else None,
            score_cp=d.get("cp"), score_mate=d.get("mate"),
            depth=d.get("depth"), nodes=d.get("nodes"), pv=pv,
        )

    def close(self):
        try:
            self._send("quit")
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _parse_info(line: str) -> dict:
    t = line.split()
    d: dict = {}
    i = 1
    while i < len(t):
        k = t[i]
        if k in ("depth", "seldepth", "nodes", "nps", "time", "multipv", "hashfull"):
            d[k] = int(t[i + 1]); i += 2
        elif k == "score":
            kind, val = t[i + 1], t[i + 2]
            if kind == "cp":
                d["cp"] = int(val)
            else:  # mate。"+" / "-" だけのこともある
                d["mate"] = int(val) if val.lstrip("+-") else (1 if val.startswith("+") else -1)
            i += 3
            if i < len(t) and t[i] in ("lowerbound", "upperbound"):
                i += 1
        elif k == "pv":
            d["pv"] = t[i + 1:]; break
        else:
            i += 1
    return d
