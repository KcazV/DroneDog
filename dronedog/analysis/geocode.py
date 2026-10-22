"""Offline reverse geocoder: "which city / county / state was this flight near?".

Answers from a vendored GeoNames extract (``dronedog/data/places.npz``, built by
``tools/make_places.py``). No network access, ever (D4).

What a result means (say exactly this in reports): the *nearest named populated place in
GeoNames* (cities500: places with population >= 500, plus admin seats; neighbourhood
"sections", historical, abandoned and destroyed places excluded) to the coordinate, with
the great-circle distance and compass bearing from that place to the point. It is not a
postal address, and it is not a boundary test: a point can be nearest to a town in one
county or state while lying inside the neighbouring one. ``admin2``/``admin1`` are the
county/state *of the reported place*.

Selection rule (consistent, no population weighting): the single nearest place by
great-circle distance wins, even when a much larger city is only slightly farther away.
Ties (identical distance) go to the first row in the data file, so results are
reproducible.

Attribution: "Contains GeoNames data, CC BY 4.0, geonames.org".
"""
from __future__ import annotations

import json
import math
import sys
import threading
from pathlib import Path

import numpy as np

DATA_FILE = "places.npz"
EARTH_R_KM = 6371.0088
ATTRIBUTION = "Contains GeoNames data, CC BY 4.0, geonames.org"
NOTE = ("Nearest named populated place in GeoNames; not a postal address or a "
        "jurisdiction boundary determination.")
_COMPASS8 = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
_NCELLS = 180 * 360

_lock = threading.Lock()
_db: "_Places | None" = None


class GeocoderUnavailable(FileNotFoundError):
    """The vendored places file is missing (run tools/make_places.py / fix the build)."""


def data_path() -> Path:
    """Location of places.npz, from source or from a PyInstaller build.

    Frozen builds put package data under ``sys._MEIPASS`` (``_internal`` in a onedir build)
    and set module ``__file__`` there too, the same way ``dronedog/ui/app.py`` finds
    ``ui/web``; the spec must ship the file as ``dronedog/data/places.npz``.
    """
    cands = [Path(__file__).resolve().parent.parent / "data" / DATA_FILE]
    if getattr(sys, "frozen", False):
        mp = getattr(sys, "_MEIPASS", None)
        if mp:
            cands.append(Path(mp) / "dronedog" / "data" / DATA_FILE)
        cands.append(Path(sys.executable).resolve().parent / "_internal" / "dronedog" / "data"
                     / DATA_FILE)
    for c in cands:
        if c.is_file():
            return c
    return cands[0]


def _strings(z, key: str) -> list[str]:
    return z[key].tobytes().decode("utf-8").split("\n")


class _Places:
    def __init__(self, path: Path):
        with np.load(path, allow_pickle=False) as z:
            lat_e5 = z["lat_e5"]
            lon_e5 = z["lon_e5"]
            self.pop = z["pop"]
            self.a1 = z["a1"]
            self.a2 = z["a2"]
            self.cc = z["cc"]
            self.fc = z["fc"]
            self.names = _strings(z, "names")
            a1_keys = _strings(z, "a1_keys")
            self.a1_names = _strings(z, "a1_names")
            self.a1_codes = [k.split(".", 1)[1] if "." in k else k for k in a1_keys]
            self.a2_names = _strings(z, "a2_names")
            self.cc_codes = _strings(z, "cc_codes")
            self.cc_names = _strings(z, "cc_names")
            self.fc_codes = _strings(z, "fc_codes")
            self.meta = json.loads(z["meta"].tobytes().decode("utf-8"))
        self.lat = lat_e5 / 1e5
        self.lon = lon_e5 / 1e5
        self.lat_r = np.radians(self.lat)
        self.lon_r = np.radians(self.lon)
        self.cos_lat = np.cos(self.lat_r)
        # Rows are pre-sorted by 1-degree cell; starts[c]..starts[c+1] is cell c.
        cell = ((np.floor(self.lat).astype(np.int64) + 90) * 360
                + (np.floor(self.lon).astype(np.int64) + 180))
        cell = np.clip(cell, 0, _NCELLS - 1)   # lat == 90 / lon == 180 edge rows
        if np.any(np.diff(cell) < 0):
            raise ValueError(f"{path}: rows not sorted by grid cell (rebuild the data file)")
        self.starts = np.searchsorted(cell, np.arange(_NCELLS + 1), side="left")
        self.source = f"{self.meta.get('source', 'GeoNames')} ({self.meta.get('licence', 'CC BY 4.0')})"

    def candidates(self, lat: float, lon: float, max_km: float) -> np.ndarray:
        dlat = max_km / 110.0 + 0.01
        r0 = max(int(math.floor(lat - dlat)) + 90, 0)
        r1 = min(int(math.floor(lat + dlat)) + 90, 179)
        far_lat = min(90.0, max(abs(lat - dlat), abs(lat + dlat)))
        cosm = math.cos(math.radians(far_lat))
        dlon = 360.0 if cosm < 1e-6 else max_km / (111.0 * cosm) + 0.01
        if dlon >= 180.0:
            segs = [(0, 359)]
        else:
            c0 = int(math.floor(lon - dlon)) + 180
            c1 = int(math.floor(lon + dlon)) + 180
            if c0 < 0:
                segs = [(c0 + 360, 359), (0, c1)]
            elif c1 > 359:
                segs = [(c0, 359), (0, c1 - 360)]
            else:
                segs = [(c0, c1)]
        parts = []
        for r in range(r0, r1 + 1):
            base = r * 360
            for a, b in segs:
                s, e = self.starts[base + a], self.starts[base + b + 1]
                if e > s:
                    parts.append(np.arange(s, e))
        return np.concatenate(parts) if parts else np.empty(0, np.int64)


