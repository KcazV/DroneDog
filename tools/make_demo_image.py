"""Build the CONSTRUCTED demo SD image (backup for the class demo).

Packs real, public Phantom 4 Pro flight logs (DataverseNO doi:10.18710/AFDYLS, CC0,
EPFL flights) into a FAT16 SD-card image behind an MBR, then deletes one log so the
deleted-file recovery path can be shown. This is NOT a device acquisition: the image
is labeled as constructed in its name and in the README written next to it, which also
records the SHA-256 of every input log so the recovered files can be checked against
the originals.

Usage: python tools/make_demo_image.py [out_dir]   (default corpus/demo)
"""
import hashlib
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_ingest_fatbuilder import SECTOR, Fat16Builder  # noqa: E402

LOGS = ["FLY134", "FLY135", "FLY140", "FLY168", "FLY179", "FLY182"]
DELETE = "FLY140"
SRC = ROOT / "corpus" / "_work" / "dataverse"
START_LBA = 2048


class _Sized:
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n


def main(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in LOGS:
        hits = list(SRC.rglob(f"{name}.DAT"))
        if not hits:
            raise SystemExit(f"missing {name}.DAT under {SRC}")
        files[name] = hits[0]
    total = sum(p.stat().st_size for p in files.values())
    spc = 64  # 32 KiB clusters
    sectors = ((total * 12 // 10) // (SECTOR * spc) + 6000) * spc
    fb = Fat16Builder(total_sectors=sectors, spc=spc, hidden=START_LBA)
    nodes, hashes = {}, {}
    for name, p in files.items():
        data = p.read_bytes()
        hashes[name] = hashlib.sha256(data).hexdigest()
        nodes[name] = fb.add(f"MISC/LOG/{name}.DAT", data)
        nodes[name].data = b"\0" * len(data)  # keep size for the dir entry, free the copy
    fb.add("MISC/README.TXT", b"DJI aircraft internal storage (constructed demo image)\r\n")
    fb.add("DCIM/100MEDIA/DJI_0001.JPG", b"\xff\xd8\xff\xe0" + bytes(60000))
    fb.finalize()
    fb.delete(nodes[DELETE])

    img = out_dir / "DroneDog_DEMO_P4P_internalSD_CONSTRUCTED.dd"
    mbr = bytearray(SECTOR)
    struct.pack_into("<B3sB3sII", mbr, 446, 0x00, b"\x00\x02\x00", 0x06, b"\xfe\xff\xff", START_LBA, sectors)
    mbr[510:512] = b"\x55\xaa"
    h = hashlib.sha256()
    with img.open("wb") as fh:
        for chunk in (bytes(mbr), bytes((START_LBA - 1) * SECTOR)):
            fh.write(chunk)
            h.update(chunk)
        view = memoryview(fb.img)
        for i in range(0, len(view), 64 << 20):
            fh.write(view[i:i + (64 << 20)])
            h.update(view[i:i + (64 << 20)])
    readme = out_dir / "README_DEMO.txt"
    lines = [
        "DroneDog demo evidence -- CONSTRUCTED IMAGE, NOT A DEVICE ACQUISITION",
        f"built {datetime.now(timezone.utc).isoformat(timespec='seconds')} by tools/make_demo_image.py",
        "",
        f"image: {img.name}  size {img.stat().st_size:,} B  sha256 {h.hexdigest()}",
        "layout: MBR, FAT16 partition at LBA 2048, logs in MISC/LOG/",
        f"deleted on purpose: MISC/LOG/{DELETE}.DAT (directory entry 0xE5, FAT chain cleared, data intact)",
        "",
        "source logs: DataverseNO doi:10.18710/AFDYLS (CC0), EPFL Phantom 4 Pro flights,",
        "validated against DatCon 4.0.5 CSVs (corpus/baselines/dat/VALIDATION.md)",
    ] + [f"  {n}.DAT  sha256 {hashes[n]}" for n in LOGS]
    readme.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "corpus" / "demo")
