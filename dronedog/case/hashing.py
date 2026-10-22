"""Streaming hash + copy helpers.

Hashes are computed while bytes stream through, in 8 MiB chunks, with each
algorithm updated on its own worker thread (hashlib releases the GIL for large
buffers) and the next chunk read while the previous one is hashed. Memory use is
bounded (two chunk buffers) regardless of file size, so 100+ GB images are fine.
"""
from __future__ import annotations

import hashlib
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, Sequence

CHUNK = 8 * 1024 * 1024
DEFAULT_ALGOS = ("sha256", "sha1")
IMAGE_ALGOS = ("sha256", "sha1", "md5")

# progress(bytes_done) — called after every chunk
ProgressFn = Callable[[int], None]


class Cancelled(Exception):
    """Raised when a cancel event is set during a long operation."""


@dataclass
class HashResult:
    size: int = 0
    digests: dict[str, str] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        return self.digests.get("sha256", "")

    @property
    def sha1(self) -> str:
        return self.digests.get("sha1", "")

    @property
    def md5(self) -> str | None:
        return self.digests.get("md5")


class MultiHasher:
    """Feed chunks; all algorithms update in parallel."""

    def __init__(self, algos: Sequence[str] = DEFAULT_ALGOS):
        self.algos = tuple(algos)
        self._h = [hashlib.new(a) for a in self.algos]
        self._pool = ThreadPoolExecutor(max_workers=len(self._h)) if len(self._h) > 1 else None
        self._pending: list = []
        self.size = 0

    def update(self, data) -> None:
        """Start hashing `data` (bytes/memoryview). The buffer must not be
        modified until the next call to update()/wait()/result()."""
        self.wait()
        n = len(data)
        if not n:
            return
        self.size += n
        if self._pool is None:
            self._h[0].update(data)
        else:
            self._pending = [self._pool.submit(h.update, data) for h in self._h]

    def wait(self) -> None:
        for f in self._pending:
            f.result()
        self._pending = []

    def result(self) -> HashResult:
        self.wait()
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None
        return HashResult(self.size, {a: h.hexdigest() for a, h in zip(self.algos, self._h)})


def _pump(src: BinaryIO, sinks: Iterable[Callable], hasher: MultiHasher,
          progress: ProgressFn | None, cancel, limit: int | None = None) -> None:
    """Read src in chunks, hash every chunk, hand every chunk to each sink.

    Two alternating buffers: while chunk N is hashed on worker threads, the main
    thread writes it to the sinks and then reads chunk N+1 into the other buffer.
    """
    bufs = (bytearray(CHUNK), bytearray(CHUNK))
    i = 0
    done = 0
    readinto = getattr(src, "readinto", None)
    while True:
        if cancel is not None and cancel.is_set():
            hasher.wait()
            raise Cancelled()
        want = CHUNK if limit is None else min(CHUNK, limit - done)
        if want <= 0:
            break
        buf = bufs[i]
        if readinto is not None:
            n = readinto(memoryview(buf)[:want])
            if not n:
                break
            view = memoryview(buf)[:n]
        else:
            data = src.read(want)
            if not data:
                break
            n = len(data)
            view = memoryview(data)
        hasher.update(view)  # waits for previous chunk first, so the other buffer is free
        for sink in sinks:
            sink(view)
        done += n
        if progress:
            progress(done)
        i ^= 1
    hasher.wait()


def hash_stream(src: BinaryIO, algos: Sequence[str] = DEFAULT_ALGOS,
                progress: ProgressFn | None = None, cancel=None) -> HashResult:
    h = MultiHasher(algos)
    _pump(src, (), h, progress, cancel)
    return h.result()


def hash_file(path: str | os.PathLike, algos: Sequence[str] = DEFAULT_ALGOS,
              progress: ProgressFn | None = None, cancel=None) -> HashResult:
    with open(path, "rb", buffering=0) as fh:
        return hash_stream(fh, algos, progress, cancel)


def hash_files(paths: Sequence[str | os.PathLike], algos: Sequence[str] = DEFAULT_ALGOS,
               progress: ProgressFn | None = None, cancel=None
               ) -> tuple[HashResult, list[HashResult]]:
    """Hash a segment set: returns (hash over the concatenation, per-file hashes).

    For split raw (.001/.002...) the concatenated hash equals the media hash an
    imager records (e.g. FTK's .md5/.sha1 sidecars)."""
    whole = MultiHasher(algos)
    per: list[HashResult] = []
    base = 0
    for p in paths:
        part = MultiHasher(algos)

        def prog(n, _b=base):
            if progress:
                progress(_b + n)

        with open(p, "rb", buffering=0) as fh:
            # one read feeds both hashers
            _pump(fh, (whole.update, ), part, prog, cancel)
        whole.wait()
        r = part.result()
        per.append(r)
        base += r.size
    return whole.result(), per


def copy_with_hash(src: BinaryIO, dst_path: str | os.PathLike,
                   algos: Sequence[str] = DEFAULT_ALGOS,
                   progress: ProgressFn | None = None, cancel=None,
                   limit: int | None = None) -> HashResult:
    """Stream src -> dst_path, hashing on the way. Fsyncs dst before returning."""
    h = MultiHasher(algos)
    with open(dst_path, "wb", buffering=0) as out:
        def write(view, _w=out.write):
            mv = view
            while len(mv):
                k = _w(mv)
                mv = mv[k:]
        try:
            _pump(src, (write,), h, progress, cancel, limit)
        finally:
            h.wait()
        out.flush()
        os.fsync(out.fileno())
    return h.result()


def set_readonly(path: str | os.PathLike) -> None:
    """Set the read-only attribute (Windows FILE_ATTRIBUTE_READONLY / POSIX r--)."""
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def clear_readonly(path: str | os.PathLike) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def is_readonly(path: str | os.PathLike) -> bool:
    return not os.access(path, os.W_OK)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path_text(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
