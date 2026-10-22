"""Offline reverse geocoder (dronedog.analysis.geocode) against the vendored GeoNames data."""
import math
import threading

import pytest

from dronedog.analysis import geocode

pytestmark = pytest.mark.skipif(not geocode.available(),
                                reason="dronedog/data/places.npz not built (tools/make_places.py)")


def test_epfl_lausanne():
    r = geocode.locate(46.5214, 6.5673)
    assert r is not None
    assert r["name"] in ("Ecublens", "Lausanne")
    assert r["admin1"] == "Vaud" and r["admin1_code"] == "VD"
    assert r["country"] == "Switzerland" and r["country_code"] == "CH"
    assert r["label"] == f"{r['name']}, Vaud, Switzerland"
    assert r["distance_km"] < 5
    assert r["summary"].endswith(f"of {r['label']}")
    assert r["source"] == "GeoNames cities500 (CC BY 4.0)"


def test_boulder_colorado_label_has_county():
    r = geocode.locate(40.0150, -105.2705)
    assert r["name"] == "Boulder"
    assert r["admin2"] == "Boulder County"
    assert r["admin1"] == "Colorado" and r["admin1_code"] == "CO"
    assert r["country_code"] == "US"
    assert r["label"] == "Boulder, Boulder County, Colorado, United States"
    assert r["population"] > 50_000


def test_washington_dc_no_duplicate_parts():
    r = geocode.locate(38.8977, -77.0365)
    assert r["name"] == "Washington"
    assert r["admin1"] == "District of Columbia"
    assert r["label"] == "Washington, District of Columbia, United States"
    assert r["distance_km"] < 2


def test_distance_and_bearing_from_place_to_point():
    base = geocode.nearest_place(40.0150, -105.2705)
    # ~2 km due north of the Boulder place point: still Boulder-ish, bearing ~0
    north = geocode.nearest_place(base["lat"] + 0.018, base["lon"], max_km=50)
    assert north["name"] == "Boulder" or north["distance_km"] < 2.1
    if north["name"] == "Boulder":
        assert north["bearing_deg"] in range(0, 5) or north["bearing_deg"] > 355
        assert 1.8 <= north["distance_km"] <= 2.2
        assert " N of Boulder" in geocode.locate(base["lat"] + 0.018, base["lon"])["summary"]
    # result keys and rounding
    assert set(base) >= {"name", "admin2", "admin1", "admin1_code", "country", "country_code",
                         "population", "distance_km", "bearing_deg", "lat", "lon", "source"}
    assert base["distance_km"] == round(base["distance_km"], 1)


@pytest.mark.parametrize("lat,lon", [
    (0, 0), (0.0, 0.0), (None, None), (None, 6.5), (46.5, None),
    (math.nan, 6.5), (46.5, math.nan), (math.inf, 0.5), (91.0, 6.5), (-90.5, 6.5),
    (46.5, 181.0), ("abc", 6.5),
])
def test_invalid_coordinates_return_none(lat, lon):
    assert geocode.nearest_place(lat, lon) is None
    assert geocode.locate(lat, lon) is None


def test_open_ocean_is_none_with_default_radius():
    assert geocode.locate(-30.0, -140.0) is None      # South Pacific, > 500 km from land
    assert geocode.locate(35.0, -40.0) is None        # mid North Atlantic


def test_max_km_bounds_result():
    near = geocode.nearest_place(46.5214, 6.5673, max_km=50)
    assert geocode.nearest_place(46.5214, 6.5673, max_km=near["distance_km"] / 2) is None
    assert geocode.nearest_place(46.5214, 6.5673, max_km=0) is None
    # a large radius finds something for the open-ocean point
    assert geocode.nearest_place(-30.0, -140.0, max_km=3000) is not None


def test_antimeridian_wraps():
    # Fiji straddles 180 deg: the nearest place may be on either side of the line
    east = geocode.nearest_place(-16.80, 179.999, max_km=100)
    west = geocode.nearest_place(-16.80, -179.999, max_km=100)
    assert east is not None and west is not None
    assert east["country_code"] == west["country_code"] == "FJ"
    assert abs(east["distance_km"] - west["distance_km"]) < 1.0


def test_threadsafe_lazy_load():
    geocode._db = None
    out = []
    ts = [threading.Thread(target=lambda: out.append(geocode.locate(40.015, -105.2705)["name"]))
          for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out == ["Boulder"] * 8


def test_data_info_attribution():
    info = geocode.data_info()
    assert info["licence"] == "CC BY 4.0"
    assert info["attribution"] == "Contains GeoNames data, CC BY 4.0, geonames.org"
    assert info["count"] > 100_000
    assert geocode.data_path().stat().st_size <= 12 * 1024 * 1024
