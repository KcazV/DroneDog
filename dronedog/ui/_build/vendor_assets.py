"""Build-time vendoring of DroneDog UI assets.  NEVER imported at runtime.

The shipped app is fully air-gapped; every JS/CSS/font/map file it loads lives
under ``dronedog/ui/web/vendor``.  This script is how those files got there,
so the provenance is reproducible:

    python -I dronedog/ui/_build/vendor_assets.py fetch     <raw_dir>   # curl downloads
    python -I dronedog/ui/_build/vendor_assets.py naturalearth <raw_dir> # simplify NE -> vendor
    python -I dronedog/ui/_build/vendor_assets.py icons                  # lucide SVG -> icons.js
    python -I dronedog/ui/_build/vendor_assets.py manifest               # VENDOR.md (sha256)

``raw_dir`` must be an empty scratch directory outside the repo; downloads are
treated as untrusted data and only ever parsed as JSON / copied as bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parent / "web"
VENDOR = WEB / "vendor"

LEAFLET_V = "1.9.4"
UPLOT_V = "1.6.32"
LUCIDE_V = "1.50.0"
FONT_V = "5.3.0"
NE_TAG = "v5.1.2"

# name, version, license, [(url, dest relative to vendor/)]
PACKAGES: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    ("Leaflet", LEAFLET_V, "BSD-2-Clause", [
        (f"https://unpkg.com/leaflet@{LEAFLET_V}/dist/leaflet.js", f"leaflet/{LEAFLET_V}/leaflet.js"),
        (f"https://unpkg.com/leaflet@{LEAFLET_V}/dist/leaflet.css", f"leaflet/{LEAFLET_V}/leaflet.css"),
        (f"https://unpkg.com/leaflet@{LEAFLET_V}/dist/images/layers.png", f"leaflet/{LEAFLET_V}/images/layers.png"),
        (f"https://unpkg.com/leaflet@{LEAFLET_V}/dist/images/layers-2x.png", f"leaflet/{LEAFLET_V}/images/layers-2x.png"),
        (f"https://unpkg.com/leaflet@{LEAFLET_V}/dist/images/marker-icon.png", f"leaflet/{LEAFLET_V}/images/marker-icon.png"),
        (f"https://unpkg.com/leaflet@{LEAFLET_V}/dist/images/marker-icon-2x.png", f"leaflet/{LEAFLET_V}/images/marker-icon-2x.png"),
        (f"https://unpkg.com/leaflet@{LEAFLET_V}/dist/images/marker-shadow.png", f"leaflet/{LEAFLET_V}/images/marker-shadow.png"),
        (f"https://unpkg.com/leaflet@{LEAFLET_V}/LICENSE", f"leaflet/{LEAFLET_V}/LICENSE"),
    ]),
    ("uPlot", UPLOT_V, "MIT", [
        (f"https://unpkg.com/uplot@{UPLOT_V}/dist/uPlot.esm.js", f"uplot/{UPLOT_V}/uPlot.esm.js"),
        (f"https://unpkg.com/uplot@{UPLOT_V}/dist/uPlot.min.css", f"uplot/{UPLOT_V}/uPlot.min.css"),
        (f"https://unpkg.com/uplot@{UPLOT_V}/LICENSE", f"uplot/{UPLOT_V}/LICENSE"),
    ]),
    ("Inter (variable, Fontsource)", FONT_V, "OFL-1.1", [
        (f"https://cdn.jsdelivr.net/npm/@fontsource-variable/inter@{FONT_V}/files/inter-latin-wght-normal.woff2", "fonts/inter-latin-wght-normal.woff2"),
        (f"https://cdn.jsdelivr.net/npm/@fontsource-variable/inter@{FONT_V}/files/inter-latin-ext-wght-normal.woff2", "fonts/inter-latin-ext-wght-normal.woff2"),
        (f"https://cdn.jsdelivr.net/npm/@fontsource-variable/inter@{FONT_V}/LICENSE", "fonts/LICENSE-Inter.txt"),
    ]),
    ("JetBrains Mono (variable, Fontsource)", FONT_V, "OFL-1.1", [
        (f"https://cdn.jsdelivr.net/npm/@fontsource-variable/jetbrains-mono@{FONT_V}/files/jetbrains-mono-latin-wght-normal.woff2", "fonts/jetbrains-mono-latin-wght-normal.woff2"),
        (f"https://cdn.jsdelivr.net/npm/@fontsource-variable/jetbrains-mono@{FONT_V}/files/jetbrains-mono-latin-ext-wght-normal.woff2", "fonts/jetbrains-mono-latin-ext-wght-normal.woff2"),
        (f"https://cdn.jsdelivr.net/npm/@fontsource-variable/jetbrains-mono@{FONT_V}/LICENSE", "fonts/LICENSE-JetBrainsMono.txt"),
    ]),
]

LUCIDE_ICONS = [
    "folder-open", "folder-plus", "file-plus", "hard-drive", "upload", "database", "files",
    "file-search", "plane", "map", "map-pin", "file-text", "shield-check", "settings", "sun",
    "moon", "play", "pause", "skip-back", "skip-forward", "chevron-left", "chevron-right",
    "chevron-down", "chevron-up", "copy", "check", "x", "triangle-alert", "circle-alert", "info",
    "lock", "house", "layers", "clock", "search", "funnel", "download", "external-link",
    "refresh-cw", "list", "activity", "battery", "gauge", "satellite", "gamepad-2", "navigation",
    "crosshair", "link", "fingerprint", "scale", "folder", "file", "file-x", "circle-check",
    "circle-x", "loader-circle", "plus", "arrow-up-down", "arrow-up", "arrow-down", "keyboard",
    "plane-takeoff", "plane-landing", "timer", "rotate-ccw", "trash-2", "eye", "square-check",
    "square", "flag", "shield-alert", "disc", "maximize-2", "minimize-2", "rewind", "fast-forward",
    "scan-search", "history", "package", "chart-line",
]

# Natural Earth (public domain).  key -> source file in geojson/
NE_FILES = {
    "countries_50m": "ne_50m_admin_0_countries_lakes.geojson",
    "countries_10m": "ne_10m_admin_0_countries_lakes.geojson",
    "borders_10m": "ne_10m_admin_0_boundary_lines_land.geojson",
    "admin1_lines_10m": "ne_10m_admin_1_states_provinces_lines.geojson",
    "admin1_10m": "ne_10m_admin_1_states_provinces.geojson",
    "places_10m": "ne_10m_populated_places_simple.geojson",
    "lakes_10m": "ne_10m_lakes.geojson",
    "rivers_10m": "ne_10m_rivers_lake_centerlines.geojson",
    "roads_10m": "ne_10m_roads.geojson",
}
NE_BASE = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NE_TAG}/geojson/"


def curl(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print("curl", url)
    subprocess.run(["curl.exe" if os.name == "nt" else "curl", "-fsSL", "--retry", "3",
                    "-o", str(dest), url], check=True)


def cmd_fetch(raw_dir: Path) -> None:
    for _name, _ver, _lic, files in PACKAGES:
        for url, rel in files:
            curl(url, VENDOR / rel)
    for icon in LUCIDE_ICONS:
        curl(f"https://unpkg.com/lucide-static@{LUCIDE_V}/icons/{icon}.svg",
             VENDOR / "lucide" / "icons" / f"{icon}.svg")
    curl(f"https://unpkg.com/lucide-static@{LUCIDE_V}/LICENSE", VENDOR / "lucide" / "LICENSE")
    raw_dir.mkdir(parents=True, exist_ok=True)
    for _key, fname in NE_FILES.items():
        if not (raw_dir / fname).exists():
            curl(NE_BASE + fname, raw_dir / fname)


# ---------------------------------------------------------------- geometry --

def _dp(points: list, tol: float) -> list:
    """Iterative Douglas-Peucker on [[x, y], ...] (degrees)."""
    n = len(points)
    if n < 3 or tol <= 0:
        return points
    keep = bytearray(n)
    keep[0] = keep[n - 1] = 1
    stack = [(0, n - 1)]
    tol2 = tol * tol
    while stack:
        a, b = stack.pop()
        ax, ay = points[a][0], points[a][1]
        bx, by = points[b][0], points[b][1]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = points[i][0], points[i][1]
            if l2 == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / l2
                t = 0 if t < 0 else (1 if t > 1 else t)
                qx, qy = ax + t * dx, ay + t * dy
                d = (px - qx) ** 2 + (py - qy) ** 2
            if d > best:
                best, bi = d, i
        if best > tol2 and bi > 0:
            keep[bi] = 1
            stack.append((a, bi))
            stack.append((bi, b))
    return [p for p, k in zip(points, keep) if k]


def _round_line(points: list, nd: int) -> list:
    out = []
    last = None
    for p in points:
        q = [round(p[0], nd), round(p[1], nd)]
        if q != last:
            out.append(q)
            last = q
    return out


def _simplify_line(points, tol, nd, min_pts=2):
    pts = _round_line(_dp(points, tol), nd)
    return pts if len(pts) >= min_pts else None


def _simplify_ring(ring, tol, nd, min_area):
    pts = _simplify_line(ring, tol, nd, min_pts=4)
    if not pts:
        return None
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    if len(pts) < 4:
        return None
    area = 0.0
    for i in range(len(pts) - 1):
        area += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    if abs(area) / 2 < min_area:
        return None
    return pts


def simplify_geometry(geom, tol, nd, min_area=0.0):
    if not geom:
        return None
    t = geom["type"]
    c = geom["coordinates"]
    if t == "LineString":
        s = _simplify_line(c, tol, nd)
        return {"type": "LineString", "coordinates": s} if s else None
    if t == "MultiLineString":
        lines = [s for s in (_simplify_line(l, tol, nd) for l in c) if s]
        return {"type": "MultiLineString", "coordinates": lines} if lines else None
    if t in ("Polygon", "MultiPolygon"):
        polys = [c] if t == "Polygon" else c
        out = []
        for poly in polys:
            outer = _simplify_ring(poly[0], tol, nd, min_area)
            if not outer:
                continue
            holes = [h for h in (_simplify_ring(r, tol, nd, min_area) for r in poly[1:]) if h]
            out.append([outer] + holes)
        if not out:
            return None
        return {"type": "MultiPolygon", "coordinates": out}
    if t == "Point":
        return {"type": "Point", "coordinates": [round(c[0], nd), round(c[1], nd)]}
    return None


def _load(raw_dir: Path, key: str) -> dict:
    with open(raw_dir / NE_FILES[key], encoding="utf-8") as f:
        return json.load(f)


def _merge(features: list) -> list:
    """Merge features with identical properties into one Multi* feature (less JSON overhead)."""
    groups: dict = {}
    for f in features:
        key = json.dumps(f["properties"], sort_keys=True)
        g = f["geometry"]
        multi = g["type"] if g["type"].startswith("Multi") else "Multi" + g["type"]
        parts = g["coordinates"] if g["type"].startswith("Multi") else [g["coordinates"]]
        grp = groups.setdefault((key, multi), {"properties": f["properties"], "parts": []})
        grp["parts"].extend(parts)
    return [{"type": "Feature", "properties": v["properties"],
             "geometry": {"type": multi, "coordinates": v["parts"]}}
            for (key, multi), v in groups.items()]


def _write(name: str, features: list, merge: bool = False) -> None:
    if merge:
        features = _merge(features)
    out = VENDOR / "naturalearth" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    fc = {"type": "FeatureCollection",
          "source": f"Natural Earth {NE_TAG} (public domain), simplified for DroneDog",
          "features": features}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(fc, f, separators=(",", ":"), ensure_ascii=False)
    print(f"{name}: {len(features)} features, {out.stat().st_size / 1e6:.2f} MB")


def _p(props: dict, *keys, default=None):
    for k in keys:
        for kk in (k, k.upper(), k.lower()):
            if kk in props and props[kk] not in (None, ""):
                return props[kk]
    return default


def cmd_naturalearth(raw_dir: Path) -> None:
    # Land / countries, two levels of detail.
    for key, name, tol, nd, area in (
        ("countries_50m", "ne_countries_low.geojson", 0.04, 2, 0.01),
        ("countries_10m", "ne_countries.geojson", 0.009, 3, 0.0003),
    ):
        feats = []
        for f in _load(raw_dir, key)["features"]:
            g = simplify_geometry(f["geometry"], tol, nd, area)
            if g:
                feats.append({"type": "Feature", "properties": {"n": _p(f["properties"], "NAME", "ADMIN")},
                              "geometry": g})
        _write(name, feats)

    feats = []
    for f in _load(raw_dir, "borders_10m")["features"]:
        g = simplify_geometry(f["geometry"], 0.006, 3)
        if g:
            feats.append({"type": "Feature", "properties": {}, "geometry": g})
    _write("ne_borders.geojson", feats, merge=True)

    feats = []
    for f in _load(raw_dir, "admin1_lines_10m")["features"]:
        g = simplify_geometry(f["geometry"], 0.008, 3)
        if g:
            feats.append({"type": "Feature", "properties": {}, "geometry": g})
    _write("ne_admin1_lines.geojson", feats, merge=True)

    feats = []
    for f in _load(raw_dir, "lakes_10m")["features"]:
        pr = f["properties"]
        g = simplify_geometry(f["geometry"], 0.006, 3, 0.0001)
        if g:
            feats.append({"type": "Feature", "properties": {"r": _p(pr, "scalerank", default=10)},
                          "geometry": g})
    _write("ne_lakes.geojson", feats, merge=True)

    feats = []
    for f in _load(raw_dir, "rivers_10m")["features"]:
        pr = f["properties"]
        if _p(pr, "scalerank", default=10) > 8:
            continue
        g = simplify_geometry(f["geometry"], 0.008, 3)
        if g:
            feats.append({"type": "Feature", "properties": {"r": _p(pr, "scalerank", default=10)},
                          "geometry": g})
    _write("ne_rivers.geojson", feats, merge=True)

    feats = []
    for f in _load(raw_dir, "roads_10m")["features"]:
        pr = f["properties"]
        typ = str(_p(pr, "type", default=""))
        rank = _p(pr, "scalerank", default=10)
        # Keep named road classes; drop ferries/tracks and low-rank "Unknown" segments.
        if "Ferry" in typ or typ == "Track" or typ == "Unknown":
            continue
        g = simplify_geometry(f["geometry"], 0.009, 3)
        if g:
            major = 1 if typ in ("Major Highway", "Beltway") else 0
            feats.append({"type": "Feature",
                          "properties": {"r": _p(pr, "scalerank", default=10), "m": major},
                          "geometry": g})
    _write("ne_roads.geojson", feats, merge=True)

    # Labels: countries, admin-1, populated places.  {n, k, z}: z = min zoom to show.
    labels = []
    for f in _load(raw_dir, "countries_10m")["features"]:
        pr = f["properties"]
        x, y = _p(pr, "LABEL_X"), _p(pr, "LABEL_Y")
        if x is None or y is None:
            continue
        labels.append({"type": "Feature",
                       "properties": {"n": _p(pr, "NAME"), "k": "c",
                                      "z": float(_p(pr, "MIN_LABEL", default=3))},
                       "geometry": {"type": "Point", "coordinates": [round(x, 3), round(y, 3)]}})
    for f in _load(raw_dir, "admin1_10m")["features"]:
        pr = f["properties"]
        x, y = _p(pr, "longitude"), _p(pr, "latitude")
        if x is None or y is None or not _p(pr, "name"):
            continue
        labels.append({"type": "Feature",
                       "properties": {"n": _p(pr, "name"), "k": "s",
                                      "z": float(_p(pr, "min_label", default=6))},
                       "geometry": {"type": "Point", "coordinates": [round(x, 3), round(y, 3)]}})
    for f in _load(raw_dir, "places_10m")["features"]:
        pr = f["properties"]
        g = f["geometry"]["coordinates"]
        labels.append({"type": "Feature",
                       "properties": {"n": _p(pr, "name"), "k": "p",
                                      "z": float(_p(pr, "min_zoom", default=7)),
                                      "cap": 1 if _p(pr, "adm0cap", default=0) else 0},
                       "geometry": {"type": "Point", "coordinates": [round(g[0], 3), round(g[1], 3)]}})
    _write("ne_labels.geojson", labels)


# ------------------------------------------------------------------- icons --

def cmd_icons() -> None:
    import re
    icons = {}
    for svg in sorted((VENDOR / "lucide" / "icons").glob("*.svg")):
        txt = svg.read_text(encoding="utf-8")
        txt = re.sub(r"<!--.*?-->", "", txt, flags=re.S)
        inner = re.search(r"<svg[^>]*>(.*)</svg>", txt, flags=re.S).group(1)
        inner = re.sub(r"\s+", " ", inner).strip()
        icons[svg.stem] = inner
    out = VENDOR / "lucide" / "icons.js"
    body = ",\n".join(f"  {json.dumps(k)}: {json.dumps(v)}" for k, v in icons.items())
    out.write_text(
        f"// Generated by dronedog/ui/_build/vendor_assets.py from lucide-static@{LUCIDE_V} (ISC, see LICENSE).\n"
        f"// Inner SVG markup only; wrap with a 24x24 stroke-based <svg>.\n"
        f"export const ICONS = {{\n{body}\n}};\n",
        encoding="utf-8")
    print(f"icons.js: {len(icons)} icons")


# ---------------------------------------------------------------- manifest --

def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_manifest() -> None:
    url_of = {}
    lic_of = {}
    for name, ver, lic, files in PACKAGES:
        for url, rel in files:
            url_of[rel] = url
            lic_of[rel] = (name, ver, lic)
    for icon in LUCIDE_ICONS:
        rel = f"lucide/icons/{icon}.svg"
        url_of[rel] = f"https://unpkg.com/lucide-static@{LUCIDE_V}/icons/{icon}.svg"
        lic_of[rel] = ("Lucide icons (lucide-static)", LUCIDE_V, "ISC")
    url_of["lucide/LICENSE"] = f"https://unpkg.com/lucide-static@{LUCIDE_V}/LICENSE"
    lic_of["lucide/LICENSE"] = ("Lucide icons (lucide-static)", LUCIDE_V, "ISC")
    lic_of["lucide/icons.js"] = ("Lucide icons (generated module)", LUCIDE_V, "ISC")
    url_of["lucide/icons.js"] = "generated from lucide/icons/*.svg by vendor_assets.py icons"

    rows = []
    total = 0
    for p in sorted(VENDOR.rglob("*")):
        if not p.is_file() or p.name == "VENDOR.md" or p.name.startswith("."):
            continue
        rel = p.relative_to(VENDOR).as_posix()
        size = p.stat().st_size
        total += size
        if rel.startswith("naturalearth/"):
            name, ver, lic = "Natural Earth", NE_TAG, "Public domain"
            url = "derived (simplified) from " + NE_BASE + "{" + ",".join(sorted(set(NE_FILES.values()))) + "}"
            if rel.endswith("README.txt"):
                url = "written by vendor_assets.py"
        else:
            name, ver, lic = lic_of.get(rel, ("?", "?", "?"))
            url = url_of.get(rel, "?")
        rows.append((rel, name, ver, lic, size, _sha256(p), url))

    lines = [
        "# Vendored UI assets",
        "",
        "Everything the DroneDog UI loads at runtime is in this folder. There are no CDN or",
        "network references in shipped files. Regenerate with `dronedog/ui/_build/vendor_assets.py`",
        "(build machine only, online). Hashes are SHA-256 of the file as committed.",
        "",
        f"Total: {len(rows)} files, {total / 1e6:.2f} MB.",
        "",
        "## Packages",
        "",
        "| Package | Version | License | Source |",
        "|---|---|---|---|",
        f"| Leaflet | {LEAFLET_V} | BSD-2-Clause | https://unpkg.com/leaflet@{LEAFLET_V}/dist/ |",
        f"| uPlot | {UPLOT_V} | MIT | https://unpkg.com/uplot@{UPLOT_V}/dist/ |",
        f"| Lucide icons (lucide-static) | {LUCIDE_V} | ISC | https://unpkg.com/lucide-static@{LUCIDE_V}/icons/ |",
        f"| Inter variable (Fontsource) | {FONT_V} | OFL-1.1 | https://cdn.jsdelivr.net/npm/@fontsource-variable/inter@{FONT_V}/files/ |",
        f"| JetBrains Mono variable (Fontsource) | {FONT_V} | OFL-1.1 | https://cdn.jsdelivr.net/npm/@fontsource-variable/jetbrains-mono@{FONT_V}/files/ |",
        f"| Natural Earth vector | {NE_TAG} | Public domain | {NE_BASE} (simplified: Douglas-Peucker + coordinate rounding, attributes stripped) |",
        "",
        "## Files",
        "",
        "| File | Package | Version | License | Size (bytes) | SHA-256 | URL |",
        "|---|---|---|---|---:|---|---|",
    ]
    for rel, name, ver, lic, size, sha, url in rows:
        lines.append(f"| `{rel}` | {name} | {ver} | {lic} | {size} | `{sha}` | {url} |")
    (VENDOR / "VENDOR.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"VENDOR.md: {len(rows)} files, {total / 1e6:.2f} MB")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "fetch":
        cmd_fetch(Path(argv[1]))
    elif cmd == "naturalearth":
        cmd_naturalearth(Path(argv[1]))
    elif cmd == "icons":
        cmd_icons()
    elif cmd == "manifest":
        cmd_manifest()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
