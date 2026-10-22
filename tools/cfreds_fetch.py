"""Download the log-bearing artifacts from the CFReDS VTO Labs DJI folders.

Reads corpus/cfreds_listing.txt (output of cfreds_list.py list) and fetches
everything that can hold flight logs: README/hash files, every .zip (flight
logs, DJI Assistant exports, aircraft/controller logical, phone logical/backup)
and the internal-SD / internal-memory / controller-SD images. Skips external
(media) SD cards, chip-off/eMMC dumps, goggles, camera SDs and phone physicals.

Resumable: files already present at full size are skipped. Failures are logged
to corpus/raw/cfreds/_fetch_errors.log and the run continues.
"""
import re
import sys
import time
from pathlib import Path

import gdown

ROOT = Path(__file__).resolve().parent.parent
LISTING = ROOT / "corpus" / "cfreds_listing.txt"
OUT = ROOT / "corpus" / "raw" / "cfreds"

SKIP = re.compile(r"external|chipoff|chip_off|emmc|goggles|camera|android_physical|tablet|mmcblk0|ic_flash|flash_chip|media_parti", re.I)
IMAGE = re.compile(r"\.(001|e01|img|dd)$", re.I)


def wanted(path: str) -> bool:
    if not IMAGE.search(path):
        return True  # zips, READMEs, hashes, cfg
    return not SKIP.search(path)


def entries():
    ref = None
    for line in LISTING.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("=="):
            ref = line.split()[1]
            continue
        line = line.strip()
        if not line or ref is None:
            continue
        path, fid = line.rsplit(" ", 1)
        yield ref, path.replace("\\", "/"), fid


def main():
    only = set(sys.argv[1:])
    todo = [e for e in entries() if wanted(e[1]) and (not only or e[0] in only)]
    print(f"{len(todo)} files to fetch", flush=True)
    errlog = OUT / "_fetch_errors.log"
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (ref, path, fid) in enumerate(todo, 1):
        dest = OUT / ref / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            print(f"[{i}/{len(todo)}] skip {ref}/{path}", flush=True)
            continue
        print(f"[{i}/{len(todo)}] get  {ref}/{path}", flush=True)
        for attempt in range(3):
            try:
                if gdown.download(id=fid, output=str(dest), quiet=True, resume=True):
                    break
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            else:
                err = "gdown returned None"
            time.sleep(10 * (attempt + 1))
        else:
            with errlog.open("a", encoding="utf-8") as fh:
                fh.write(f"{ref}\t{path}\t{fid}\t{err}\n")
            print(f"    FAILED {err}", flush=True)
            continue
        print(f"    ok {dest.stat().st_size:,} bytes", flush=True)


if __name__ == "__main__":
    main()
