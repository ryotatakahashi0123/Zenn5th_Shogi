"""samples/raw の棋譜を匿名化して samples/anon に書き出す(06のT17, FR-45)。

使い方: python scripts/make_anon_samples.py --my-id <自分のID> [--my-id ...]
raw は .gitignore で除外されている。コミットするのは anon のみ。
改行(CRLF/LF)は元のまま保つ。
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.domain.anonymize import anonymize_kif  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--my-id", action="append", default=[], help="自分の対局者ID(複数可)")
    ap.add_argument("--raw", default=str(ROOT / "samples" / "raw"))
    ap.add_argument("--out", default=str(ROOT / "samples" / "anon"))
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(pathlib.Path(a.raw).glob("*.kif"))
    if not files:
        print("samples/raw に .kif がない", file=sys.stderr)
        return 1
    for f in files:
        raw = f.read_bytes().decode("utf-8")
        (out / f.name).write_bytes(anonymize_kif(raw, a.my_id).encode("utf-8"))
        print("anon:", f.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