def _load() -> _Places:
    global _db
    if _db is None:
        with _lock:
            if _db is None:
                p = data_path()
                if not p.is_file():
                    raise GeocoderUnavailable(f"offline places data not found: {p}")
                _db = _Places(p)
    return _db


def available() -> bool:
    """True when the places data file is present (does not load it)."""
    return data_path().is_file()


def data_info() -> dict:
    """Provenance of the loaded data (source, download date, licence, attribution, count)."""
    return dict(_load().meta)


def _valid(lat, lon) -> tuple[float, float] | None:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(la) and math.isfinite(lo)):
        return None
    if abs(la) > 90.0 or abs(lo) > 180.0:
        return None
    if la == 0.0 and lo == 0.0:          # DJI logs write 0,0 when there is no GPS fix
        return None
    if lo >= 180.0:
        lo -= 360.0
    return la, lo


def nearest_place(lat: float, lon: float, max_km: float = 50.0) -> dict | None:
    """Nearest GeoNames populated place within ``max_km`` of (lat, lon), or None.

    None when nothing is within ``max_km`` or the coordinate is invalid (None, NaN,
    |lat| > 90, |lon| > 180, or exactly 0,0 = DJI "no fix"). ``bearing_deg`` is the initial
    great-circle bearing *from the place to the point* (0 = north, clockwise). ``lat``/
    ``lon`` in the result are the place's coordinates. Raises GeocoderUnavailable if the
    data file is missing (so a missing file is never reported as "no place nearby").
    """
    v = _valid(lat, lon)
    try:
        max_km = float(max_km)
    except (TypeError, ValueError):
        return None
    if v is None or not (max_km > 0):
        return None
    la, lo = v
    db = _load()
    idx = db.candidates(la, lo, max_km)
    if idx.size == 0:
        return None
    p1, l1 = math.radians(la), math.radians(lo)
    p2, l2 = db.lat_r[idx], db.lon_r[idx]
    h = (np.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * db.cos_lat[idx] * np.sin((l2 - l1) / 2) ** 2)
    d = 2 * EARTH_R_KM * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))
    k = int(np.argmin(d))
    dist = float(d[k])
    if dist > max_km:
        return None
    i = int(idx[k])
    # bearing from place (i) to point
    pa, pb = db.lat_r[i], p1
    dl = l1 - db.lon_r[i]
    y = math.sin(dl) * math.cos(pb)
    x = math.cos(pa) * math.sin(pb) - math.sin(pa) * math.cos(pb) * math.cos(dl)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    a1, a2 = int(db.a1[i]), int(db.a2[i])
    cci = int(db.cc[i])
    return {
        "name": db.names[i],
        "admin2": db.a2_names[a2] if a2 >= 0 else None,
        "admin1": db.a1_names[a1] if a1 >= 0 else None,
        "admin1_code": db.a1_codes[a1] if a1 >= 0 else None,
        "country": db.cc_names[cci],
        "country_code": db.cc_codes[cci],
        "population": int(db.pop[i]),
        "feature_code": db.fc_codes[int(db.fc[i])],
        "distance_km": round(dist, 1),
        "bearing_deg": int(round(bearing)) % 360,
        "lat": float(db.lat[i]),
        "lon": float(db.lon[i]),
        "source": db.source,
    }


def compass8(bearing_deg: float) -> str:
    """8-point compass name for a bearing in degrees."""
    return _COMPASS8[int(((bearing_deg % 360.0) + 22.5) // 45.0) % 8]


def place_label(p: dict) -> str:
    """'Lausanne, Vaud, Switzerland'; US adds the county: 'Boulder, Boulder County,
    Colorado, United States'. Consecutive duplicate parts are collapsed."""
    parts = [p.get("name"), p.get("admin2") if p.get("country_code") == "US" else None,
             p.get("admin1"), p.get("country")]
    out: list[str] = []
    for s in parts:
        if s and (not out or out[-1].casefold() != s.casefold()):
            out.append(s)
    return ", ".join(out)


def locate(lat: float, lon: float, max_km: float = 50.0) -> dict | None:
    """``nearest_place`` plus ``label``, ``summary`` and ``note``.

    summary: "1.2 km NE of Lausanne, Vaud, Switzerland" (8-point compass, direction from the
    place to the point); "within 0.1 km of ..." when closer than 0.1 km. Same selection rule
    as ``nearest_place`` (strictly nearest place, see module docstring).
    """
    p = nearest_place(lat, lon, max_km)
    if p is None:
        return None
    label = place_label(p)
    d = p["distance_km"]
    if d < 0.1:
        summary = f"within 0.1 km of {label}"
    else:
        summary = f"{d:.1f} km {compass8(p['bearing_deg'])} of {label}"
    p.update(label=label, summary=summary, note=NOTE)
    return p
