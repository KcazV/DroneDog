"""Identifier extraction from flight-controller text-log lines."""
from dronedog.analysis.summary import identifiers


def test_uuid_as_logged_and_geo_user_id():
    # Synthetic values in the logged line format.
    ev = [{"message": "3805 [L-DBG1][set uuid]:TEST_UID_0001", "t_offset_ms": 1},
          {"message": "3962 [L-GEO][INFO]user id updated 0=>1000000000000000001", "t_offset_ms": 2},
          {"message": "g_config.flying_limit.height => 500"}]
    got = {(i["group"], i["label"]): i["value"] for i in identifiers({}, ev)}
    assert got == {("Aircraft", "UUID set by flight controller (as logged)"): "TEST_UID_0001",
                   ("Account", "User ID (GEO module, as logged)"): "1000000000000000001"}
