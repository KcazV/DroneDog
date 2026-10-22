"""C6 custody / methodology log: append-only, hash-chained JSONL.

Each line is the canonical JSON (sorted keys, compact separators, UTF-8) of:

    {"seq", "ts_utc", "actor", "action", "inputs", "outputs", "tool",
     "tool_version", "component", "params", "prev_hash", "hash"}

* ``hash``      = sha256 of the canonical JSON of the entry *without* ``hash``.
* ``prev_hash`` = sha256 of the previous line's bytes (without the newline);
                  64 zeros for the first entry.
* ``seq``       = 1, 2, 3 ... with no gaps.

Because lines are written canonically, any edit to any byte of a past line breaks
either that line's ``hash`` or the next line's ``prev_hash``. Truncation of the tail
is caught by comparing against the head (seq + hash) mirrored into the case DB
(``custody_head`` table) on every append; pass ``expected_head`` to verify_chain.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from dronedog import TOOL_NAME, __version__

GENESIS = "0" * 64
ACTIONS = {
    "case.create", "case.open", "evidence.add", "evidence.verify", "artifact.extract",
    "artifact.detect", "artifact.parse", "analysis.run", "report.export", "job.start",
    "job.end", "note",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def line_hash(line: bytes) -> str:
    return hashlib.sha256(line.rstrip(b"\r\n")).hexdigest()


def io_ref(path: str | os.PathLike | None, sha256: str | None, **extra) -> dict:
    """Build an inputs/outputs element."""
    d = {"path": None if path is None else str(path), "sha256": sha256}
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


@dataclass
class ChainReport:
    ok: bool
    entries: int
    first_bad_line: int | None = None      # 1-based line number in custody.log
    first_bad_seq: int | None = None
    reason: str | None = None
    head_seq: int | None = None
    head_hash: str | None = None
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "entries": self.entries, "first_bad_line": self.first_bad_line,
            "first_bad_seq": self.first_bad_seq, "reason": self.reason,
            "head_seq": self.head_seq, "head_hash": self.head_hash,
        }


class CustodyLog:
    """Thread-safe appender. One instance per case (shared by threads)."""

    def __init__(self, path: str | os.PathLike,
                 on_append: Callable[[int, str], None] | None = None,
                 tool_version: str | None = None):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._on_append = on_append
        self.tool_version = tool_version or __version__
        self._seq: int | None = None
        self._prev: str | None = None
        self._size: int | None = None

    # -- tail state -------------------------------------------------------
    def _load_tail(self) -> None:
        seq, prev = 0, GENESIS
        if self.path.exists():
            last = None
            with open(self.path, "rb") as fh:
                # read the last non-empty line cheaply
                fh.seek(0, os.SEEK_END)
                end = fh.tell()
                pos = end
                block = b""
                while pos > 0:
                    step = min(65536, pos)
                    pos -= step
                    fh.seek(pos)
                    block = fh.read(step) + block
                    stripped = block.rstrip(b"\r\n")
                    if b"\n" in stripped:
                        last = stripped.rsplit(b"\n", 1)[1]
                        break
                else:
                    stripped = block.rstrip(b"\r\n")
                    last = stripped or None
            if last:
                obj = json.loads(last.decode("utf-8"))
                seq = int(obj["seq"])
                prev = line_hash(last)
        self._seq, self._prev = seq, prev
        self._size = self.path.stat().st_size if self.path.exists() else 0

    def append(self, action: str, *, inputs: Iterable[dict] = (), outputs: Iterable[dict] = (),
               component: str, params: dict | None = None, actor: str = "system",
               tool_version: str | None = None) -> dict:
        if action not in ACTIONS:
            raise ValueError(f"unknown custody action {action!r}")
        with self._lock:
            cur_size = self.path.stat().st_size if self.path.exists() else 0
            if self._seq is None or cur_size != self._size:
                self._load_tail()  # someone else appended (another process) — resync
            entry = {
                "seq": self._seq + 1,
                "ts_utc": utc_now(),
                "actor": actor,
                "action": action,
                "inputs": list(inputs),
                "outputs": list(outputs),
                "tool": TOOL_NAME,
                "tool_version": tool_version or self.tool_version,
                "component": component,
                "params": params or {},
                "prev_hash": self._prev,
            }
            entry["hash"] = entry_hash(entry)
            line = canonical(entry).encode("utf-8")
            # O_APPEND write of one complete line, then fsync
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0))
            try:
                os.write(fd, line + b"\n")
                os.fsync(fd)
            finally:
                os.close(fd)
            self._seq = entry["seq"]
            self._prev = line_hash(line)
            self._size = cur_size + len(line) + 1
            if self._on_append:
                self._on_append(entry["seq"], entry["hash"])
            return entry

    def entries(self) -> list[dict]:
        out = []
        if self.path.exists():
            with open(self.path, "rb") as fh:
                for raw in fh:
                    if raw.strip():
                        out.append(json.loads(raw.decode("utf-8")))
        return out


def verify_chain(path: str | os.PathLike, expected_head: tuple[int, str] | None = None
                 ) -> ChainReport:
    """Verify custody.log. Returns a ChainReport pinpointing the first bad line.

    Checks, per line: valid JSON object; canonical serialisation (byte-for-byte);
    ``hash`` matches content; ``prev_hash`` matches sha256 of previous line;
    ``seq`` increments by one. With ``expected_head=(seq, hash)`` (from the case DB),
    also detects truncation / replacement of the tail.
    """
    path = Path(path)
    if not path.exists():
        return ChainReport(False, 0, reason="custody log missing")
    prev = GENESIS
    expect_seq = 1
    n = 0
    last_hash = None
    with open(path, "rb") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip(b"\r\n")
            if not raw.endswith(b"\n"):
                return ChainReport(False, n, lineno, None, "line not newline-terminated (partial write or edit)")
            if not line:
                return ChainReport(False, n, lineno, None, "blank line inserted")
            try:
                obj = json.loads(line.decode("utf-8"))
                if not isinstance(obj, dict):
                    raise ValueError("not an object")
            except Exception as e:  # noqa: BLE001
                return ChainReport(False, n, lineno, None, f"unparseable line: {e}")
            seq = obj.get("seq")
            if canonical(obj).encode("utf-8") != line:
                return ChainReport(False, n, lineno, seq, "line is not in canonical form (edited)")
            if seq != expect_seq:
                return ChainReport(False, n, lineno, seq, f"seq {seq} != expected {expect_seq}")
            if obj.get("prev_hash") != prev:
                return ChainReport(False, n, lineno, seq,
                                   "prev_hash does not match previous line (previous line edited, removed or reordered)")
            if obj.get("hash") != entry_hash(obj):
                return ChainReport(False, n, lineno, seq, "entry hash mismatch (this line edited)")
            prev = line_hash(line)
            last_hash = obj["hash"]
            expect_seq += 1
            n += 1
    rep = ChainReport(True, n, head_seq=n or None, head_hash=last_hash)
    if expected_head is not None:
        eseq, ehash = expected_head
        if eseq != n or (ehash and ehash != last_hash):
            rep.ok = False
            rep.first_bad_line = min(n, eseq) + 1 if eseq > n else None
            rep.first_bad_seq = None
            rep.reason = (f"log ends at seq {n} but case DB head records seq {eseq}"
                          + (" (tail truncated)" if eseq > n else "" if eseq == n else " (entries appended outside DroneDog)"))
            if eseq == n and ehash != last_hash:
                rep.reason = "last entry hash differs from case DB head (tail replaced)"
                rep.first_bad_line = n
    return rep
