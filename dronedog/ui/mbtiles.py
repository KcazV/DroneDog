"""Offline raster map packs (MBTiles 1.x) for the map views.

Map packs are built on a connected machine (street / satellite raster tiles) and
copied to the air-gapped lab machine.  ``import_pack`` copies one into the local
basemap folder; ``tile`` serves a tile to the UI as base64 through the bridge, so
no HTTP tile server and no network access are involved.

Coordinates: the UI (Leaflet) asks for XYZ / "slippy map" tiles.  MBTiles stores
rows in TMS order (origin bottom-left), so ``tile_row = 2**z - 1 - y``.

Map packs are reference material, not evidence: they are never written into a case
folder and never enter the custody log.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import sqlite3
import threading
from pathlib import Path

MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


class MapPackError(Exception):
    pass


def default_basemap_dir() -> Path:
    base = os.environ.get("DRONEDOG_BASEMAPS")
    if base:
        return Path(base)
    from dronedog.paths import data_dir
    return data_dir() / "basemaps"


def xyz_to_tms_row(z: int, y: int) -> int:
    return (1 << z) - 1 - y


def _sniff_format(blob: bytes) -> str | None:
    if blob.startswith(b"\x89PNG"):
        return "png"
    if blob.startswith(b"\xff\xd8"):
        return "jpg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp"
    return None


def _read_meta(con: sqlite3.Connection) -> dict:
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    if "tiles" not in tables or "metadata" not in tables:
        raise MapPackError("not an MBTiles file (missing 'tiles' or 'metadata' table)")
    meta = {k: v for k, v in con.execute("SELECT name, value FROM metadata")}
    fmt = (meta.get("format") or "").lower()
    if fmt in ("pbf", "mvt"):
        raise MapPackError("vector MBTiles (pbf) are not supported; build a raster (png/jpg) pack")
    if fmt not in MIME:
        row = con.execute("SELECT tile_data FROM tiles LIMIT 1").fetchone()
        fmt = _sniff_format(row[0]) if row and row[0] else None
        if not fmt:
            raise MapPackError("unrecognised tile format")
    zr = con.execute("SELECT MIN(zoom_level), MAX(zoom_level), COUNT(*) FROM tiles").fetchone()
    bounds = None
    if meta.get("bounds"):
        try:
            bounds = [float(v) for v in meta["bounds"].split(",")]
        except ValueError:
            bounds = None
    return {
        "name": meta.get("name"),
        "format": fmt,
        "mime": MIME[fmt],
        # zoom range of the tiles actually present wins over metadata (packs often over-claim);
        # the map overzooms past maxzoom and hides the layer below minzoom
        "minzoom": int(zr[0]) if zr[0] is not None else int(meta.get("minzoom", 0)),
        "maxzoom": int(zr[1]) if zr[1] is not None else int(meta.get("maxzoom", 0)),
        "meta_minzoom": meta.get("minzoom"), "meta_maxzoom": meta.get("maxzoom"),
        "tile_count": zr[2],
        "bounds": bounds,
        "attribution": meta.get("attribution"),
        "description": meta.get("description"),
        "type": meta.get("type", "baselayer"),
    }


class MBTilesStore:
    def __init__(self, folder: str | os.PathLike | None = None):
        self.folder = Path(folder) if folder else default_basemap_dir()
        self._lock = threading.Lock()
        self._cons: dict[str, sqlite3.Connection] = {}
        self._meta: dict[str, dict] = {}

    def _paths(self) -> dict[str, Path]:
        if not self.folder.is_dir():
            return {}
        out = {}
        for p in sorted(self.folder.glob("*.mbtiles")):
            out[re.sub(r"[^A-Za-z0-9_.-]", "_", p.stem)] = p
        return out

    def _con(self, basemap_id: str) -> sqlite3.Connection:
        con = self._cons.get(basemap_id)
        if con is None:
            p = self._paths().get(basemap_id)
            if p is None:
                raise MapPackError(f"unknown basemap {basemap_id!r}")
            con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro&immutable=1", uri=True,
                                  check_same_thread=False)
            self._cons[basemap_id] = con
        return con

    def list(self) -> list[dict]:
        out = []
        with self._lock:
            for bid, p in self._paths().items():
                try:
                    if bid not in self._meta:
                        self._meta[bid] = _read_meta(self._con(bid))
                    m = dict(self._meta[bid])
                    m.update({"id": bid, "path": str(p), "size": p.stat().st_size, "kind": "raster",
                              "name": m.get("name") or p.stem, "ok": True})
                except (MapPackError, sqlite3.Error) as e:
                    m = {"id": bid, "path": str(p), "name": p.stem, "ok": False, "error": str(e)}
                out.append(m)
        return out

    def tile(self, basemap_id: str, z: int, x: int, y: int) -> dict | None:
        """XYZ tile -> {"mime", "data": base64} or None when the pack has no such tile."""
        z, x, y = int(z), int(x), int(y)
        if z < 0 or z > 30 or not (0 <= x < (1 << z)) or not (0 <= y < (1 << z)):
            return None
        with self._lock:
            con = self._con(basemap_id)
            if basemap_id not in self._meta:
                self._meta[basemap_id] = _read_meta(con)
            mime = self._meta[basemap_id]["mime"]
            row = con.execute(
                "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                (z, x, xyz_to_tms_row(z, y))).fetchone()
        if not row or not row[0]:
            return None
        blob = bytes(row[0])
        fmt = _sniff_format(blob)
        return {"mime": MIME[fmt] if fmt else mime, "data": base64.b64encode(blob).decode("ascii")}

    def import_pack(self, src: str | os.PathLike) -> dict:
        src = Path(src)
        if not src.is_file():
            raise MapPackError(f"file not found: {src}")
        con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
        try:
            meta = _read_meta(con)
        except sqlite3.DatabaseError as e:
            raise MapPackError(f"not a SQLite/MBTiles file: {e}") from e
        finally:
            con.close()
        self.folder.mkdir(parents=True, exist_ok=True)
        dest = self.folder / src.name
        n = 1
        while dest.exists():
            dest = self.folder / f"{src.stem}_{n}{src.suffix}"
            n += 1
        shutil.copy2(src, dest)
        bid = re.sub(r"[^A-Za-z0-9_.-]", "_", dest.stem)
        meta.update({"id": bid, "path": str(dest), "kind": "raster", "ok": True,
                     "name": meta.get("name") or dest.stem, "size": dest.stat().st_size})
        return meta

    def close(self) -> None:
        with self._lock:
            for c in self._cons.values():
                c.close()
            self._cons.clear()
