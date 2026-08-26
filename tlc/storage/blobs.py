"""Content-addressed local blob store: blobs/sha256/ab/cd/<full-hash> (spec 03 §7.1.1).
Free dedup, free integrity checking, no service to run. S3Store is a stub behind the same protocol."""

from pathlib import Path
from typing import Protocol

from tlc.core.hashing import sha256_bytes


class ObjectStore(Protocol):
    def put(self, data: bytes) -> str: ...
    def get(self, sha256: str) -> bytes: ...
    def path_for(self, sha256: str) -> str: ...
    def exists(self, sha256: str) -> bool: ...


class LocalFSStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _p(self, h: str) -> Path:
        return self.root / "sha256" / h[:2] / h[2:4] / h

    def put(self, data: bytes) -> str:
        h = sha256_bytes(data)
        p = self._p(h)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return h

    def get(self, sha256: str) -> bytes:
        data = self._p(sha256).read_bytes()
        if sha256_bytes(data) != sha256:
            raise OSError(f"blob {sha256} failed integrity check")
        return data

    def path_for(self, sha256: str) -> str:
        return str(self._p(sha256))

    def exists(self, sha256: str) -> bool:
        return self._p(sha256).exists()


class S3Store:
    """Stubbed, untested (spec 03 §7.1.1): moving to S3/MinIO later is a config change."""

    def __init__(self, bucket: str, prefix: str = ""):
        self.bucket, self.prefix = bucket, prefix

    def put(self, data: bytes) -> str:  # pragma: no cover
        raise NotImplementedError("S3Store is a stub in this release")

    def get(self, sha256: str) -> bytes:  # pragma: no cover
        raise NotImplementedError("S3Store is a stub in this release")

    def path_for(self, sha256: str) -> str:  # pragma: no cover
        return f"s3://{self.bucket}/{self.prefix}{sha256}"

    def exists(self, sha256: str) -> bool:  # pragma: no cover
        raise NotImplementedError("S3Store is a stub in this release")
