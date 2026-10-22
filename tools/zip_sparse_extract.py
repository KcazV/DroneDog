#!/usr/bin/env python3
"""Extract entries from a zip that is only partly downloaded (build-time corpus tool).

Used to get DataverseNO AFDYLS EPFL DAT+CSV pairs for tools/dat_validate.py without
waiting for the whole 6.5 GB 02_EPFL_FLIGHTS.zip. Sources of bytes, in order:
  1. the zip's tail (central directory), fetched once with an HTTP Range request,
  2. byte-range files  <zip>.<start>-<end>.part  (in the zip's folder or ranges/),
  3. the partially downloaded zip itself (prefix).
An entry is extracted only if all of its bytes are present; zipfile verifies each
entry's CRC-32 against the central directory, so a truncated entry raises instead of
producing a bad file. The input zip is opened read-only and never modified.

    # 1) tail (central directory; the last 1 MB is plenty for <100 entries)
    python tools/zip_sparse_extract.py fetch-tail URL TOTAL_BYTES tail.bin
    # 2) list entries and coverage; print byte ranges for wanted entries
    python tools/zip_sparse_extract.py list ZIP TAIL TOTAL_BYTES [--want FLY140 ...]
    # 3) fetch a range (curl works too: curl -L -r A-B URL >> ZIP.A-B.part)
    python tools/zip_sparse_extract.py fetch-range URL A B OUT.part
    # 4) extract every complete FLIGHT/*.DAT|*.csv entry
    python tools/zip_sparse_extract.py extract ZIP TAIL TOTAL_BYTES OUTDIR

AFDYLS EPFL: URL https://dataverse.no/api/access/datafile/128776, TOTAL 6468142762.
"""
import argparse
import glob
import io
import os
import re
import sys
import urllib.request
import zipfile


class Sparse(io.RawIOBase):
    def __init__(self, part: str, tail: str, total: int):
        self.total = total
        self.p = open(part, "rb") if os.path.exists(part) else None
        self.psize = os.path.getsize(part) if self.p else 0
        self.t = open(tail, "rb").read()
        self.tstart = total - len(self.t)
        self.pos = 0
        self.ranges = []
        pats = [part + ".*-*.part",
                os.path.join(os.path.dirname(part), "ranges", os.path.basename(part) + ".*-*.part")]
        for pat in pats:
            for f in glob.glob(pat):
                m = re.search(r"\.(\d+)-(\d+)\.part$", f)
                a, b = int(m.group(1)), int(m.group(2))
                have = os.path.getsize(f)
                if have > 0:
                    self.ranges.append((a, min(b, a + have - 1), open(f, "rb")))

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.total + off)
        return self.pos

    def has(self, a: int, b: int) -> bool:
        """True if bytes [a, b] are all available."""
        if a >= self.tstart:
            return True
        if b < self.psize:
            return True
        return any(ra <= a and b <= rb for ra, rb, _ in self.ranges)

    def read(self, n=-1):
        if n < 0:
            n = self.total - self.pos
        end = min(self.total, self.pos + n)
        out = bytearray()
        while self.pos < end:
            if self.pos >= self.tstart:
                out += self.t[self.pos - self.tstart:end - self.tstart]
                self.pos = end
                continue
            for ra, rb, f in self.ranges:
                if ra <= self.pos <= rb:
                    f.seek(self.pos - ra)
                    chunk = f.read(min(end, rb + 1) - self.pos)
                    out += chunk
                    self.pos += len(chunk)
                    break
            else:
                if self.pos < self.psize:
                    self.p.seek(self.pos)
                    chunk = self.p.read(min(end, self.psize) - self.pos)
                    out += chunk
                    self.pos += len(chunk)
                else:
                    raise IOError("offset %d not downloaded" % self.pos)
        return bytes(out)

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def _entries(sp: Sparse):
    z = zipfile.ZipFile(sp)
    infos = z.infolist()
    offs = sorted(i.header_offset for i in infos) + [sp.total]
    out = []
    for i in infos:
        nxt = min(o for o in offs if o > i.header_offset)
        out.append((i, nxt, sp.has(i.header_offset, nxt - 1)))
    return z, out


def _fetch(url: str, a: int, b: int, out: str, mode: str = "wb") -> None:
    req = urllib.request.Request(url, headers={"Range": "bytes=%d-%d" % (a, b)})
    with urllib.request.urlopen(req) as r, open(out, mode) as f:   # follows the 303 to S3
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch-tail")
    p.add_argument("url"); p.add_argument("total", type=int); p.add_argument("out")
    p.add_argument("--bytes", type=int, default=1_000_000)
    p = sub.add_parser("fetch-range")
    p.add_argument("url"); p.add_argument("a", type=int); p.add_argument("b", type=int)
    p.add_argument("out")
    for name in ("list", "extract"):
        p = sub.add_parser(name)
        p.add_argument("zip"); p.add_argument("tail"); p.add_argument("total", type=int)
        if name == "extract":
            p.add_argument("outdir")
            p.add_argument("--all", action="store_true", help="not only FLIGHT/ DAT+CSV")
        else:
            p.add_argument("--want", nargs="*", default=[])
    a = ap.parse_args(argv)
    if a.cmd == "fetch-tail":
        _fetch(a.url, a.total - a.bytes, a.total - 1, a.out)
        return 0
    if a.cmd == "fetch-range":
        have = os.path.getsize(a.out) if os.path.exists(a.out) else 0
        if a.a + have <= a.b:
            _fetch(a.url, a.a + have, a.b, a.out, "ab")   # resumable
        print("%s %d/%d bytes" % (a.out, os.path.getsize(a.out), a.b - a.a + 1))
        return 0
    sp = Sparse(a.zip, a.tail, a.total)
    z, ents = _entries(sp)
    if a.cmd == "list":
        for i, nxt, ok in ents:
            print("%s %12d %12d %s" % ("Y" if ok else "-", i.header_offset, i.file_size,
                                      i.filename))
        offs = sorted(i.header_offset for i, _, _ in ents) + [a.total]
        for stem in a.want:
            sel = [i for i, _, _ in ents if stem in i.filename and "/FLIGHT/" in i.filename
                   and not i.is_dir()]
            if sel:
                lo = min(i.header_offset for i in sel)
                hi = min(o for o in offs if o > max(i.header_offset for i in sel)) - 1
                print("range %s %d %d (%.1f MB)" % (stem, lo, hi, (hi - lo + 1) / 1e6))
        return 0
    n = 0
    for i, nxt, ok in ents:
        low = i.filename.lower()
        if not ok or i.is_dir():
            continue
        if not a.all and not ("/flight/" in low and (low.endswith(".dat") or low.endswith(".csv"))):
            continue
        dest = os.path.join(a.outdir, i.filename)
        if os.path.exists(dest) and os.path.getsize(dest) == i.file_size:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with z.open(i) as src, open(dest + ".tmp", "wb") as dst:   # CRC-32 checked on read
            while True:
                b = src.read(1 << 20)
                if not b:
                    break
                dst.write(b)
        os.replace(dest + ".tmp", dest)
        n += 1
        print("extracted (CRC-32 ok) %s %d" % (i.filename, i.file_size))
    print("%d entries extracted" % n, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
