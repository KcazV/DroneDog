"""Build a small raster MBTiles map pack for one area (build time, online).

Only uses imagery whose terms allow offline use:
  swissimage : swisstopo SWISSIMAGE (Swiss federal open data, attribution "© swisstopo")
  usgs       : USGS National Map imagery (US public domain)

Usage:
  python tools/make_mbtiles.py <source> <lat> <lon> <radius_m> <out.mbtiles> [--name N] [--zmin 12] [--zmax 19]
The radius shrinks at the top zoom levels so the pack stays small.
"""
import argparse
import math
import sqlite3
import time
import urllib.request
from pathlib import Path

SOURCES = {
    "swissimage": {
        "url": "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg",
        "format": "jpg", "attribution": "Imagery © swisstopo (SWISSIMAGE, open government data)",
    },
    "usgs": {
        "url": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        "format": "jpg", "attribution": "Imagery: USGS The National Map (public domain)",
    },
}
UA = "DroneDog-mappack-builder/0.1 (offline forensic demo; small area)"


def tile_xy(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=SOURCES)
    ap.add_argument("lat", type=float)
    ap.add_argument("lon", type=float)
    ap.add_argument("radius_m", type=float)
    ap.add_argument("out")
    ap.add_argument("--name", default=None)
    ap.add_argument("--zmin", type=int, default=12)
    ap.add_argument("--zmax", type=int, default=19)
    a = ap.parse_args()
    src = SOURCES[a.source]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    db = sqlite3.connect(out)
    db.executescript("CREATE TABLE metadata(name TEXT, value TEXT);"
                     "CREATE TABLE tiles(zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);"
                     "CREATE UNIQUE INDEX tile_index ON tiles(zoom_level, tile_column, tile_row);")
    dlat = a.radius_m / 111320
    dlon = a.radius_m / (111320 * math.cos(math.radians(a.lat)))
    got = bytes_ = fails = 0
    for z in range(a.zmin, a.zmax + 1):
        shrink = 0.5 if z == a.zmax else 1.0  # top zoom: half radius
        x0, y0 = tile_xy(a.lat + dlat * shrink, a.lon - dlon * shrink, z)
        x1, y1 = tile_xy(a.lat - dlat * shrink, a.lon + dlon * shrink, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                url = src["url"].format(z=z, x=x, y=y)
                for attempt in range(3):
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": UA})
                        data = urllib.request.urlopen(req, timeout=60).read()
                        break
                    except Exception:  # noqa: BLE001
                        data = None
                        time.sleep(2 * (attempt + 1))
                if not data:
                    fails += 1
                    continue
                db.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", (z, x, (2 ** z - 1) - y, data))
                got += 1
                bytes_ += len(data)
        db.commit()
        print(f"z{z}: total {got} tiles, {bytes_ / 1e6:.1f} MB, {fails} failed", flush=True)
    meta = {"name": a.name or out.stem, "format": src["format"], "type": "baselayer", "version": "1",
            "description": f"{a.source} around {a.lat:.5f},{a.lon:.5f} r={a.radius_m:.0f} m",
            "attribution": src["attribution"], "minzoom": str(a.zmin), "maxzoom": str(a.zmax),
            "bounds": f"{a.lon - dlon:.6f},{a.lat - dlat:.6f},{a.lon + dlon:.6f},{a.lat + dlat:.6f}",
            "center": f"{a.lon:.6f},{a.lat:.6f},{a.zmax - 2}"}
    db.executemany("INSERT INTO metadata VALUES (?,?)", meta.items())
    db.commit()
    db.close()
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
