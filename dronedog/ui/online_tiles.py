"""Online basemap tiles (D11), fetched by Python through the NetMonitor choke point only.

Sources (only when the examiner turns online maps on):
  esri_imagery  Esri World Imagery                    attribution required; Esri terms of use
                (non-commercial / evaluation use here, see DECISIONS D12), worldwide, z<=19
  usgs_imagery  USGS The National Map, Imagery Only   public domain, US coverage
  usgs_topo     USGS The National Map, Topo           public domain, US coverage
  osm           OpenStreetMap standard tiles          (c) OpenStreetMap contributors, ODbL;
                used per the OSM tile usage policy: identified User-Agent, attribution shown,
                interactive viewing only (no prefetch / bulk), HTTP cache headers honoured.

Tiles are cached on disk under <data dir>/tilecache/<source>/<z>/<x>/<y>.tile (+ .json), where
<data dir> is dronedog.paths.data_dir() (beside the exe in portable mode)
with a JSON sidecar holding the validators (ETag / Last-Modified) and the expiry computed
from Cache-Control max-age / Expires. Fresh tiles are served from disk; stale ones are
revalidated with a conditional GET (304 keeps the cached bytes).
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from pathlib import Path

from dronedog.ui.netmon import NetMonitor, OfflineError

SOURCES = {
    "esri_imagery": {
        "name": "Satellite HD (Esri)", "kind": "satellite",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
        "minzoom": 0, "maxzoom": 19, "bounds": None, "coverage": "World (sub-metre in most of the US)",
    },
    "usgs_imagery": {
        "name": "Satellite (USGS)", "kind": "satellite",
        "url": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Imagery: USGS The National Map (public domain)", "minzoom": 0, "maxzoom": 16,
        "bounds": [-180, 15, -60, 72], "coverage": "United States",
    },
    "usgs_topo": {
        "name": "Topo (USGS)", "kind": "topo",
        "url": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
        "attribution": "USGS The National Map (public domain)", "minzoom": 0, "maxzoom": 16,
        "bounds": [-180, 15, -60, 72], "coverage": "United States",
    },
    "osm": {
        "name": "Street (OpenStreetMap)", "kind": "street",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenStreetMap contributors", "minzoom": 0, "maxzoom": 19,
        "bounds": None, "coverage": "World",
    },
}
PREFIX = "online:"
DEFAULT_MAX_AGE = 24 * 3600
MIN_MAX_AGE = 3600
MISSING_TTL = 7 * 24 * 3600


def default_cache_dir() -> Path:
    from dronedog.paths import data_dir
    return data_dir() / "tilecache"


def _max_age(headers: dict) -> int:
    cc = headers.get("cache-control", "")
    m = re.search(r"max-age=(\d+)", cc)
    if "no-store" in cc or "no-cache" in cc:
        return 0
    if m:
        return int(m.group(1))
    if headers.get("expires"):
        try:
            return max(0, int(parsedate_to_datetime(headers["expires"]).timestamp() - time.time()))
        except (TypeError, ValueError):
            pass
    return DEFAULT_MAX_AGE


class OnlineTiles:
    def __init__(self, monitor: NetMonitor, cache_dir: str | os.PathLike | None = None, workers: int = 4,
                 timeout: float = 8.0):
        self.mon = monitor
        self.cache = Path(cache_dir) if cache_dir else default_cache_dir()
        self.timeout = timeout
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dronedog-tiles")
        self._inflight: dict[str, object] = {}
        self._lock = threading.Lock()

    def list(self) -> list[dict]:
        on = self.mon.online
        return [{"id": PREFIX + k, "name": v["name"], "kind": "online", "online": True, "ok": on, "available": on,
                 "format": "png" if k == "osm" else "jpg", "minzoom": v["minzoom"], "maxzoom": v["maxzoom"],
                 "attribution": v["attribution"], "bounds": v["bounds"], "coverage": v["coverage"],
                 "error": None if on else "Online maps are off"} for k, v in SOURCES.items()]

    def _paths(self, sid: str, z: int, x: int, y: int) -> tuple[Path, Path]:
        d = self.cache / sid / str(z) / str(x)
        return d / f"{y}.tile", d / f"{y}.json"

    def tile(self, basemap_id: str, z: int, x: int, y: int) -> dict | None:
        sid = basemap_id[len(PREFIX):] if basemap_id.startswith(PREFIX) else basemap_id
        src = SOURCES.get(sid)
        if src is None:
            raise KeyError(f"unknown online source {basemap_id!r}")
        z, x, y = int(z), int(x), int(y)
        if z < src["minzoom"] or z > src["maxzoom"] or not (0 <= x < (1 << z)) or not (0 <= y < (1 << z)):
            return None
        if not self.mon.online:
            return None                       # offline: never touch the network, never show online tiles
        key = f"{sid}/{z}/{x}/{y}"
        with self._lock:
            fut = self._inflight.get(key)
            if fut is None:
                fut = self._pool.submit(self._fetch, sid, src, z, x, y)
                self._inflight[key] = fut
        try:
            return fut.result(timeout=self.timeout + 4)
        except Exception:  # noqa: BLE001 - a missing tile must not break the map
            return None
        finally:
            with self._lock:
                if self._inflight.get(key) is fut and fut.done():
                    self._inflight.pop(key, None)

    def _fetch(self, sid: str, src: dict, z: int, x: int, y: int) -> dict | None:
        data_p, meta_p = self._paths(sid, z, x, y)
        meta = None
        if meta_p.exists():
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = None
            if meta and not meta.get("missing") and not data_p.exists():
                meta = None                   # sidecar without its tile: refetch
        if meta and meta.get("expires_at", 0) > time.time():
            return None if meta.get("missing") else self._out(data_p, meta)
        headers = {}
        if meta and meta.get("missing"):
            meta = None                       # expired negative entry: ask once more
        if meta and meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta and meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
        url = src["url"].format(z=z, x=x, y=y)
        try:
            status, rh, body = self.mon.request(url, purpose=f"map tiles: {src['name']}", headers=headers,
                                                timeout=self.timeout)
        except OfflineError:
            return None
        except Exception:  # noqa: BLE001 - network error: serve stale if we have it
            return self._out(data_p, meta) if meta else None
        if status == 304 and meta:
            meta["expires_at"] = time.time() + max(MIN_MAX_AGE, _max_age(rh))
            self._write_meta(meta_p, meta)
            return self._out(data_p, meta)
        if status == 404:
            # the provider has no tile here (e.g. USGS above z16 or outside coverage): remember
            # that for a week so panning back and forth does not re-ask
            data_p.parent.mkdir(parents=True, exist_ok=True)
            self._write_meta(meta_p, {"url": url, "missing": True, "fetched_at": time.time(),
                                      "expires_at": time.time() + MISSING_TTL})
            return None
        if status != 200 or not body:
            return self._out(data_p, meta) if meta else None
        mime = rh.get("content-type", "image/png").split(";")[0].strip()
        if not mime.startswith("image/"):
            return None
        meta = {"url": url, "mime": mime, "etag": rh.get("etag"), "last_modified": rh.get("last-modified"),
                "fetched_at": time.time(), "expires_at": time.time() + max(MIN_MAX_AGE, _max_age(rh))}
        data_p.parent.mkdir(parents=True, exist_ok=True)
        tmp = data_p.with_suffix(".part")
        tmp.write_bytes(body)
        os.replace(tmp, data_p)
        self._write_meta(meta_p, meta)
        return {"mime": mime, "data": base64.b64encode(body).decode("ascii")}

    @staticmethod
    def _write_meta(p: Path, meta: dict) -> None:
        tmp = p.with_suffix(".jpart")
        tmp.write_text(json.dumps(meta), encoding="utf-8")
        os.replace(tmp, p)

    @staticmethod
    def _out(data_p: Path, meta: dict) -> dict | None:
        try:
            return {"mime": meta.get("mime", "image/png"), "data": base64.b64encode(data_p.read_bytes()).decode("ascii")}
        except OSError:
            return None

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
