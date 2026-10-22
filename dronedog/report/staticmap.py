"""Static map images for the printable report (Pillow only, no network).

``render_map`` draws one or more GPS tracks plus event markers onto a PNG that can be
embedded in the HTML report (``png_data_uri``).  It never fetches anything itself: the
caller may pass ``tile_fetch(z, x, y) -> bytes | None`` (an offline MBTiles pack or the
NetMonitor-gated online tile cache, adapted to return raw PNG/JPEG bytes).  Without
tiles, or when no tile is available, the map is drawn on a neutral WGS 84 graticule so
the track still reads.

Projection is Web Mercator (EPSG:3857, XYZ "slippy" tiles, 256 px), north up.  The
image is framed with a thin margin that carries the latitude / longitude tick labels.

Input coordinates that are ``None``, non-finite, out of range or within 1e-5 deg of
(0, 0) (DJI "no fix") are dropped.  If nothing valid remains ``render_map`` raises
``ValueError`` -- a flight without a GPS fix has no map, and the report should say so
in words rather than show an empty grid.
"""

from __future__ import annotations

import base64
import bisect
import io
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

__all__ = ["render_map", "png_data_uri"]

TILE = 256
EARTH_CIRC = 40075016.686          # WGS 84 equatorial circumference, metres
MAX_LAT = 85.0511287798
MIN_SPAN_M = 150.0                 # smallest ground span shown (single point / hover)
OVERZOOM_LEVELS = 6                # how many parent levels to try for a missing tile
SS = 3                             # supersampling factor for vector overlay anti-aliasing
FRAME = 22                         # outer margin carrying the coordinate labels
PALETTE = ["#E0A43A", "#3A8EE0", "#D9534F", "#4CAF6A", "#9B6BD6", "#17A2B8"]

# basemap / furniture colours
BG_PAPER = (238, 240, 243)
GRID_MAJOR = (205, 211, 220)
GRID_MINOR = (226, 230, 235)
MISSING_FILL = (226, 228, 232)
MISSING_HATCH = (208, 211, 217)
FRAME_BG = (255, 255, 255)
FRAME_LINE = (74, 81, 96)
INK = (33, 37, 46)
INK_SOFT = (90, 97, 110)
TAKEOFF = (46, 158, 79)
LANDING = (214, 69, 65)
HOME = (232, 185, 49)
POINT = (40, 92, 170)
OUTLINE = (25, 27, 32)

_FONT_DIR = Path(__file__).resolve().parent.parent / "ui" / "web" / "vendor" / "fonts"
_FONT_FILES = {"sans": "inter-latin-wght-normal.woff2", "mono": "jetbrains-mono-latin-wght-normal.woff2"}


# --------------------------------------------------------------------------- helpers

@lru_cache(maxsize=64)
def _font(size: int, weight: str = "Regular", family: str = "sans"):
    """Bundled Inter / JetBrains Mono (woff2, variable weight); Pillow default as fallback."""
    try:
        # BASIC layout: raqm ignores variable-font weight changes in its advances
        f = ImageFont.truetype(str(_FONT_DIR / _FONT_FILES[family]), size,
                               layout_engine=ImageFont.Layout.BASIC)
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
        return f
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # Pillow < 10.1
            return ImageFont.load_default()


def png_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _valid_lonlat(lon, lat) -> tuple[float, float] | None:
    try:
        lon = float(lon)
        lat = float(lat)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    if abs(lat) < 1e-5 and abs(lon) < 1e-5:
        return None
    return lon, lat


def _mx(lon: float) -> float:
    return (lon + 180.0) / 360.0


def _my(lat: float) -> float:
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    s = math.sin(math.radians(lat))
    return 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


def _lon_of(x: float) -> float:
    return x * 360.0 - 180.0


def _lat_of(y: float) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y))))


def _rgb(c, default) -> tuple[int, int, int]:
    try:
        return ImageColor.getrgb(c)[:3]
    except Exception:
        return ImageColor.getrgb(default)[:3]


