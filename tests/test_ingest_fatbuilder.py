"""Synthetic test data builders (FAT16 images, DJI-looking DAT/TXT bytes, a fake core)
plus a self-test of the FAT builder. Imported by the other test_* modules."""
from __future__ import annotations

import io
import os
import struct
import zipfile
import zlib
from dataclasses import dataclass, field

import pytest

SECTOR = 512


# -- DJI-looking payloads ----------------------------------------------------------
def fake_dat(seed: int = 1, size: int = 6000, v3: bool = True) -> bytes:
    """Bytes with the DatCon-recognised DAT header ('BUILD' @16, 'DJI_LOG_V3' @242)."""
    b = bytearray(os.urandom(size)) if seed is None else bytearray((i * 31 + seed * 7) % 251 for i in range(size))
    b[0:16] = struct.pack("<IIII", 16, 3, 19, seed)
    b[16:37] = b"BUILD Nov 16 2017 10:"
    if v3:
        b[242:252] = b"DJI_LOG_V3"
    return bytes(b)


def fake_txt(seed: int = 1, size: int = 5000, version: int = 8) -> bytes:
    """Bytes with a valid DJI TXT v6-v11 prefix (details block at the end)."""
    detail_len = 400
    body = bytearray((i * 13 + seed) % 256 for i in range(size))
    struct.pack_into("<QHB", body, 0, size - detail_len, detail_len, version)
    body[11:100] = bytes(89)
    return bytes(body)


def fake_encrypted_dat(seed: int = 3, size: int = 3000) -> bytes:
    b = bytearray((i * 17 + seed) % 256 for i in range(size))
    b[0:4] = b"LOGH"
    return bytes(b)


def assistant_bundle(members: list[tuple[str, bytes]]) -> bytes:
    """DJI Assistant 2 style export: zlib( [283-byte header | data]* )."""
    raw = bytearray()
    for name, data in members:
        hdr = bytearray(283)
        hdr[0] = 1
        struct.pack_into("<I", hdr, 1, len(data))
        nb = name.encode()
        hdr[7:7 + len(nb)] = nb
        raw += hdr + data
    return zlib.compress(bytes(raw))


def make_zip(members: dict[str, bytes], stored: bool = False) -> bytes:
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as z:
        for n, d in members.items():
            z.writestr(n, d)
    return bio.getvalue()


# -- fake dronedog_core (contract C1) --------------------------------------------------------
class FakeCore:
    """Implements the C1 API shape. parse_* return `rows` frames; files whose bytes contain
    b'BOOM' raise, LOGH DATs come back encrypted_unreadable."""

    def __init__(self, rows: int = 50):
        self.rows = rows
        self.calls: list[tuple[str, str]] = []

    def version(self):
        return "fake-0.0.1"

    def detect(self, path):
        from dronedog.ingest.sniff import fallback_detect
        self.calls.append(("detect", path))
        d = fallback_detect(path)
        d["reasons"] = [r for r in d["reasons"] if "fallback" not in r] + ["fake core"]
        return d

    def _result(self, path, adapter):
        data = open(path, "rb").read()
        if b"BOOM" in data:
            raise ValueError("corrupt record stream (fake)")
        n = self.rows
        enc = data[:4] == b"LOGH"
        frames = {} if enc else {
            "t_offset_ms": [i * 100 for i in range(n)],
            "lat": [None if i == 0 else 38.0 + i * 1e-5 for i in range(n)],
            "lon": [None if i == 0 else -77.0 - i * 1e-5 for i in range(n)],
            "alt_rel_m": [float(i % 100) for i in range(n)],
            "battery_cells_v": [[3.9, 3.91, 3.9, 3.92]] * n,
            "motor_on": [i > 2 for i in range(n)],
            "flight_mode": ["P-GPS"] * n,
            "x_vendor_field": list(range(n)),          # non-C3 column -> frame_extra
        }
        return {"adapter": adapter, "adapter_version": "fake 0.0.1",
                "details": {"aircraft_sn": "FAKE123", "start_time_utc": "2017-06-01T12:00:00Z",
                            "duration_s": None if enc else n / 10},
                "frames": frames,
                "events": [] if enc else [{"t_offset_ms": 100, "kind": "mode", "severity": "info",
                                           "message": "P-GPS", "raw": None}],
                "records_total": n, "records_decoded": 0 if enc else n, "records_failed": 0,
                "warnings": [], "encrypted_unreadable": enc}

    def parse_txt(self, path, keychains_json=None):
        self.calls.append(("parse_txt", path))
        return self._result(path, "txt_djilog")

    def parse_dat(self, path):
        self.calls.append(("parse_dat", path))
        return self._result(path, "dat_v3")


@pytest.fixture
def fake_core():
    from dronedog import core_bridge
    core = FakeCore()
    core_bridge.set_core(core)
    yield core
    core_bridge.reset_core()


# -- FAT16 image builder -----------------------------------------------------------------------
def _83(name: str) -> bytes:
    base, _, ext = name.upper().partition(".")
    return base[:8].ljust(8).encode() + ext[:3].ljust(3).encode()


