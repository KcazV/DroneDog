"""dat_ref_impl must not raise on V3 DATs whose record layouts it does not know.

A plaintext V3 file from a newer platform (e.g. a decrypted Matrice 4 controller DAT)
can carry known type numbers with different payloads. That must produce warnings or an
empty track, never an exception (which marks the artifact "failed").
"""
from __future__ import annotations

import math
import struct

import pytest

from dronedog.adapters import dat_ref_impl as M


def _rec(rtype: int, tick: int, payload: bytes) -> bytes:
    ln = len(payload) + 12
    key = tick & 0xFF
    hdr = bytes([0x55, ln & 0xFF, (ln >> 8) & 0x03])
    body = hdr + bytes([M.crc8_dji(hdr)]) + struct.pack("<HI", rtype, tick) + bytes(b ^ key for b in payload)
    return body + struct.pack("<H", M.crc16_dji(body))


def _dat(records: bytes) -> bytes:
    h = bytearray(256)
    h[16:42] = b"BUILD Jul 10 2026 18:19:28"
    struct.pack_into("<I", h, 0x30, 4_500_000)
    h[242:252] = b"DJI_LOG_V3"
    return bytes(h) + records


@pytest.mark.parametrize("fast", [True, False])
def test_non_finite_positions_do_not_raise(fast):
    imu = struct.pack("<dd", math.inf, -math.inf) + bytes(104)
    home = struct.pack("<dd", 0.5, 0.6) + bytes(16)
    recs = b"".join(_rec(2048, 1000 + i * 450_000, imu) + _rec(13, 1001 + i * 450_000, home)
                    for i in range(20))
    res = M.DatDecode(_dat(recs), "2026-07-10_18-19-28_FLY001.DAT", header=None, fast=fast).run().parse_result()
    assert all(v is None for v in res["frames"]["lat"])
    assert any("not a valid coordinate" in w for w in res["warnings"])


@pytest.mark.parametrize("fast", [True, False])
def test_all_records_rejected_by_sequence_rules(fast):
    one = _rec(2048, 30_000_000, bytes(120))   # first tick > 22,000,000: DatCon jump rule drops it
    res = M.DatDecode(_dat(one), "FLY001.DAT", header=None, fast=fast).run().parse_result()
    assert res["frames"]["t_offset_ms"] == []