def _nice_floor(v: float) -> float:
    """Largest 1/2/5 x 10^k that is <= v."""
    if v <= 0:
        return 0.0
    b = 10 ** math.floor(math.log10(v))
    for m in (5, 2, 1):
        if m * b <= v * (1 + 1e-9):
            return m * b
    return b


def _nice_step(span: float, target_lines: float) -> float:
    raw = span / max(target_lines, 1.0)
    b = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * b >= raw:
            return m * b
    return 10 * b


def _fmt_num(v: float) -> str:
    return f"{v:,.0f}" if v >= 10 else f"{v:g}"


def _fmt_coord(v: float, step: float, pos: str, neg: str) -> str:
    dec = max(0, math.ceil(-math.log10(step) - 1e-9))
    return f"{abs(v):.{dec}f}°{pos if v >= 0 else neg}"


def _decode_tile(blob) -> Image.Image | None:
    if isinstance(blob, dict):          # tolerate the UI bridge shape {"mime", "data": base64}
        try:
            blob = base64.b64decode(blob.get("data") or "")
        except Exception:
            return None
    if not blob:
        return None
    try:
        im = Image.open(io.BytesIO(bytes(blob)))
        im.load()
        im = im.convert("RGB")
    except Exception:
        return None
    if im.size != (TILE, TILE):
        im = im.resize((TILE, TILE), Image.Resampling.BICUBIC)
    return im


class _Text:
    """Deferred 1x text draws, executed after the supersampled overlay is composited."""

    def __init__(self):
        self.ops = []

    def add(self, xy, text, font, fill=INK, anchor="la", stroke=0, stroke_fill=(255, 255, 255)):
        self.ops.append((xy, text, font, fill, anchor, stroke, stroke_fill))

    def run(self, draw: ImageDraw.ImageDraw):
        for xy, text, font, fill, anchor, stroke, stroke_fill in self.ops:
            draw.text(xy, text, font=font, fill=fill, anchor=anchor,
                      stroke_width=stroke, stroke_fill=stroke_fill)


def _text_size(text: str, font) -> tuple[int, int]:
    x0, y0, x1, y1 = font.getbbox(text)
    return x1 - x0, y1 - y0


# --------------------------------------------------------------------------- render