def _dt(y=2017, mo=6, d=1, h=12, mi=30, s=10):
    return ((y - 1980) << 9) | (mo << 5) | d, (h << 11) | (mi << 5) | (s // 2)


@dataclass
class _Node:
    name: str
    is_dir: bool
    data: bytes = b""
    children: dict = field(default_factory=dict)
    cluster: int = 0
    entry_loc: tuple | None = None  # (absolute byte offset of the dir entry)


class Fat16Builder:
    """Minimal FAT16 writer: 8.3 names, subdirectories, deletion (0xE5 + chain cleared),
    and cluster reuse to simulate overwrite."""

    def __init__(self, total_sectors: int = 16384, spc: int = 2, hidden: int = 0):
        self.bps, self.spc, self.reserved, self.nfats, self.root_ents = SECTOR, spc, 1, 2, 512
        self.total = total_sectors
        self.hidden = hidden
        root_secs = self.root_ents * 32 // SECTOR
        clusters_est = (total_sectors - self.reserved - root_secs) // spc
        self.fatsz = -(-(clusters_est + 2) * 2 // SECTOR)
        self.root_start = self.reserved + self.nfats * self.fatsz
        self.data_start = self.root_start + root_secs
        self.clusters = (total_sectors - self.data_start) // spc
        assert 4085 <= self.clusters < 65525, self.clusters
        self.img = bytearray(total_sectors * SECTOR)
        self.fat = [0] * (self.clusters + 2)
        self.fat[0], self.fat[1] = 0xFFF8, 0xFFFF
        self.root = _Node("", True)
        self.next_free = 2
        self._boot()

    @property
    def cluster_bytes(self):
        return self.spc * SECTOR

    def cluster_offset(self, c: int) -> int:
        return (self.data_start + (c - 2) * self.spc) * SECTOR

    def _boot(self):
        b = self.img
        b[0:3] = b"\xEB\x3C\x90"
        b[3:11] = b"MSDOS5.0"
        struct.pack_into("<HBHBHHBHHHII", b, 11, SECTOR, self.spc, self.reserved, self.nfats, self.root_ents,
                         self.total if self.total < 65536 else 0, 0xF8, self.fatsz, 32, 64, self.hidden,
                         self.total if self.total >= 65536 else 0)
        struct.pack_into("<BBBI", b, 36, 0x80, 0, 0x29, 0x12345678)
        b[43:54] = b"DRONEDOGSD "
        b[54:62] = b"FAT16   "
        b[510:512] = b"\x55\xaa"

    def _alloc(self, nbytes: int, at: int | None = None) -> list[int]:
        n = max(1, -(-nbytes // self.cluster_bytes))
        start = self.next_free if at is None else at
        chain = list(range(start, start + n))
        for c in chain:
            assert self.fat[c] == 0, f"cluster {c} in use"
        for a, b in zip(chain, chain[1:]):
            self.fat[a] = b
        self.fat[chain[-1]] = 0xFFFF
        if at is None:
            self.next_free = start + n
        return chain

    def _mkdirs(self, parts: list[str]) -> _Node:
        node = self.root
        for p in parts:
            if p.upper() not in node.children:
                node.children[p.upper()] = _Node(p.upper(), True)
            node = node.children[p.upper()]
        return node

    def add(self, path: str, data: bytes, at_cluster: int | None = None) -> _Node:
        parts = path.strip("/").split("/")
        d = self._mkdirs(parts[:-1])
        n = _Node(parts[-1].upper(), False, data)
        chain = self._alloc(len(data) or 1, at_cluster)
        n.cluster = chain[0]
        for i, c in enumerate(chain):
            off = self.cluster_offset(c)
            chunk = data[i * self.cluster_bytes:(i + 1) * self.cluster_bytes]
            self.img[off:off + len(chunk)] = chunk
        d.children[n.name] = n
        return n

    def _write_dir(self, node: _Node, parent_cluster: int, region_off: int | None):
        entries = []
        if node is not self.root:
            entries.append(("." + " " * 10, 0x10, node.cluster, 0, None))
            entries.append((".." + " " * 9, 0x10, parent_cluster, 0, None))
        for child in node.children.values():
            if child.is_dir:
                if not child.cluster:
                    child.cluster = self._alloc(self.cluster_bytes)[0]
                entries.append((_83(child.name).decode(), 0x10, child.cluster, 0, child))
            else:
                entries.append((_83(child.name).decode(), 0x20, child.cluster, len(child.data), child))
        if region_off is None:
            region_off = self.cluster_offset(node.cluster)
        date, time = _dt()
        for i, (nm, attr, cl, size, ch) in enumerate(entries):
            o = region_off + i * 32
            e = bytearray(32)
            e[0:11] = nm.encode() if isinstance(nm, str) else nm
            e[11] = attr
            struct.pack_into("<BHHHHHHHI", e, 13, 0, time, date, date, 0, time, date, cl, size)
            self.img[o:o + 32] = e
            if ch is not None:
                ch.entry_loc = o
        for child in node.children.values():
            if child.is_dir:
                self._write_dir(child, node.cluster if node is not self.root else 0, None)

    def finalize(self) -> bytes:
        self._write_dir(self.root, 0, self.root_start * SECTOR)
        self._flush_fat()
        return bytes(self.img)

    def _flush_fat(self):
        raw = b"".join(struct.pack("<H", v) for v in self.fat)
        for k in range(self.nfats):
            o = (self.reserved + k * self.fatsz) * SECTOR
            self.img[o:o + len(raw)] = raw

    def delete(self, node: _Node):
        """Mark a written file deleted: first byte 0xE5, FAT chain zeroed (data left in place)."""
        assert node.entry_loc is not None, "finalize() before delete()"
        self.img[node.entry_loc] = 0xE5
        c = node.cluster
        while 2 <= c < 0xFFF8:
            nxt = self.fat[c]
            self.fat[c] = 0
            c = nxt
        self._flush_fat()

    def overwrite_with(self, deleted: _Node, path: str, data: bytes) -> _Node:
        """After delete(): allocate a NEW file on the deleted file's clusters (simulated overwrite)."""
        parts = path.strip("/").split("/")
        parent = self._mkdirs(parts[:-1])
        # place a new dir entry in a free slot of the parent dir
        n = _Node(parts[-1].upper(), False, data)
        chain = self._alloc(len(data), at=deleted.cluster)
        n.cluster = chain[0]
        for i, c in enumerate(chain):
            off = self.cluster_offset(c)
            chunk = data[i * self.cluster_bytes:(i + 1) * self.cluster_bytes]
            self.img[off:off + self.cluster_bytes] = chunk.ljust(self.cluster_bytes, b"\0")
        base = self.root_start * SECTOR if parent is self.root else self.cluster_offset(parent.cluster)
        for slot in range(16 if parent is not self.root else self.root_ents):
            o = base + slot * 32
            if self.img[o] == 0:
                e = bytearray(32)
                e[0:11] = _83(n.name)
                e[11] = 0x20
                date, time = _dt()
                struct.pack_into("<BHHHHHHHI", e, 13, 0, time, date, date, 0, time, date, n.cluster, len(data))
                self.img[o:o + 32] = e
                n.entry_loc = o
                break
        parent.children[n.name] = n
        self._flush_fat()
        return n

    def image(self) -> bytes:
        return bytes(self.img)


def wrap_mbr(fs: bytes, start_lba: int = 2048, ptype: int = 0x06) -> bytes:
    nsec = len(fs) // SECTOR
    mbr = bytearray(SECTOR)
    struct.pack_into("<B3sB3sII", mbr, 446, 0x00, b"\x00\x02\x00", ptype, b"\xfe\xff\xff", start_lba, nsec)
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr) + bytes((start_lba - 1) * SECTOR) + fs


def build_sd_image(with_mbr: bool = True):
    """Phantom-style internal SD: MISC/LOG/FLY*.DAT + one deleted log + one overwritten log +
    a stored zip containing another log + a junk file.
    Returns (image_bytes, info dict)."""
    fb = Fat16Builder(hidden=2048 if with_mbr else 0)
    f1 = fb.add("MISC/LOG/FLY001.DAT", fake_dat(1, 7000))
    f2 = fb.add("MISC/LOG/FLY002.DAT", fake_dat(2, 5000))
    f3 = fb.add("MISC/LOG/FLY003.DAT", fake_dat(3, 3000))
    fb.add("MISC/NOTES.TXT", b"not a log at all\r\n" * 10)
    fb.add("FLIGHTS.ZIP", make_zip({"inner/FLY009.DAT": fake_dat(9, 4000)}, stored=False))
    fb.add("DCIM/100MEDIA/DJI_0001.JPG", b"\xff\xd8\xff" + bytes(4000))
    fb.add("SNIFFED.BIN", fake_txt(5, 3000))
    fb.finalize()
    fb.delete(f2)
    fb.delete(f3)
    fb.overwrite_with(f3, "MISC/LOG/FLY004.DAT", fake_dat(4, 3000))
    fs = fb.image()
    info = {"fs": fs, "builder": fb, "f1": f1, "f2": f2, "f3": f3,
            "fs_offset": 2048 * SECTOR if with_mbr else 0}
    return (wrap_mbr(fs) if with_mbr else fs), info


# -- self-test ---------------------------------------------------------------------------------
def test_fat_builder_is_readable_by_tsk(tmp_path):
    import pytsk3
    img, info = build_sd_image(with_mbr=True)
    p = tmp_path / "sd.dd"
    p.write_bytes(img)
    im = pytsk3.Img_Info(str(p))
    vs = pytsk3.Volume_Info(im)
    parts = [x for x in vs if x.flags & pytsk3.TSK_VS_PART_FLAG_ALLOC]
    assert len(parts) == 1 and parts[0].start == 2048
    fs = pytsk3.FS_Info(im, offset=2048 * 512)
    names = {e.info.name.name.decode() for e in fs.open_dir("/MISC/LOG")}
    assert "FLY001.DAT" in names and "FLY004.DAT" in names
    # deleted entries are exposed by TSK with the first char replaced
    assert any(n.endswith("LY002.DAT") and n != "FLY002.DAT" for n in names), names
