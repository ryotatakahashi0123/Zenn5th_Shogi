"""google.cloud.storage.Bucket の偽物(試験用)。GcsStore が使う口だけを持つ。
本物と違う点: 版番号は単純な連番。実際のCloud Storageでは動かしていない。"""


class PreconditionFailed(Exception):
    code = 412


class _Blob:
    def __init__(self, bucket, name):
        self._b, self.name = bucket, name

    @property
    def generation(self):
        return self._b.data[self.name][1]

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        cur = self._b.data.get(self.name)
        if if_generation_match is not None and (cur[1] if cur else 0) != if_generation_match:
            raise PreconditionFailed("generation mismatch")
        self._b.seq += 1
        self._b.data[self.name] = (data.encode("utf-8") if isinstance(data, str) else data, self._b.seq)

    def download_as_bytes(self):
        return self._b.data[self.name][0]

    def delete(self):
        self._b.data.pop(self.name, None)  # 本物は無いと NotFound。GcsStore は存在を確かめてから消す


class FakeBucket:
    def __init__(self):
        self.data, self.seq = {}, 0

    def blob(self, name):
        return _Blob(self, name)

    def get_blob(self, name):
        return _Blob(self, name) if name in self.data else None

    def list_blobs(self, prefix=""):
        return [_Blob(self, n) for n in sorted(self.data) if n.startswith(prefix)]