def render_map(tracks, markers=None, tile_fetch=None, size=(1000, 620), max_zoom=19,
               attribution=None, title=None) -> dict:
    """Render tracks + markers to a PNG.

    tracks:  [{"coords": [[lon, lat], ...], "color": "#E0A43A", "label": str}, ...]
    markers: [{"lon", "lat", "kind": takeoff|landing|home|home_update|point, "label"}, ...]
    tile_fetch(z, x, y) -> PNG/JPEG bytes or None.  Missing tiles are filled from the
    nearest available parent tile (overzoom, up to OVERZOOM_LEVELS levels); tiles with
    no data at any level are hatched grey.  No tiles at all -> graticule basemap.

    Returns {"png", "zoom", "bounds" (data bounds, lon/lat), "view_bounds" (map area),
    "tiles_used", "tiles_missing", "basemap": "tiles"|"graticule"}.
    Raises ValueError when there is no valid coordinate to show.
    """
    W, H = int(size[0]), int(size[1])
    if W < 200 or H < 150:
        raise ValueError("render_map: size must be at least 200x150")
    MW, MH = W - 2 * FRAME, H - 2 * FRAME

    # ---- clean input
    clean_tracks = []
    for i, t in enumerate(tracks or []):
        pts = []
        for c in t.get("coords") or []:
            if c is None or len(c) < 2:
                continue
            v = _valid_lonlat(c[0], c[1])
            if v:
                pts.append(v)
        if pts:
            clean_tracks.append({"pts": pts, "label": str(t.get("label") or ""),
                                 "color": _rgb(t.get("color") or PALETTE[i % len(PALETTE)],
                                               PALETTE[i % len(PALETTE)])})
    clean_markers = []
    for m in markers or []:
        v = _valid_lonlat(m.get("lon"), m.get("lat"))
        if v:
            kind = m.get("kind") if m.get("kind") in ("takeoff", "landing", "home", "home_update") else "point"
            clean_markers.append({"lon": v[0], "lat": v[1], "kind": kind, "label": str(m.get("label") or "")})
    allpts = [p for t in clean_tracks for p in t["pts"]] + [(m["lon"], m["lat"]) for m in clean_markers]
    if not allpts:
        raise ValueError("render_map: no valid coordinates (empty, None or 0,0 no-fix only)")

    lons = [p[0] for p in allpts]
    lats = [p[1] for p in allpts]
    bounds = [min(lons), min(lats), max(lons), max(lats)]

    # ---- fit: normalised mercator extent, padded, min ground span
    x0, x1 = _mx(bounds[0]), _mx(bounds[2])
    y0, y1 = _my(bounds[3]), _my(bounds[1])          # y grows southwards
    clat = (bounds[1] + bounds[3]) / 2
    min_span = MIN_SPAN_M / (EARTH_CIRC * max(math.cos(math.radians(clat)), 1e-6))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    sx, sy = max(x1 - x0, min_span), max(y1 - y0, min_span)
    pad_l, pad_r = 44, 44
    pad_t = 78 if title else 48
    pad_b = 70
    uw, uh = max(MW - pad_l - pad_r, 40), max(MH - pad_t - pad_b, 40)
    zf = math.log2(min(uw / (sx * TILE), uh / (sy * TILE)))
    zoom = int(max(0, min(int(max_zoom), math.floor(zf))))
    ws = TILE * (1 << zoom)
    ox = int(round(cx * ws - (pad_l + uw / 2)))
    oy = int(round(cy * ws - (pad_t + uh / 2)))

    def proj(lon, lat):
        return _mx(lon) * ws - ox, _my(lat) * ws - oy

    view_bounds = [_lon_of(ox / ws), _lat_of(min((oy + MH) / ws, 1.0)),
                   _lon_of((ox + MW) / ws), _lat_of(max(oy / ws, 0.0))]

    # ---- basemap
    base = Image.new("RGB", (MW, MH), BG_PAPER)
    tiles_used = tiles_missing = 0
    missing_rects = []
    if tile_fetch is not None:
        cache: dict = {}

        def get(z, x, y):
            key = (z, x, y)
            if key not in cache:
                try:
                    cache[key] = _decode_tile(tile_fetch(z, x, y))
                except Exception:
                    cache[key] = None
            return cache[key]

        n = 1 << zoom
        for ty in range(math.floor(oy / TILE), math.floor((oy + MH - 1) / TILE) + 1):
            if ty < 0 or ty >= n:
                continue
            for tx in range(math.floor(ox / TILE), math.floor((ox + MW - 1) / TILE) + 1):
                x = tx % n
                img = get(zoom, x, ty)
                if img is None:
                    for dz in range(1, min(OVERZOOM_LEVELS, zoom) + 1):
                        parent = get(zoom - dz, x >> dz, ty >> dz)
                        if parent is not None:
                            sub = TILE >> dz
                            px = (x - ((x >> dz) << dz)) * sub
                            py = (ty - ((ty >> dz) << dz)) * sub
                            img = parent.crop((px, py, px + sub, py + sub)).resize(
                                (TILE, TILE), Image.Resampling.BICUBIC)
                            break
                pos = (tx * TILE - ox, ty * TILE - oy)
                if img is not None:
                    base.paste(img, pos)
                    tiles_used += 1
                else:
                    tiles_missing += 1
                    missing_rects.append(pos)
    basemap = "tiles" if tiles_used else "graticule"

    # graticule steps (also used for the frame tick labels in tile mode)
    lon_step = _nice_step(view_bounds[2] - view_bounds[0], MW / 140)
    lat_step = _nice_step(view_bounds[3] - view_bounds[1], MH / 110)

    bd = ImageDraw.Draw(base)
    if basemap == "tiles" and missing_rects:
        for (px, py) in missing_rects:
            bd.rectangle([px, py, px + TILE - 1, py + TILE - 1], fill=MISSING_FILL)
            for k in range(-TILE, TILE, 12):
                bd.line([(px + k, py + TILE), (px + k + TILE, py)], fill=MISSING_HATCH, width=1)
    if basemap == "graticule":
        if tile_fetch is not None:
            # tiles were requested but none existed: fill with the neutral paper
            bd.rectangle([0, 0, MW, MH], fill=BG_PAPER)
        for step, colr in ((lon_step / (4 if _mant(lon_step) == 2 else 5), GRID_MINOR), (lon_step, GRID_MAJOR)):
            for lon in _ticks(view_bounds[0], view_bounds[2], step):
                X = round(_mx(lon) * ws - ox)
                bd.line([(X, 0), (X, MH)], fill=colr, width=1)
        for step, colr in ((lat_step / (4 if _mant(lat_step) == 2 else 5), GRID_MINOR), (lat_step, GRID_MAJOR)):
            for lat in _ticks(view_bounds[1], view_bounds[3], step):
                Y = round(_my(lat) * ws - oy)
                bd.line([(0, Y), (MW, Y)], fill=colr, width=1)

    # ---- vector overlay (supersampled)
    ov_img = Image.new("RGBA", (MW * SS, MH * SS), (0, 0, 0, 0))
    ov = ImageDraw.Draw(ov_img)
    txt = _Text()
    halo_a = 210 if basemap == "tiles" else 185
    track_px = []

    for t in clean_tracks:
        pts = [proj(lon, lat) for lon, lat in t["pts"]]
        dec = [pts[0]]
        for p in pts[1:]:
            q = dec[-1]
            if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 >= 0.6:
                dec.append(p)
        track_px.extend(dec)
        t["px"] = dec
        s = [(x * SS, y * SS) for x, y in dec]
        if len(s) == 1:
            r = 5 * SS
            ov.ellipse([s[0][0] - r - SS, s[0][1] - r - SS, s[0][0] + r + SS, s[0][1] + r + SS],
                       fill=(10, 10, 12, halo_a))
            ov.ellipse([s[0][0] - r, s[0][1] - r, s[0][0] + r, s[0][1] + r], fill=t["color"] + (255,))
            continue
        hw, tw = (7 if basemap == "tiles" else 6) * SS, 3 * SS
        ov.line(s, fill=(10, 10, 12, halo_a), width=hw, joint="curve")
        for e in (s[0], s[-1]):
            ov.ellipse([e[0] - hw / 2, e[1] - hw / 2, e[0] + hw / 2, e[1] + hw / 2], fill=(10, 10, 12, halo_a))
        ov.line(s, fill=t["color"] + (255,), width=tw, joint="curve")
        for e in (s[0], s[-1]):
            ov.ellipse([e[0] - tw / 2, e[1] - tw / 2, e[0] + tw / 2, e[1] + tw / 2], fill=t["color"] + (255,))
    for t in clean_tracks:
        if len(t.get("px", [])) > 1:
            _draw_arrows(ov, t["px"], t["color"])

    # ---- markers
    placed = []          # 1x boxes of icons already drawn, for collision avoidance
    order = {"home_update": 0, "home": 1, "takeoff": 2, "landing": 3, "point": 4}
    for m in sorted(clean_markers, key=lambda m: order[m["kind"]]):
        X, Y = proj(m["lon"], m["lat"])
        if m["kind"] == "home_update":
            _icon(ov, txt, "home_update", X, Y)
            continue
        if m["kind"] == "home":
            hx, hy = X, Y - 26
            _icon(ov, txt, "home", X, Y)
            placed.append((hx - 11, hy - 11, hx + 11, hy + 11))
            lx, ly = hx + 15, hy
        else:
            ix, iy = _place(X, Y, placed)
            if (ix, iy) != (X, Y):
                ov.line([(X * SS, Y * SS), (ix * SS, iy * SS)], fill=OUTLINE + (255,), width=int(1.5 * SS))
                r = 3 * SS
                ov.ellipse([X * SS - r, Y * SS - r, X * SS + r, Y * SS + r], fill=(255, 255, 255, 255),
                           outline=OUTLINE + (255,), width=SS)
            _icon(ov, txt, m["kind"], ix, iy)
            placed.append((ix - 11, iy - 11, ix + 11, iy + 11))
            lx, ly = ix + 15, iy
        if m["label"]:
            txt.add((lx, ly), m["label"], _font(12, "SemiBold"), fill=INK, anchor="lm", stroke=3)

    # ---- furniture
    _furniture(ov, txt, MW, MH, ws, oy, title, attribution, basemap, clean_tracks, clean_markers, track_px)

    # ---- composite map area, then frame
    ov_small = ov_img.resize((MW, MH), Image.Resampling.BOX)
    mapimg = Image.alpha_composite(base.convert("RGBA"), ov_small)
    txt.run(ImageDraw.Draw(mapimg))

    canvas = Image.new("RGB", (W, H), FRAME_BG)
    canvas.paste(mapimg.convert("RGB"), (FRAME, FRAME))
    _frame(canvas, MW, MH, ws, ox, oy, view_bounds, lon_step, lat_step)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=False, compress_level=6)
    return {"png": buf.getvalue(), "zoom": zoom, "bounds": bounds,
            "view_bounds": [round(v, 7) for v in view_bounds],
            "tiles_used": tiles_used, "tiles_missing": tiles_missing, "basemap": basemap}


