"""Static report map renderer (dronedog.report.staticmap).

Empty input policy: render_map raises ValueError when no valid coordinate remains
(empty lists, None, 0/0 no-fix). The report shows "no GPS fix" text instead of a map.
"""
import base64
import io
import math

import pytest
from PIL import Image

from dronedog.report.staticmap import TILE, png_data_uri, render_map

LAT0, LON0 = 46.5214, 6.5673


def _track(n=400, r=0.002):
    return [[LON0 + r * math.cos(i / n * 2 * math.pi), LAT0 + r * 0.7 * math.sin(i / n * 2 * math.pi)]
            for i in range(n)]


def _png(color, size=TILE):
    b = io.BytesIO()
    Image.new("RGB", (size, size), color).save(b, "PNG")
    return b.getvalue()


def _open(res):
    im = Image.open(io.BytesIO(res["png"]))
    im.load()
    return im.convert("RGB")


def _center_pixel_far_from_track(im):
    # map-area pixel well inside the frame, away from the centred ring track and furniture
    return im.getpixel((im.width // 2, im.height // 2))


def test_graticule_render():
    res = render_map([{"coords": _track(), "color": "#E0A43A", "label": "FLY1"}],
                     [{"lon": LON0 + 0.002, "lat": LAT0, "kind": "takeoff", "label": "T0"}],
                     title="Test map", size=(800, 500))
    im = _open(res)
    assert im.size == (800, 500)
    assert res["basemap"] == "graticule"
    assert res["tiles_used"] == 0 and res["tiles_missing"] == 0
    assert 0 <= res["zoom"] <= 19
    mnlon, mnlat, mxlon, mxlat = res["bounds"]
    assert mnlon < LON0 < mxlon and mnlat < LAT0 < mxlat
    vb = res["view_bounds"]
    assert vb[0] < mnlon and vb[2] > mxlon and vb[1] < mnlat and vb[3] > mxlat
    # track colour is present
    assert any(abs(r - 0xE0) < 12 and abs(g - 0xA4) < 12 and abs(b - 0x3A) < 12
               for _, (r, g, b) in im.getcolors(maxcolors=1 << 20))
    assert png_data_uri(res["png"]).startswith("data:image/png;base64,")
    assert base64.b64decode(png_data_uri(res["png"]).split(",", 1)[1]) == res["png"]


def test_fake_tiles_stitched():
    calls = []

    def fetch(z, x, y):
        calls.append((z, x, y))
        return _png((10, 120, 200))

    res = render_map([{"coords": _track(), "label": "FLY1"}], tile_fetch=fetch,
                     attribution="Test tiles", size=(800, 500))
    assert res["basemap"] == "tiles"
    assert res["tiles_used"] > 0 and res["tiles_missing"] == 0
    assert all(z == res["zoom"] for z, _, _ in calls)
    assert _center_pixel_far_from_track(_open(res)) == (10, 120, 200)


def test_missing_tiles_overzoom_from_parent():
    def fetch(z, x, y):
        # source only has tiles up to z=14; deeper requests miss
        return _png((200, 40, 40)) if z <= 14 else None

    res = render_map([{"coords": _track(r=0.0005), "label": "FLY1"}], tile_fetch=fetch,
                     max_zoom=17, size=(800, 500))
    assert res["zoom"] == 17
    assert res["basemap"] == "tiles"
    assert res["tiles_used"] > 0 and res["tiles_missing"] == 0
    assert _center_pixel_far_from_track(_open(res)) == (200, 40, 40)


def test_tiles_missing_everywhere_falls_back_to_graticule():
    def fetch(z, x, y):
        if z == 3:
            raise RuntimeError("broken tile source")
        return None

    res = render_map([{"coords": _track()}], tile_fetch=fetch, size=(800, 500))
    assert res["basemap"] == "graticule"
    assert res["tiles_used"] == 0 and res["tiles_missing"] > 0


def test_partial_tiles_counts_missing():
    def fetch(z, x, y):
        # only even columns at z=15, no parent levels: odd columns stay missing (hatched)
        return _png((0, 200, 0)) if z == 15 and x % 2 == 0 else None

    res = render_map([{"coords": _track()}], tile_fetch=fetch, size=(800, 500), max_zoom=15)
    assert res["zoom"] == 15
    assert res["basemap"] == "tiles"
    assert res["tiles_used"] > 0 and res["tiles_missing"] > 0


def test_bridge_shaped_tile_and_bad_bytes():
    good = {"mime": "image/png", "data": base64.b64encode(_png((1, 2, 3))).decode()}

    def fetch(z, x, y):
        return good if (x + y) % 2 else b"not an image"

    res = render_map([{"coords": _track()}], tile_fetch=fetch, size=(800, 500))
    assert res["tiles_used"] > 0


def test_single_point_uses_min_span():
    res = render_map([{"coords": [[LON0, LAT0]], "label": "hover"}], size=(800, 500), max_zoom=22)
    assert res["bounds"] == [LON0, LAT0, LON0, LAT0]
    vb = res["view_bounds"]
    m_per_deg_lat = 111_320
    assert (vb[3] - vb[1]) * m_per_deg_lat >= 150          # at least ~150 m shown
    assert res["zoom"] <= 22
    assert _open(res).size == (800, 500)


def test_marker_only():
    res = render_map([], [{"lon": LON0, "lat": LAT0, "kind": "home", "label": "Home"},
                          {"lon": LON0 + 1e-4, "lat": LAT0, "kind": "home_update"},
                          {"lon": LON0, "lat": LAT0, "kind": "landing"},
                          {"lon": LON0, "lat": LAT0, "kind": "takeoff"},
                          {"lon": LON0, "lat": LAT0 - 1e-4, "kind": "bogus"}])
    assert res["basemap"] == "graticule"


def test_no_fix_points_dropped():
    coords = [[0.0, 0.0], [None, None], None, [float("nan"), 1.0], [1e-6, -1e-6]] + _track(50) + [[0, 0]]
    res = render_map([{"coords": coords}])
    assert res["bounds"][0] > 6 and res["bounds"][1] > 46


@pytest.mark.parametrize("tracks,markers", [
    ([], None),
    ([{"coords": []}], []),
    ([{"coords": [[0, 0], [None, None]]}], [{"lon": 0, "lat": 0, "kind": "home"}]),
])
def test_empty_input_raises(tracks, markers):
    with pytest.raises(ValueError):
        render_map(tracks, markers)


def test_max_zoom_respected():
    res = render_map([{"coords": [[LON0, LAT0], [LON0 + 1e-6, LAT0]]}], max_zoom=12)
    assert res["zoom"] == 12
