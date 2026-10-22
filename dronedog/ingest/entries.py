"""Common types for the ingest walkers."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import BinaryIO, Callable


@dataclass
class Entry:
    """One file inside some container, before the candidate policy is applied."""
    path: str                                  # logical path within the container ('/'-joined)
    name: str
    size: int | None
    opener: Callable[[], BinaryIO]             # fresh readable stream positioned at 0
    meta: dict = field(default_factory=dict)   # provenance: deleted, meta_addr, offset, times, ios...
    head_reader: Callable[[int], bytes] | None = None   # cheap read of the first n bytes
    _head: bytes | None = None

    def head(self, n: int) -> bytes:
        if self._head is None or (len(self._head) < n and (self.size is None or len(self._head) < self.size)):
            if self.head_reader is not None:
                self._head = self.head_reader(n)
            else:
                with self.opener() as fh:
                    self._head = fh.read(n)
        return self._head[:n]


@dataclass
class Candidate:
    """A selected flight-log artifact, ready to extract.

    ``opener`` is only guaranteed to work while the walker generator is positioned on
    this candidate (nested-zip / Assistant members are spooled to temp files and zip
    handles are closed as the walk moves on) - extract before advancing."""
    name: str
    inner_path: str                     # full path inside the evidence: 'p1/MISC/LOG/FLY001.DAT', 'a.zip!/x.txt'
    size: int | None
    opener: Callable[[], BinaryIO]
    container: str                      # file|folder|zip|ios_backup|image|assistant
    reasons: list[str] = field(default_factory=list)
    sniff: dict | None = None
    deleted: bool = False
    meta_addr: int | None = None
    offset: int | None = None
    partition_index: int | None = None
    partition_offset: int | None = None
    fs_type: str | None = None
    overwrite_status: str | None = None
    times: dict = field(default_factory=dict)
    ios: dict | None = None
    app_hint: str | None = None
    extra: dict = field(default_factory=dict)

    def provenance(self) -> dict:
        d = {
            "inner_path": self.inner_path, "container": self.container, "size": self.size,
            "deleted": self.deleted, "meta_addr": self.meta_addr, "offset": self.offset,
            "partition_index": self.partition_index, "partition_offset": self.partition_offset,
            "fs_type": self.fs_type, "overwrite_status": self.overwrite_status,
            "times": self.times or None, "ios": self.ios, "app_hint": self.app_hint,
            "reasons": self.reasons,
        }
        if self.extra:
            d["extra"] = self.extra
        return {k: v for k, v in d.items() if v not in (None, {}, [])} | {"deleted": self.deleted}


class RandomReader(io.RawIOBase):
    """Seekable raw stream over a read_at(offset, n) -> bytes function."""

    def __init__(self, read_at: Callable[[int, int], bytes], size: int):
        self._read_at = read_at
        self._size = size
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, off: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = off
        elif whence == 1:
            self._pos += off
        else:
            self._pos = self._size + off
        self._pos = max(0, self._pos)
        return self._pos

    def readinto(self, b) -> int:
        n = min(len(b), self._size - self._pos)
        if n <= 0:
            return 0
        data = self._read_at(self._pos, n)
        k = len(data)
        b[:k] = data
        self._pos += k
        return k


class TolerantRandomReader(RandomReader):
    """Like RandomReader, but a read error after some data was delivered ends the stream
    (EOF) instead of raising, and is kept in ``short_read_error`` so the extractor can
    record a partial recovery (e.g. a file whose tail lies beyond the end of a partial
    image). An error at offset 0 still raises - nothing was recoverable."""

    short_read_error: str | None = None
    short_read_at: int | None = None

    def readinto(self, b) -> int:
        if self.short_read_error is not None:
            return 0
        try:
            return super().readinto(b)
        except Exception as e:  # noqa: BLE001
            if self._pos == 0:
                raise
            # retry sector by sector up to the failure point to salvage what we can
            got = 0
            mv = memoryview(b)
            while got + 512 <= len(mv) and self._pos < self._size:
                try:
                    chunk = self._read_at(self._pos, min(512, self._size - self._pos))
                except Exception:  # noqa: BLE001
                    break
                if not chunk:
                    break
                mv[got:got + len(chunk)] = chunk
                got += len(chunk)
                self._pos += len(chunk)
            self.short_read_error = str(e).splitlines()[0][:300]
            self.short_read_at = self._pos
            return got


def buffered(raw: io.RawIOBase, size: int = 1024 * 1024) -> io.BufferedReader:
    return io.BufferedReader(raw, buffer_size=size)


@dataclass
class WalkStats:
    files_seen: int = 0
    dirs_seen: int = 0
    candidates: int = 0
    deleted_seen: int = 0
    deleted_candidates: int = 0
    containers: list[dict] = field(default_factory=list)   # zips/images/partitions opened
    errors: list[dict] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)            # reason -> count
    bytes_total: int = 0

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def error(self, where: str, err: str) -> None:
        if len(self.errors) < 500:
            self.errors.append({"where": where, "error": err})
        else:
            self.skip("errors_truncated")

    def as_dict(self) -> dict:
        return {
            "files_seen": self.files_seen, "dirs_seen": self.dirs_seen, "candidates": self.candidates,
            "deleted_seen": self.deleted_seen, "deleted_candidates": self.deleted_candidates,
            "containers": self.containers, "errors": self.errors, "skipped": self.skipped,
            "bytes_total": self.bytes_total,
        }