# --------------------------------------------------------------------------- drawing parts

def _mant(step: float) -> int:
    return int(round(step / 10 ** math.floor(math.log10(step))))


def _ticks(lo: float, hi: float, step: float):
    k = math.ceil(lo / step - 1e-9)
    out = []
    while k * step <= hi + 1e-12 and len(out) < 500:
        out.append(round(k * step, 10))
        k += 1
    return out


def _point_at(px, cum, s):
    i = max(1, min(len(cum) - 1, bisect.bisect_left(cum, s)))
    seg = cum[i] - cum[i - 1]
    f = 0.0 if seg <= 0 else (s - cum[i - 1]) / seg
    (ax, ay), (bx, by) = px[i - 1], px[i]
    return ax + (bx - ax) * f, ay + (by - ay) * f


def _draw_arrows(ov, px, color):
    cum = [0.0]
    for (ax, ay), (bx, by) in zip(px, px[1:]):
        cum.append(cum[-1] + math.hypot(bx - ax, by - ay))
    total = cum[-1]
    if total < 90:
        return
    spacing = max(120.0, total / 40)
    n = max(1, int(total // spacing))
    for k in range(n):
        s = (k + 0.5) * total / n
        x, y = _point_at(px, cum, s)
        ax, ay = _point_at(px, cum, max(0.0, s - 6))
        bx, by = _point_at(px, cum, min(total, s + 6))
        if math.hypot(bx - ax, by - ay) > 2:
            ang = math.atan2(by - ay, bx - ax)
            ca, sa = math.cos(ang), math.sin(ang)

            def P(u, v):
                return ((x + u * ca - v * sa) * SS, (y + u * sa + v * ca) * SS)

            tri = [P(7, 0), P(-5, -5.5), P(-2.5, 0), P(-5, 5.5)]
            ov.polygon(tri, fill=(255, 255, 255, 255), outline=OUTLINE + (255,), width=int(1.2 * SS))


def _place(X, Y, placed):
    def hit(cx, cy):
        return any(not (cx + 11 < b[0] or cx - 11 > b[2] or cy + 11 < b[1] or cy - 11 > b[3]) for b in placed)

    if not hit(X, Y):
        return X, Y
    for r in (26, 40):
        for deg in (45, -45, 135, -135, 0, 180, 90, -90):
            a = math.radians(deg)
            cx, cy = X + r * math.cos(a), Y - r * math.sin(a)
            if not hit(cx, cy):
                return cx, cy
    return X + 26, Y + 26


def _icon(ov, txt, kind, X, Y, scale=1.0, letters=True):
    S = SS
    o = OUTLINE + (255,)
    w = (255, 255, 255, 255)
    if kind == "takeoff":
        r = 9 * scale
        ov.ellipse([(X - r - 1.5) * S, (Y - r - 1.5) * S, (X + r + 1.5) * S, (Y + r + 1.5) * S], fill=o)
        ov.ellipse([(X - r) * S, (Y - r) * S, (X + r) * S, (Y + r) * S], fill=TAKEOFF + (255,),
                   outline=w, width=int(1.6 * S))
        if letters:
            txt.add((X, Y + 0.5), "T", _font(max(8, int(11 * scale)), "Bold"), fill=(255, 255, 255), anchor="mm")
    elif kind == "landing":
        r = 8 * scale
        ov.rounded_rectangle([(X - r - 1.5) * S, (Y - r - 1.5) * S, (X + r + 1.5) * S, (Y + r + 1.5) * S],
                             radius=2.5 * S, fill=o)
        ov.rounded_rectangle([(X - r) * S, (Y - r) * S, (X + r) * S, (Y + r) * S], radius=1.5 * S,
                             fill=LANDING + (255,), outline=w, width=int(1.6 * S))
        if letters:
            txt.add((X, Y + 0.5), "L", _font(max(8, int(11 * scale)), "Bold"), fill=(255, 255, 255), anchor="mm")
    elif kind == "home":
        # map pin: tip on the home point, head above it
        r = 9.5 * scale
        hy = Y - 26 * scale
        stem = [(X * S, Y * S), ((X - 6 * scale) * S, (hy + 5 * scale) * S), ((X + 6 * scale) * S, (hy + 5 * scale) * S)]
        ov.polygon(stem, fill=o)
        ov.ellipse([(X - r - 1.5) * S, (hy - r - 1.5) * S, (X + r + 1.5) * S, (hy + r + 1.5) * S], fill=o)
        inner = [(X * S, (Y - 2.2 * scale) * S), ((X - 4.6 * scale) * S, (hy + 4 * scale) * S),
                 ((X + 4.6 * scale) * S, (hy + 4 * scale) * S)]
        ov.polygon(inner, fill=HOME + (255,))
        ov.ellipse([(X - r) * S, (hy - r) * S, (X + r) * S, (hy + r) * S], fill=HOME + (255,))
        if letters:
            txt.add((X, hy + 0.5), "H", _font(max(8, int(11 * scale)), "Bold"), fill=INK, anchor="mm")
    elif kind == "home_head":       # legend swatch: pin head only
        r = 9 * scale
        ov.ellipse([(X - r - 1.5) * S, (Y - r - 1.5) * S, (X + r + 1.5) * S, (Y + r + 1.5) * S], fill=o)
        ov.ellipse([(X - r) * S, (Y - r) * S, (X + r) * S, (Y + r) * S], fill=HOME + (255,))
        if letters:
            txt.add((X, Y + 0.5), "H", _font(max(8, int(11 * scale)), "Bold"), fill=INK, anchor="mm")
    elif kind == "home_update":
        r = 4.5 * scale
        ov.ellipse([(X - r - 1.2) * S, (Y - r - 1.2) * S, (X + r + 1.2) * S, (Y + r + 1.2) * S],
                   outline=o, width=int(4.4 * S))
        ov.ellipse([(X - r) * S, (Y - r) * S, (X + r) * S, (Y + r) * S], outline=HOME + (255,), width=int(2 * S))
    else:
        r = 5.5 * scale
        ov.ellipse([(X - r - 1.5) * S, (Y - r - 1.5) * S, (X + r + 1.5) * S, (Y + r + 1.5) * S], fill=o)
        ov.ellipse([(X - r) * S, (Y - r) * S, (X + r) * S, (Y + r) * S], fill=POINT + (255,),
                   outline=w, width=int(1.6 * S))


def _chip(ov, box, alpha=232):
    x0, y0, x1, y1 = box
    ov.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=4 * SS,
                         fill=(255, 255, 255, alpha), outline=(40, 44, 52, 70), width=SS)


def _furniture(ov, txt, MW, MH, ws, oy, title, attribution, basemap, tracks, markers, track_px):
    pad = 10
    # title, top-left
    title_box = None
    if title:
        f = _font(15, "SemiBold")
        tw, th = _text_size(title, f)
        title_box = (pad, pad, pad + tw + 20, pad + 32)
        _chip(ov, title_box)
        txt.add((pad + 10, pad + 16), title, f, fill=INK, anchor="lm")

    # north arrow, top-right
    nb = (MW - pad - 34, pad, MW - pad, pad + 46)
    _chip(ov, nb)
    cx = (nb[0] + nb[2]) / 2
    txt.add((cx, nb[1] + 11), "N", _font(12, "Bold"), fill=INK, anchor="mm")
    top, bot, mid = nb[1] + 20, nb[3] - 6, nb[3] - 13
    ov.polygon([(cx * SS, top * SS), ((cx - 7) * SS, bot * SS), (cx * SS, mid * SS)], fill=INK + (255,))
    ov.polygon([(cx * SS, top * SS), ((cx + 7) * SS, bot * SS), (cx * SS, mid * SS)],
               fill=(255, 255, 255, 255), outline=INK + (255,), width=SS)

    # scale bar, bottom-left (metres above, feet below)
    lat_c = _lat_of((oy + MH / 2) / ws)
    mpp = EARTH_CIRC * math.cos(math.radians(lat_c)) / ws
    max_px = 150
    m_len = _nice_floor(max_px * mpp)
    m_px = m_len / mpp
    m_lbl = f"{_fmt_num(m_len / 1000)} km" if m_len >= 1000 else f"{_fmt_num(m_len)} m"
    ft_pp = mpp * 3.280839895
    if max_px * ft_pp >= 5280:
        mi = _nice_floor(max_px * ft_pp / 5280)
        i_px, i_lbl = mi * 5280 / ft_pp, f"{_fmt_num(mi)} mi"
    else:
        ft = _nice_floor(max_px * ft_pp)
        i_px, i_lbl = ft / ft_pp, f"{_fmt_num(ft)} ft"
    fs = _font(11, "Medium")
    lw = max(_text_size(m_lbl, fs)[0], _text_size(i_lbl, fs)[0])
    bar_w = max(m_px, i_px)
    sb = (pad, MH - pad - 42, pad + 14 + bar_w + 8 + lw + 10, MH - pad)
    _chip(ov, sb)
    bx, by = sb[0] + 12, sb[1] + 21
    lw_ = int(1.6 * SS)
    ov.line([(bx * SS, by * SS), ((bx + bar_w) * SS, by * SS)], fill=INK + (255,), width=lw_)
    for frac in (0, 0.5, 1):
        X = bx + m_px * frac
        ov.line([(X * SS, (by - (7 if frac != 0.5 else 4)) * SS), (X * SS, by * SS)], fill=INK + (255,), width=lw_)
        X = bx + i_px * frac
        ov.line([(X * SS, by * SS), (X * SS, (by + (7 if frac != 0.5 else 4)) * SS)], fill=INK + (255,), width=lw_)
    txt.add((bx + bar_w + 8, by - 6), m_lbl, fs, fill=INK, anchor="lm")
    txt.add((bx + bar_w + 8, by + 7), i_lbl, fs, fill=INK, anchor="lm")

    # attribution, bottom-right
    parts = []
    if attribution:
        parts.append(str(attribution))
    if basemap == "graticule":
        parts.append("No basemap · WGS 84 graticule")
    att_box = None
    if parts:
        fa = _font(10, "Regular")
        s = "  ·  ".join(parts)
        aw, _ = _text_size(s, fa)
        att_box = (MW - pad - aw - 14, MH - pad - 18, MW - pad, MH - pad)
        _chip(ov, att_box, alpha=215)
        txt.add((att_box[2] - 7, (att_box[1] + att_box[3]) / 2), s, fa, fill=INK_SOFT, anchor="rm")

    # legend: put it in the corner slot that hides the fewest track points
    entries = [("track", t["label"], t["color"]) for t in tracks if t["label"]]
    present = {m["kind"] for m in markers}
    names = {"takeoff": "Takeoff", "landing": "Landing", "home": "Home point",
             "home_update": "Home point update", "point": "Marked point"}
    entries += [(k, names[k], None) for k in ("takeoff", "landing", "home", "home_update", "point") if k in present]
    if not entries:
        return
    fl = _font(11, "Medium")
    row = 20
    lw = max(_text_size(e[1], fl)[0] for e in entries)
    w, h = 34 + lw + 12, 10 + row * len(entries)
    slots = [
        (MW - pad - w, nb[3] + 8),                                                # top-right
        (pad, (title_box[3] if title_box else pad - 8) + 8),                     # top-left
        (MW - pad - w, (att_box[1] if att_box else MH - pad) - 8 - h),           # bottom-right
        (pad, sb[1] - 8 - h),                                                     # bottom-left
    ]

    def cost(x, y):
        return sum(1 for (px, py) in track_px if x - 6 <= px <= x + w + 6 and y - 6 <= py <= y + h + 6)

    lx, ly = min(slots, key=lambda s: cost(*s))
    _chip(ov, (lx, ly, lx + w, ly + h))
    for i, (kind, label, color) in enumerate(entries):
        cy = ly + 5 + row * i + row / 2
        ix = lx + 17
        if kind == "track":
            ov.line([((ix - 10) * SS, cy * SS), ((ix + 10) * SS, cy * SS)], fill=(10, 10, 12, 170), width=7 * SS)
            ov.line([((ix - 10) * SS, cy * SS), ((ix + 10) * SS, cy * SS)], fill=color + (255,), width=3 * SS)
        else:
            k = "home_head" if kind == "home" else kind
            _icon(ov, txt, k, ix, cy, scale=0.78, letters=k in ("takeoff", "landing", "home_head"))
        txt.add((lx + 34, cy), label, fl, fill=INK, anchor="lm")


def _frame(canvas, MW, MH, ws, ox, oy, vb, lon_step, lat_step):
    d = ImageDraw.Draw(canvas)
    f = _font(10, "Regular", "mono")
    x0, y0, x1, y1 = FRAME, FRAME, FRAME + MW, FRAME + MH
    edge = 34   # keep labels away from the corners
    for lon in _ticks(vb[0], vb[2], lon_step):
        X = FRAME + _mx(lon) * ws - ox
        if X < x0 + edge or X > x1 - edge:
            continue
        lbl = _fmt_coord(lon, lon_step, "E", "W")
        d.line([(X, y0 - 4), (X, y0 - 1)], fill=FRAME_LINE)
        d.line([(X, y1), (X, y1 + 3)], fill=FRAME_LINE)
        d.text((X, y0 - 6), lbl, font=f, fill=INK_SOFT, anchor="md")
        d.text((X, y1 + 6), lbl, font=f, fill=INK_SOFT, anchor="ma")
    for lat in _ticks(vb[1], vb[3], lat_step):
        Y = FRAME + _my(lat) * ws - oy
        if Y < y0 + edge or Y > y1 - edge:
            continue
        lbl = _fmt_coord(lat, lat_step, "N", "S")
        d.line([(x0 - 4, Y), (x0 - 1, Y)], fill=FRAME_LINE)
        d.line([(x1, Y), (x1 + 3, Y)], fill=FRAME_LINE)
        tw, th = _text_size(lbl, f)
        timg = Image.new("L", (tw + 4, 14), 0)
        ImageDraw.Draw(timg).text((2 + tw / 2, 7), lbl, font=f, fill=255, anchor="mm")
        rot = timg.rotate(90, expand=True)
        ink = Image.new("RGB", rot.size, INK_SOFT)
        canvas.paste(ink, (int(x0 - 6 - rot.size[0]), int(Y - rot.size[1] / 2)), rot)
        canvas.paste(ink, (int(x1 + 6), int(Y - rot.size[1] / 2)), rot)
    d.rectangle([x0 - 1, y0 - 1, x1, y1], outline=FRAME_LINE, width=1)
