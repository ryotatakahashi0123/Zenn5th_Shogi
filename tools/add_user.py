"""利用者を追加する（同じ名前があればパスワードを変える）。

    python tools/add_user.py <利用者名>           # パスワードは画面に出さずに聞く
    BUCKET=<バケット名> python tools/add_user.py <利用者名>   # 公開しているサービスの利用者（Cloud Storage に書く）

保存するのはパスワードのハッシュだけ。BUCKET が無ければ手元の ./data/auth/users.json に書く。
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shogi_coach import auth, storage  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    pw = getpass.getpass("パスワード: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
    try:
        auth.add_user(sys.argv[1], pw)
    except ValueError as e:
        sys.exit(str(e))
    print(f"{sys.argv[1]} を登録しました（保存先: {storage.where()}/{auth.USERS}）")
