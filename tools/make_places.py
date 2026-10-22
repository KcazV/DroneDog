"""Build the offline reverse-geocoder data file (dronedog/data/places.npz) from GeoNames.

Build-time only (network allowed here; DroneDog itself never touches the network for this,
D4). Downloads from https://download.geonames.org/export/dump/ :

    cities500.zip         populated places with population >= 500 (or seats of admin units)
    admin1CodesASCII.txt  state / province names        (key CC.A1)
    admin2Codes.txt       county / district names       (key CC.A1.A2)
    countryInfo.txt       ISO country code -> country name

Licence: CC BY 4.0. Attribution: "Contains GeoNames data, CC BY 4.0, geonames.org".

Output (numpy .npz, compressed, allow_pickle=False safe; rows pre-sorted by 1-degree grid cell):
    lat_e5, lon_e5   int32   degrees * 1e5 (GeoNames publishes 5 decimals, so this is exact)
    pop              uint32  population
    a1, a2           int32   index into admin1 / admin2 tables, -1 = none
    cc               uint16  index into country table
    fc               uint8   index into feature-code table
    names            uint8   UTF-8 blob, one name per row, "\\n"-separated
    a1_keys/a1_names, a2_names, cc_codes/cc_names, fc_codes   string tables, same encoding
    meta             uint8   UTF-8 JSON (source, build date, counts, input SHA-256s)

Usage:
    .venv\\Scripts\\python.exe tools\\make_places.py               # cities500, default cache
    .venv\\Scripts\\python.exe tools\\make_places.py --set cities1000 --cache D:\\geonames
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://download.geonames.org/export/dump/"
AUX_FILES = ("admin1CodesASCII.txt", "admin2Codes.txt", "countryInfo.txt")
UA = "DroneDog-build/1.0 (offline reverse geocoder data build)"

# Feature codes that are not a current named place an examiner would report a location
# against: sections of populated places (neighbourhoods), historical, abandoned, destroyed.
DROP_FCODES = {"PPLX", "PPLH", "PPLQ", "PPLW", "PPLCH"}

ATTRIBUTION = "Contains GeoNames data, CC BY 4.0, geonames.org"


def _download(name: str, cache: Path, refresh: bool) -> Path:
    dest = cache / name
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        print(f"  cached  {dest}")
        return dest
    url = BASE_URL + name
    print(f"  fetch   {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    return dest


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _blob(strings) -> np.ndarray:
    for s in strings:
        if "\n" in s:
            raise ValueError(f"newline in string {s!r}")
    return np.frombuffer("\n".join(strings).encode("utf-8"), dtype=np.uint8)


def _read_lines(p: Path):
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line and not line.startswith("#"):
                yield line.split("\t")


def build(set_name: str, cache: Path, out: Path, refresh: bool) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    zip_name = f"{set_name}.zip"
    inputs = {n: _download(n, cache, refresh) for n in (zip_name, *AUX_FILES)}

    admin1 = {c[0]: c[1] for c in _read_lines(inputs["admin1CodesASCII.txt"]) if len(c) >= 2}
    admin2 = {c[0]: c[1] for c in _read_lines(inputs["admin2Codes.txt"]) if len(c) >= 2}
    countries = {c[0]: c[4] for c in _read_lines(inputs["countryInfo.txt"]) if len(c) >= 5}

    rows = []
    dropped = 0
    with zipfile.ZipFile(inputs[zip_name]) as z:
        with z.open(f"{set_name}.txt") as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                c = line.rstrip("\r\n").split("\t")
                if len(c) < 15 or c[6] != "P":
                    continue
                if c[7] in DROP_FCODES:
                    dropped += 1
                    continue
                rows.append((float(c[4]), float(c[5]), c[1].strip(), c[7], c[8], c[10], c[11],
                             int(c[14] or 0)))
    print(f"  {len(rows)} places kept, {dropped} dropped ({', '.join(sorted(DROP_FCODES))})")

    a1_keys: list[str] = []
    a1_ix: dict[str, int] = {}
    a2_names: list[str] = []
    a2_ix: dict[str, int] = {}
    cc_codes: list[str] = []
    cc_ix: dict[str, int] = {}
    fc_codes: list[str] = []
    fc_ix: dict[str, int] = {}

    n = len(rows)
    lat = np.empty(n, np.int32)
    lon = np.empty(n, np.int32)
    pop = np.empty(n, np.uint32)
    a1 = np.full(n, -1, np.int32)
    a2 = np.full(n, -1, np.int32)
    cc = np.empty(n, np.uint16)
    fc = np.empty(n, np.uint8)
    names = []
    for i, (la, lo, name, fcode, ccode, ad1, ad2, p) in enumerate(rows):
        lat[i] = round(la * 1e5)
        lon[i] = round(lo * 1e5)
        pop[i] = min(p, 0xFFFFFFFF)
        names.append(name)
        if ccode not in cc_ix:
            cc_ix[ccode] = len(cc_codes)
            cc_codes.append(ccode)
        cc[i] = cc_ix[ccode]
        if fcode not in fc_ix:
            fc_ix[fcode] = len(fc_codes)
            fc_codes.append(fcode)
        fc[i] = fc_ix[fcode]
        k1 = f"{ccode}.{ad1}"
        if ad1 and k1 in admin1:
            if k1 not in a1_ix:
                a1_ix[k1] = len(a1_keys)
                a1_keys.append(k1)
            a1[i] = a1_ix[k1]
        k2 = f"{ccode}.{ad1}.{ad2}"
        if ad1 and ad2 and k2 in admin2:
            if k2 not in a2_ix:
                a2_ix[k2] = len(a2_names)
                a2_names.append(admin2[k2])
            a2[i] = a2_ix[k2]
    if len(fc_codes) > 255 or len(cc_codes) > 65535:
        raise ValueError("table overflow")

    # Sort rows by 1-degree grid cell (then lon) so the runtime can slice cells directly.
    cell = ((np.floor(lat / 1e5).astype(np.int64) + 90) * 360
            + (np.floor(lon / 1e5).astype(np.int64) + 180))
    order = np.lexsort((lon, cell))
    names = [names[j] for j in order]

    date = _dt.date.today().isoformat()
    meta = {
        "format": "dronedog-places-1",
        "source": f"GeoNames {set_name}",
        "source_urls": [BASE_URL + n for n in inputs],
        "download_date": date,
        "licence": "CC BY 4.0",
        "attribution": ATTRIBUTION,
        "count": n,
        "dropped_fcodes": sorted(DROP_FCODES),
        "inputs_sha256": {k: _sha256(v) for k, v in inputs.items()},
    }
    np.savez_compressed(
        out,
        lat_e5=lat[order], lon_e5=lon[order], pop=pop[order], a1=a1[order], a2=a2[order],
        cc=cc[order], fc=fc[order],
        names=_blob(names),
        a1_keys=_blob(a1_keys), a1_names=_blob([admin1[k] for k in a1_keys]),
        a2_names=_blob(a2_names),
        cc_codes=_blob(cc_codes), cc_names=_blob([countries.get(c, c) for c in cc_codes]),
        fc_codes=_blob(fc_codes),
        meta=_blob([json.dumps(meta, ensure_ascii=False, separators=(",", ":"))]),
    )
    out_sha = _sha256(out)
    size = out.stat().st_size
    print(f"  wrote   {out}  ({size / 1e6:.2f} MB, sha256 {out_sha})")

    src = out.parent / "PLACES_SOURCE.txt"
    lines = [
        "DroneDog offline reverse-geocoder data",
        "=======================================",
        "",
        f"File:          {out.name}  ({size} bytes)",
        f"SHA-256:       {out_sha}",
        f"Built by:      tools/make_places.py",
        f"Source:        GeoNames geographical database, {set_name} dump",
        f"Download date: {date}",
        "Licence:       Creative Commons Attribution 4.0 International (CC BY 4.0)",
        "               https://creativecommons.org/licenses/by/4.0/",
        f"Attribution:   {ATTRIBUTION}",
        "",
        "Source URLs and SHA-256 of the files as downloaded:",
    ]
    lines += [f"  {BASE_URL + k}\n    {v}" for k, v in meta["inputs_sha256"].items()]
    lines += [
        "",
        f"Places kept:   {n} (feature class P; dropped feature codes: "
        f"{', '.join(sorted(DROP_FCODES))})",
        "Fields kept:   lat, lon, name, admin1 (state/province) name + code, admin2",
        "               (county/district) name, country name + ISO code, population,",
        "               feature code.",
        "",
        "Modifications: rows filtered and re-encoded as described above; names and",
        "               coordinates are unchanged from GeoNames.",
        "",
        "Meaning: a result is the nearest named populated place in GeoNames to a",
        "coordinate. It is not a postal address and not a statement of which",
        "jurisdiction's boundary contains the point.",
        "",
    ]
    src.write_text("\n".join(lines), encoding="utf-8")
    print(f"  wrote   {src}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--set", default="cities500", choices=["cities500", "cities1000"])
    ap.add_argument("--cache", type=Path, default=ROOT / "corpus" / "_downloads" / "geonames",
                    help="download cache folder (default: corpus/_downloads/geonames, gitignored)")
    ap.add_argument("--out", type=Path, default=ROOT / "dronedog" / "data" / "places.npz")
    ap.add_argument("--refresh", action="store_true", help="re-download even if cached")
    a = ap.parse_args()
    build(a.set, a.cache, a.out, a.refresh)


if __name__ == "__main__":
    main()
