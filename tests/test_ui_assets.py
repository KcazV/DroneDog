"""UI assets: offline-only (no remote references), vendored files match VENDOR.md, maps, decimation."""
import base64
import hashlib
import re
import sqlite3
from pathlib import Path

import pytest

from dronedog.ui.decimate import decimate
from dronedog.ui.mbtiles import MapPackError, MBTilesStore, xyz_to_tms_row

WEB = Path(__file__).resolve().parents[1] / "dronedog" / "ui" / "web"
REMOTE = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//|url\(\s*["']?\s*(?:https?:)?//|import\s*\(?\s*["'](?:https?:)?//|fetch\(\s*["']https?:""", re.I)


def own_files():
    return [p for p in WEB.rglob("*") if p.is_file() and "vendor" not in p.parts and p.suffix in (".html", ".js", ".css")]


def test_no_remote_references_in_shipped_web_files():
    files = own_files() + [p for p in (WEB / "vendor").rglob("*") if p.suffix in (".css", ".html")]
    assert files
    bad = [f"{p}: {m.group(0)}" for p in files for m in REMOTE.finditer(p.read_text(encoding="utf-8", errors="ignore"))]
    assert not bad, bad


def test_vendor_manifest_hashes_match():
    md = (WEB / "vendor" / "VENDOR.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([^`]+)` \|.*?\| (\d+) \| `([0-9a-f]{64})` \|", md, re.M)
    listed = {r[0] for r in rows}
    on_disk = {p.relative_to(WEB / "vendor").as_posix() for p in (WEB / "vendor").rglob("*") if p.is_file() and p.name != "VENDOR.md" and not p.name.startswith(".")}
    assert listed == on_disk
    for rel, size, sha in rows:
        data = (WEB / "vendor" / rel).read_bytes()
        assert len(data) == int(size), rel
        assert hashlib.sha256(data).hexdigest() == sha, rel


def test_basemap_budget_and_index_references():
    ne = sum(p.stat().st_size for p in (WEB / "vendor" / "naturalearth").glob("*.geojson"))
    assert ne < 15_000_000
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:src|href)="([^"#?]+)"', html):
        assert (WEB / ref).exists(), ref


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def make_pack(path, fmt="png"):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    con.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
    con.executemany("INSERT INTO metadata VALUES (?,?)", [("name", "Test pack"), ("format", fmt), ("minzoom", "0"),
                                                         ("maxzoom", "2"), ("bounds", "-84,42,-83,43")])
    # XYZ (z=2, x=1, y=0) is TMS row 3
    con.execute("INSERT INTO tiles VALUES (2, 1, 3, ?)", (PNG,))
    con.commit()
    con.close()


def test_mbtiles_import_list_and_tms_flip(tmp_path):
    src = tmp_path / "src.mbtiles"
    make_pack(src)
    store = MBTilesStore(tmp_path / "maps")
    meta = store.import_pack(src)
    assert meta["ok"] and meta["format"] == "png" and meta["maxzoom"] == 2
    packs = store.list()
    assert [p["id"] for p in packs] == [meta["id"]]
    assert xyz_to_tms_row(2, 0) == 3
    t = store.tile(meta["id"], 2, 1, 0)
    assert t and t["mime"] == "image/png" and base64.b64decode(t["data"]) == PNG
    assert store.tile(meta["id"], 2, 1, 3) is None        # not flipped twice
    assert store.tile(meta["id"], 2, 9, 9) is None         # out of range
    store.close()


def test_mbtiles_rejects_vector_and_garbage(tmp_path):
    vec = tmp_path / "v.mbtiles"
    make_pack(vec, "pbf")
    with pytest.raises(MapPackError):
        MBTilesStore(tmp_path / "m").import_pack(vec)
    junk = tmp_path / "j.mbtiles"
    junk.write_bytes(b"not sqlite at all" * 100)
    with pytest.raises(MapPackError):
        MBTilesStore(tmp_path / "m").import_pack(junk)


def test_decimate_keeps_ends_and_spikes():
    n = 100_000
    alt = [10.0] * n
    alt[54_321] = 500.0                        # single-frame spike
    frames = {"t_offset_ms": list(range(n)), "alt_rel_m": alt}
    out, info = decimate(frames, 2000)
    assert info["returned"] <= 2000 and info["total"] == n
    assert out["t_offset_ms"][0] == 0 and out["t_offset_ms"][-1] == n - 1
    assert 500.0 in out["alt_rel_m"]
    same, info2 = decimate(frames, 0)
    assert same is frames and info2["method"] == "none"
