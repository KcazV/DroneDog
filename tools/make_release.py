"""Assemble the class deliverables from a fresh PyInstaller build.

  release/DroneDog/                 BLANK portable app: no cases, no app data, no samples
  release/DroneDog-portable.zip     zip of the above (+ .sha256)
  release/DroneDog Demo Kit/        app + Demo Evidence + offline map packs + prebuilt
                                    fallback case + demo script

Usage:
  python tools/make_release.py [--no-build] [--no-kit] [--prebuilt-case <case dir>]

The blank folder is copied from dist/DroneDog with DroneDogData/ and Cases/ excluded,
then verified to contain neither, so test runs of the dist exe can never leak into it.
"""
import argparse
import hashlib
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "DroneDog"
REL = ROOT / "release"
EXCLUDE_TOP = {"DroneDogData", "Cases"}

# (source relative to ROOT, destination inside "Demo Evidence", provenance line)
EVIDENCE = [
    ("corpus/demo/DroneDog_DEMO_P4P_internalSD_CONSTRUCTED.dd", "1 - SD card image (constructed)/",
     "Constructed FAT16 SD image holding 6 real Phantom 4 Pro DATs (DataverseNO doi:10.18710/AFDYLS, CC0); "
     "FLY140 deliberately deleted. NOT a device acquisition."),
    ("corpus/demo/README_DEMO.txt", "1 - SD card image (constructed)/", "Build record + original SHA-256s."),
    ("corpus/raw/cfreds/DF040/2017_December/SDCard_Internal_3F/df040_microSD_Internal_3F.E01", "2 - NIST E01 (DJI Agras)/",
     "NIST CFReDS / VTO Labs Drone Forensics Program DF040, DJI Agras MG-1S internal microSD (E01)."),
    ("corpus/raw/cfreds/DF019/2017_August/mobile_iOS_backup/df019_flight_ios_backup.zip", "3 - iPhone backup (Mavic Pro)/",
     "NIST CFReDS / VTO Labs DF019, DJI Mavic Pro, iOS backup of the flight phone (DJI GO 4)."),
    ("corpus/raw/gh-drop", "4 - Phantom 3 aircraft + phone logs (DROP)/",
     "DROP (Clark et al., DFRWS 2017, MIT): Phantom 3 Standard aircraft FLY*.DAT + DJI GO TXT of the same flights."),
]
MAP_PACKS = ["corpus/demo/maps/EPFL_Lausanne_swissimage.mbtiles", "corpus/demo/maps/WestHaven_CT_USGS.mbtiles"]
SCRIPT_FILES = ["docs/DEMO_SCRIPT.html", "docs/DEMO_SCRIPT.md"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def rmtree(p: Path):
    def onerr(func, path, _exc):
        Path(path).chmod(stat.S_IWRITE)
        func(path)
    if p.exists():
        shutil.rmtree(p, onerror=onerr)


def copy_app(dst: Path):
    rmtree(dst)
    shutil.copytree(DIST, dst, ignore=lambda d, names: [n for n in names if Path(d) == DIST and n in EXCLUDE_TOP])
    leaked = [n for n in EXCLUDE_TOP if (dst / n).exists()]
    if leaked:
        raise SystemExit(f"blank app contains {leaked}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--no-kit", action="store_true")
    ap.add_argument("--prebuilt-case", type=Path)
    a = ap.parse_args()

    if not a.no_build:
        py = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
        subprocess.run([str(py), str(ROOT / "packaging" / "dronedog.spec"), "--noconfirm", "--clean",
                        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build")], check=True)
    if not (DIST / "DroneDog.exe").exists():
        raise SystemExit("dist/DroneDog/DroneDog.exe missing; build first")

    REL.mkdir(exist_ok=True)
    blank = REL / "DroneDog"
    copy_app(blank)
    (blank / "README.txt").write_text(
        "DroneDog - drone flight-log forensics (portable)\r\n\r\n"
        "TRAINING AND RESEARCH TOOL - not independently validated for casework. Verify every finding\r\n"
        "with an independent, validated tool before relying on it.\r\n"
        "PolyForm Noncommercial License (noncommercial use only); no warranty.\r\n\r\n"
        "Run DroneDog.exe. Nothing is installed. Cases are saved in the Cases folder next to\r\n"
        "DroneDog.exe unless you choose another location; app settings live in DroneDogData.\r\n"
        "Use an exFAT or NTFS drive if you will copy evidence files larger than 4 GB into a case.\r\n"
        "DroneDog works offline. Online maps are OFF until you switch them on (top bar).\r\n"
        "Requires Windows 10/11 with the Microsoft Edge WebView2 runtime (built into Windows 11).\r\n",
        encoding="utf-8")
    shutil.copy2(ROOT / "LICENSE", blank / "LICENSE.txt")
    shutil.copy2(ROOT / "docs" / "VALIDATION_STATUS.md", blank / "VALIDATION_STATUS.md")
    z = REL / "DroneDog-portable.zip"
    if z.exists():
        z.unlink()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(blank.rglob("*")):
            if f.is_file():
                zf.write(f, Path("DroneDog") / f.relative_to(blank))
    zsha = sha256(z)
    (REL / "DroneDog-portable.zip.sha256").write_text(f"{zsha}  {z.name}\n", encoding="utf-8")
    print(f"blank app: {blank}  zip {z.stat().st_size / 1e6:.1f} MB sha256 {zsha}")

    if a.no_kit:
        return
    kit = REL / "DroneDog Demo Kit"
    rmtree(kit)
    copy_app(kit / "DroneDog")
    ev = kit / "Demo Evidence"
    lines = ["DroneDog demo evidence - sources and licences", ""]
    for src, sub, prov in EVIDENCE:
        s = ROOT / src
        if not s.exists():
            print(f"WARNING: missing {src}; skipped")
            continue
        d = ev / sub
        d.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d / s.name, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d / s.name)
        lines.append(f"{sub.rstrip('/')}: {prov}")
    (ev / "SOURCES.txt").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    maps = kit / "Map packs (offline)"
    for m in MAP_PACKS:
        if (ROOT / m).exists():
            maps.mkdir(exist_ok=True)
            shutil.copy2(ROOT / m, maps / Path(m).name)
    for sfile in SCRIPT_FILES:
        if (ROOT / sfile).exists():
            shutil.copy2(ROOT / sfile, kit / Path(sfile).name)
    if a.prebuilt_case:
        dst = kit / "DroneDog" / "Cases" / a.prebuilt_case.name
        shutil.copytree(a.prebuilt_case, dst)
        print(f"prebuilt case -> {dst}")
    total = sum(f.stat().st_size for f in kit.rglob("*") if f.is_file())
    print(f"demo kit: {kit}  {total / 1e9:.2f} GB")


if __name__ == "__main__":
    main()
