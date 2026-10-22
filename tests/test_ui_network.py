"""D11 network tests: single choke point, offline refusal, tile cache + revalidation, 404 handling,
custody logging of the online toggle, CSP present."""
import base64
import http.server
import re
import threading
from pathlib import Path

import pytest

from dronedog.ui import online_tiles
from dronedog.ui.mock_api import MockApi
from dronedog.ui.netmon import NetMonitor, OfflineError
from dronedog.ui.online_tiles import OnlineTiles

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
WEB = Path(__file__).resolve().parents[1] / "dronedog" / "ui" / "web"


class TileHandler(http.server.BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):  # noqa: N802
        TileHandler.hits.append((self.path, self.headers.get("If-None-Match"), self.headers.get("User-Agent")))
        if self.path.startswith("/3/"):                 # "no tile at this zoom"
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.headers.get("If-None-Match") == '"v1"':
            self.send_response(304)
            self.send_header("Cache-Control", "max-age=0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("ETag", '"v1"')
        self.send_header("Cache-Control", self.server.cc)
        self.send_header("Content-Length", str(len(PNG)))
        self.end_headers()
        self.wfile.write(PNG)

    def log_message(self, *a):
        pass


@pytest.fixture
def tile_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), TileHandler)
    srv.cc = "max-age=3600"
    TileHandler.hits = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def make_tiles(tmp_path, srv, monkeypatch):
    src = dict(online_tiles.SOURCES["osm"], url=f"http://127.0.0.1:{srv.server_port}/{{z}}/{{x}}/{{y}}.png")
    monkeypatch.setitem(online_tiles.SOURCES, "osm", src)
    mon = NetMonitor()
    return mon, OnlineTiles(mon, cache_dir=tmp_path / "cache", timeout=3)


def test_offline_refuses_all_requests(tmp_path, tile_server, monkeypatch):
    mon, tiles = make_tiles(tmp_path, tile_server, monkeypatch)
    assert tiles.tile("online:osm", 1, 0, 0) is None
    with pytest.raises(OfflineError):
        mon.request(f"http://127.0.0.1:{tile_server.server_port}/x", purpose="t")
    assert TileHandler.hits == []
    assert mon.status(False)["state"] == "offline"


def test_tile_fetch_cache_and_revalidate(tmp_path, tile_server, monkeypatch):
    mon, tiles = make_tiles(tmp_path, tile_server, monkeypatch)
    mon.set_online(True, ["Street"])
    t = tiles.tile("online:osm", 2, 1, 1)
    assert t and base64.b64decode(t["data"]) == PNG and t["mime"] == "image/png"
    assert TileHandler.hits[0][2].startswith("DroneDog/0.1")          # identified User-Agent
    tiles.tile("online:osm", 2, 1, 1)                                  # fresh: served from disk
    assert len(TileHandler.hits) == 1
    # expire the cache entry -> conditional GET -> 304 keeps the cached bytes
    meta = next((tmp_path / "cache").rglob("*.json"))
    meta.write_text(meta.read_text().replace('"expires_at": ', '"expires_at": 0, "old": '))
    t2 = tiles.tile("online:osm", 2, 1, 1)
    assert t2 and base64.b64decode(t2["data"]) == PNG
    assert TileHandler.hits[-1][1] == '"v1"'
    st = mon.status(False)
    h = {x["host"]: x for x in st["hosts"]}["127.0.0.1"]
    assert h["requests"] == 2 and h["bytes_in"] > len(PNG) and h["purpose"].startswith("map tiles")
    assert st["online"] and st["state"] in ("online", "alert")
    # going offline stops serving online tiles even from cache
    mon.set_online(False)
    assert tiles.tile("online:osm", 2, 1, 1) is None
    tiles.close()


def test_404_is_counted_separately_and_negative_cached(tmp_path, tile_server, monkeypatch):
    mon, tiles = make_tiles(tmp_path, tile_server, monkeypatch)
    mon.set_online(True)
    for _ in range(3):
        assert tiles.tile("online:osm", 3, 1, 1) is None
    assert len(TileHandler.hits) == 1                 # asked once, then remembered
    h = mon.status(False)["hosts"][0]
    assert h["not_found"] == 1 and h["errors"] == 0


def test_out_of_range_tiles_are_not_requested(tmp_path, tile_server, monkeypatch):
    mon, tiles = make_tiles(tmp_path, tile_server, monkeypatch)
    mon.set_online(True)
    assert tiles.tile("online:osm", 25, 0, 0) is None
    assert tiles.tile("online:osm", 2, 9, 0) is None
    assert TileHandler.hits == []


def test_os_view_lists_this_process():
    st = NetMonitor().status(True)
    assert st["os_available"]
    assert st["os"]["processes"][0]["pid"] > 0
    assert st["disclosure"].startswith("Only map tiles are requested")


def test_online_toggle_is_logged_to_custody(tmp_path):
    api = MockApi(basemap_dir=tmp_path / "maps", speed=50)
    api.case_new({"name": "N", "examiner": "E"})
    first = api.list_custody()[-1]
    assert first["action"] == "network.online_off" and first["params"]["reason"] == "case.open"
    api.set_online(True)
    api.set_online(False)
    log = api.list_custody()
    assert [e["action"] for e in log[-2:]] == ["network.online_on", "network.online_off"]
    assert log[-2]["component"] == "ui.network" and len(log[-2]["params"]["sources"]) == 4
    assert api.verify_custody()["ok"]
    online = [b for b in api.basemaps() if b.get("online")]
    assert {b["id"] for b in online} == {"online:esri_imagery", "online:usgs_imagery", "online:usgs_topo", "online:osm"}
    assert not any(b["available"] for b in online)


def test_csp_locks_the_page():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    m = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html)
    assert m, "CSP meta missing"
    csp = m.group(1)
    for d in ("default-src 'self'", "connect-src 'self'", "script-src 'self'", "font-src 'self'"):
        assert d in csp
    assert "<script>" not in html               # no inline scripts (script-src 'self')
    assert "https:" not in csp and "*" not in csp
