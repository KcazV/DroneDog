#!/usr/bin/env python3
"""DroneDog DAT decoder (dat_ref_py adapter implementation).

DroneDog reference decoder for DJI flight-controller .DAT logs.

Build-time reference implementation of docs/DAT_FORMAT.md. It exists so the
Rust port in core/src/dat/ has an executable spec and test vectors. Standard
library only (numpy is not required).

Scope (HANDOFF.md section 2): plaintext DATs whose record payloads are only
obfuscated with the publicly documented per-record XOR (key = low byte of the
record tick). Encrypted containers (e.g. "LOGH" files) are recognised and
reported, never decrypted.

Usage:
    python tools/dat_ref.py FLY123.DAT --csv out.csv       # C3 frame columns
    python tools/dat_ref.py FLY123.DAT --records            # per-record-type stats
    python tools/dat_ref.py FLY123.DAT --json result.json   # C1 ParseResult
    python tools/dat_ref.py FLY123.DAT --detect             # C1 detect() dict
    python tools/dat_ref.py FLY123.DAT --text events.txt    # 0x8000 event log

Section numbers in comments (e.g. "spec 3.2") refer to docs/DAT_FORMAT.md.
"""

from __future__ import annotations

import argparse
import binascii
import csv
import datetime as _dt
import io
import json
import math
import os
import re
import struct
import sys
import zlib
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

DECODER_VERSION = "0.2.0"

# --------------------------------------------------------------------------
# spec 3.3  Checksums
# --------------------------------------------------------------------------
# Record CRC: CRC-16, reflected polynomial 0x8408 (CCITT 0x1021 reflected),
# init/seed 0x3692, no final XOR, over every byte of the record except the
# two trailing CRC bytes, stored little-endian.
# Header CRC: CRC-8, reflected polynomial 0x8C (Dallas/Maxim 0x31 reflected),
# seed 0x77, over the first 3 bytes of the record, stored at byte 3.

CRC16_SEED = 0x3692
CRC8_SEED = 0x77

_REV8 = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def _rev16(v: int) -> int:
    return (_REV8[v & 0xFF] << 8) | _REV8[v >> 8]


_CRC16_SEED_REV = _rev16(CRC16_SEED)


def crc16_dji(data: bytes, seed: int = CRC16_SEED) -> int:
    """Reflected CRC-16/0x8408 with DJI seed.

    Computed through binascii.crc_hqx (MSB-first CRC-16/0x1021) by the
    standard identity: reflect every input byte, reflect the init value, run
    the non-reflected CRC, reflect the 16-bit result.
    """
    init = _CRC16_SEED_REV if seed == CRC16_SEED else _rev16(seed)
    return _rev16(binascii.crc_hqx(data.translate(_REV8), init))


def _make_crc8_table() -> List[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x8C if c & 1 else c >> 1
        table.append(c)
    return table


_CRC8_TABLE = _make_crc8_table()


def crc8_dji(data: bytes, seed: int = CRC8_SEED) -> int:
    v = seed
    for b in data:
        v = _CRC8_TABLE[(b ^ v) & 0xFF]
    return v


def crc16_dji_slow(data: bytes, seed: int = CRC16_SEED) -> int:
    """Table-driven reference form (what the Rust port should implement)."""
    v = seed
    for b in data:
        x = (b ^ v) & 0xFF
        c = x
        for _ in range(8):
            c = (c >> 1) ^ 0x8408 if c & 1 else c >> 1
        v = (v >> 8) ^ c
    return v


# --------------------------------------------------------------------------
# spec 2.2  Product codes (header byte 0) and timebase
# --------------------------------------------------------------------------
# code -> (model, family, nominal tick Hz used by DatCon 3.5, cells, motors)
# Codes 11..27 and 5/6 come from DatCon 3.5 DatHeader.java; the product names
# agree with the flight-controller product-type enum in
# dji-firmware-tools dji-p3-flyrec-proto.lua (REC_OSD_GENERAL_PRODUCT_TYPE).
#
# Court-honesty rule: a model name is emitted only when the file proves it, i.e. its
# header product code is in this published table, or (V1) its boot text names a known
# flight-controller board. Code 6 is NOT proof of a model: Phantom 3, Inspire 1 and the
# A3-family boards (e.g. Agras MG-1S, board "zd900", 4.125 MHz clock) all write code 6.
PRODUCTS: Dict[int, Tuple[str, str, float, int, int]] = {
    5: ("Matrice 100", "M100", 85_000_000.0, 6, 4),
    11: ("Phantom 4", "P4", 4_500_000.0, 4, 4),
    14: ("Matrice 600", "M600", 4_500_000.0, 6, 6),
    16: ("Mavic Pro", "MavicPro", 4_500_000.0, 3, 4),
    17: ("Inspire 2", "I2", 4_500_000.0, 6, 4),
    18: ("Phantom 4 Pro", "P4P", 4_500_000.0, 4, 4),
    20: ("Spreading Wings S900", "S900", 4_500_000.0, 6, 6),
    21: ("Spark", "Spark", 4_500_000.0, 3, 4),
    23: ("Matrice 600 Pro", "M600", 4_500_000.0, 6, 6),
    24: ("Mavic Air", "MavicAir", 4_500_000.0, 3, 4),
    25: ("Matrice 200 series", "M200", 4_500_000.0, 6, 4),
    27: ("Phantom 4 Advanced", "P4A", 4_500_000.0, 4, 4),
}

# Codes seen in the corpus whose model comes only from *external* metadata (dataset
# description), not from the file and not from a published table. They are reported as
# hints (details.x.model_hint), never as aircraft_model.
PRODUCT_HINTS: Dict[int, Tuple[str, str, float, int, int]] = {
    6: ("Phantom 3 / Inspire 1 family (DatCon's assumption for code 6); also written by "
        "A3-family flight controllers", "V1CODE6", 600.0, 4, 4),
    35: ("observed on Phantom 4 Pro units (DataverseNO AFDYLS EPFL flights, FC build "
         "Apr 2020)", "P4P2?", 4_687_500.0, 4, 4),
    41: ("observed on a Mavic 2 Zoom (CFReDS DF068 iOS backup)", "Mavic2?", 3_840_000.0, 4, 4),
}

# V1 boot text "Board:" names that identify a model (DatCon DatFileV1.java:395-413).
V1_BOARDS = {"wm320v2": "Phantom 3 Advanced/Professional", "wm610": "Inspire 1",
             "wm321": "Phantom 3 Standard", "wm328": "Phantom 3 Standard"}


def unidentified_model(era: str, code: Optional[int]) -> str:
    return "Unidentified DJI (%s-era DAT%s)" % (
        era, ", product code %d" % code if code is not None else "")

# Record types (spec 5). Name strings follow the recDefs/"FromViewer" names.
REC_NAMES: Dict[int, str] = {
    0: "controller_v1", 1: "imu_atti_v1", 2: "imu_tail", 3: "imu_ex", 4: "compass",
    5: "gps_glns_v1", 7: "imu_data", 12: "osd_general", 13: "osd_home",
    17: "battery_info_v1", 18: "smart_battery_info_v1", 43: "tablet_loc",
    1000: "controller", 1001: "aircraft_condition", 1002: "serial_api_inputs",
    1004: "go_home_info", 1121: "svo_avoid", 1700: "rc_debug_info",
    1710: "battery_info", 1711: "battery_status", 1712: "smart_battery_info",
    2048: "imu_atti_0", 2049: "imu_atti_1", 2050: "imu_atti_2",
    2064: "imu_ex_0", 2065: "imu_ex_1", 2066: "imu_ex_2",
    2096: "gps_0", 2097: "gps_1", 2098: "gps_2",
    2256: "mag_0", 2257: "mag_1",
    5000: "battery_dynamic_0", 5001: "battery_dynamic_1", 5002: "battery_dynamic_2",
    10090: "esc_data", 10100: "airspeed_comp",
    52721: "esc_data_v1",
    0x8000: "fly_log_text", 0xFF00: "sd_logs", 0xFFFA: "drv_log",
    0xFFFC: "module_name", 0xFFFD: "rec_defs", 0xFFFE: "svn_info", 0xFFFF: "sys_cfg",
}
TEXT_TYPES = {0x8000, 0xFF00, 0xFFFA, 0xFFFC, 0xFFFD, 0xFFFE, 0xFFFF}

# OSD general flight-controller state (byte 30 & 0x7F). spec 5.1
FLYC_STATE = {
    0: "Manual", 1: "Atti", 2: "Atti_CL", 3: "Atti_Hover", 4: "Hover", 5: "GPS_Blake",
    6: "GPS_Atti", 7: "GPS_CL", 8: "GPS_HomeLock", 9: "GPS_HotPoint",
    10: "AssitedTakeoff", 11: "AutoTakeoff", 12: "AutoLanding", 13: "AttiLangding",
    14: "NaviGo", 15: "GoHome", 16: "ClickGo", 17: "Joystick", 23: "Atti_Limited",
    24: "GPS_Atti_Limited", 25: "NaviMissionFollow", 26: "NaviSubMode_Tracking",
    27: "NaviSubMode_Pointing", 28: "PANO", 29: "Farming", 30: "FPV", 31: "SPORT",
    32: "NOVICE", 33: "FORCE_LANDING", 35: "TERRAIN_TRACKING", 36: "NAVI_ADV_GOHOME",
    37: "NAVI_ADV_LANDING", 38: "TRIPOD_GPS", 39: "TRACK_HEADLOCK", 41: "ASST_TAKEOFF",
    43: "GENTLE_GPS", 100: "OTHER",
}
FLIGHT_ACTION = {
    0: "NONE", 1: "WARNING_POWER_GOHOME", 2: "WARNING_POWER_LANDING",
    3: "SMART_POWER_GOHOME", 4: "SMART_POWER_LANDING", 5: "LOW_VOLTAGE_LANDING",
    6: "LOW_VOLTAGE_GOHOME", 7: "SERIOUS_LOW_VOLTAGE_LANDING", 8: "RC_ONEKEY_GOHOME",
    9: "RC_ASSISTANT_TAKEOFF", 10: "RC_AUTO_TAKEOFF", 11: "RC_AUTO_LANDING",
    12: "APP_AUTO_GOHOME", 13: "APP_AUTO_LANDING", 14: "APP_AUTO_TAKEOFF",
    15: "OUTOF_CONTROL_GOHOME", 16: "API_AUTO_TAKEOFF", 17: "API_AUTO_LANDING",
    18: "API_AUTO_GOHOME", 19: "AVOID_GROUND_LANDING", 20: "AIRPORT_AVOID_LANDING",
    21: "TOO_CLOSE_GOHOME_LANDING", 22: "TOO_FAR_GOHOME_LANDING", 23: "APP_WP_MISSION",
    24: "WP_AUTO_TAKEOFF", 25: "GOHOME_AVOID", 26: "GOHOME_FINISH",
    27: "VERT_LOW_LIMIT_LANDING", 28: "BATTERY_FORCE_LANDING", 29: "MC_PROTECT_GOHOME",
}


# --------------------------------------------------------------------------
# Small helpers emulating Java float32 arithmetic (for DatCon parity)
# --------------------------------------------------------------------------
_F32 = struct.Struct("<f")


def f32(x: float) -> float:
    """Round a Python float (IEEE double) to the nearest IEEE single."""
    try:
        return _F32.unpack(_F32.pack(x))[0]
    except (OverflowError, struct.error):
        return float("inf") if x > 0 else float("-inf")


def _u8(b, o): return b[o]
def _i8(b, o): return struct.unpack_from("<b", b, o)[0]
def _u16(b, o): return struct.unpack_from("<H", b, o)[0]
def _i16(b, o): return struct.unpack_from("<h", b, o)[0]
def _u32(b, o): return struct.unpack_from("<I", b, o)[0]
def _i32(b, o): return struct.unpack_from("<i", b, o)[0]
def _fl(b, o): return struct.unpack_from("<f", b, o)[0]
def _db(b, o): return struct.unpack_from("<d", b, o)[0]


def _ascii(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("ascii", "replace").strip()


# --------------------------------------------------------------------------
# spec 2  File-level detection
# --------------------------------------------------------------------------
class DatHeader:
    def __init__(self) -> None:
        self.kind = "unknown"          # dat_v1 | dat_v3 | dji_assistant_export |
        #                                logh_encrypted | dat_headerless | unknown
        self.encrypted: Optional[bool] = None
        self.start_of_records = 0
        self.product_code: Optional[int] = None
        self.model: Optional[str] = None
        self.era: Optional[str] = None             # "V1" | "V3"
        self.model_basis: Optional[str] = None     # why `model` is stated (None = not proven)
        self.model_hint: Optional[str] = None      # external, unproven association
        self.family: Optional[str] = None
        self.build: Optional[str] = None           # "BUILD Apr  8 2016 18:22:32"
        self.header_tick_hz: Optional[int] = None  # u32 @ 0x30
        self.clock_hz: float = 4_500_000.0         # rate used to convert ticks
        self.serial_obf: Optional[str] = None      # V3 0x3A..0x47 XOR 0x55
        self.hash_obf: Optional[str] = None        # V3 0x4A..0x69 XOR 0x55
        self.cells = 4
        self.motors = 4
        self.reasons: List[str] = []
        self.confidence = 0.0
        self.version: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def parse_header(buf: bytes, name: str = "") -> DatHeader:
    h = DatHeader()
    n = len(buf)
    if n >= 2 and buf[0] == 0x78 and buf[1] in (0x01, 0x5E, 0x9C, 0xDA):
        h.kind = "dji_assistant_export"
        h.confidence = 0.8
        h.reasons.append("starts with zlib stream header 78 %02X (DJI Assistant 2 "
                         "export container, spec 2.4)" % buf[1])
        return h
    if buf[:4] == b"LOGH":
        h.kind = "logh_encrypted"
        h.encrypted = True
        h.confidence = 0.95
        h.reasons.append("magic 'LOGH' at offset 0: encrypted DAT container "
                         "(spec 7); records not readable without a decryptor")
        if n >= 16:
            h.reasons.append("LOGH header data offset field (u64 @8) = %d"
                             % struct.unpack_from("<Q", buf, 8)[0])
        return h
    if n >= 21 and buf[16:21] == b"BUILD":
        v3 = n >= 252 and buf[242:252] == b"DJI_LOG_V3"
        h.kind = "dat_v3" if v3 else "dat_v1"
        h.version = 3 if v3 else 1
        h.start_of_records = 256 if v3 else 128
        h.encrypted = False
        h.confidence = 0.95
        h.product_code = buf[0]
        h.build = _ascii(buf[16:48])
        h.reasons.append("'BUILD' at offset 16 (%s)" % h.build)
        h.reasons.append("'DJI_LOG_V3' at offset 242 -> records at 256" if v3
                         else "no 'DJI_LOG_V3' at 242 -> V1 layout, records at 128")
        if n >= 0x34:
            h.header_tick_hz = _u32(buf, 0x30)
        h.era = "V3" if v3 else "V1"
        prod = PRODUCTS.get(h.product_code)
        hint = PRODUCT_HINTS.get(h.product_code)
        if prod:
            h.model, h.family, nominal, h.cells, h.motors = prod
            h.clock_hz = nominal
            h.model_basis = ("header product code %d (DatCon 3.5 DatHeader.java table)"
                             % h.product_code)
            h.reasons.append("product code %d -> %s" % (h.product_code, h.model))
        else:
            h.model = unidentified_model(h.era, h.product_code)
            h.family = "UNKNOWN"
            h.clock_hz = 600.0 if not v3 else 4_500_000.0
            h.model_basis = None
            if hint:
                h.model_hint = hint[0]
                h.family, h.clock_hz, h.cells, h.motors = hint[1], hint[2], hint[3], hint[4]
                h.reasons.append("product code %d does not identify a model by itself (%s)"
                                 % (h.product_code, hint[0]))
            else:
                h.reasons.append("product code %d not in the published table"
                                 % h.product_code)
                h.confidence = 0.7
        if h.header_tick_hz and h.header_tick_hz not in (0, 0xFFFFFFFF):
            if abs(h.header_tick_hz - h.clock_hz) > 1:
                h.reasons.append("header tick rate %d Hz differs from nominal %.0f; "
                                 "using header value" % (h.header_tick_hz, h.clock_hz))
            h.clock_hz = float(h.header_tick_hz)
        if v3 and n >= 0x6A:
            sn = bytes(b ^ 0x55 for b in buf[0x3A:0x48])
            hx = bytes(b ^ 0x55 for b in buf[0x4A:0x6A])
            s1, s2 = _ascii(sn), _ascii(hx)
            if s1 and all(32 < ord(c) < 127 for c in s1):
                h.serial_obf = s1
            if s2 and all(32 < ord(c) < 127 for c in s2):
                h.hash_obf = s2
        return h
    # Headerless: carved fragment or a stream that starts with records.
    found = _count_valid_records(buf, 0, limit=64)
    if found >= 8:
        h.kind = "dat_headerless"
        h.encrypted = False
        h.start_of_records = 0
        h.confidence = 0.6
        h.clock_hz = 4_500_000.0
        h.reasons.append("no BUILD header, but %d CRC-valid 0x55 records found in "
                         "the first bytes (fragment or stripped header)" % found)
        return h
    h.reasons.append("no known magic (BUILD@16, LOGH@0, zlib@0) and no 0x55 record "
                     "stream")
    return h


def _count_valid_records(buf: bytes, pos: int, limit: int = 64) -> int:
    count, scanned = 0, 0
    n = len(buf)
    while pos < n - 12 and scanned < 65536 and count < limit:
        if buf[pos] != 0x55:
            pos += 1
            scanned += 1
            continue
        ln = _u16(buf, pos + 1) & 0x3FF
        if ln >= 12 and pos + ln <= n and crc8_dji(buf[pos:pos + 3]) == buf[pos + 3] \
                and crc16_dji(buf[pos:pos + ln - 2]) == _u16(buf, pos + ln - 2):
            count += 1
            pos += ln
        else:
            pos += 1
            scanned += 1
    return count


# --------------------------------------------------------------------------
# spec 2.4  DJI Assistant 2 export container (zlib, 283-byte entry headers)
# --------------------------------------------------------------------------
ASSIST_ENTRY_HDR = 283


def assistant_entries(buf: bytes) -> List[Dict[str, Any]]:
    """Inflate an Assistant export and return its entries (name, data).

    The inflated stream is a sequence of entries; each entry is a 283-byte
    header (u32 LE data length @1, NUL-terminated name @7; layout from DatCon
    DJIAssistantFile.java) followed by that many data bytes. Entry data is
    identified by content (sniffing for a DAT header), not by the name.
    """
    try:
        d = zlib.decompressobj()
        raw = d.decompress(buf)
        raw += d.flush()
    except zlib.error as e:
        return [{"error": "zlib: %s" % e}]
    out, pos = [], 0
    while pos + ASSIST_ENTRY_HDR <= len(raw):
        hdr = raw[pos:pos + ASSIST_ENTRY_HDR]
        ln = _u32(hdr, 1)
        name = _ascii(hdr[7:7 + 255])
        data = raw[pos + ASSIST_ENTRY_HDR:pos + ASSIST_ENTRY_HDR + ln]
        out.append({"marker": hdr[0], "length": ln, "name": name,
                    "offset_inflated": pos, "data": data,
                    "truncated": len(data) < ln})
        pos += ASSIST_ENTRY_HDR + ln
        if ln == 0 and not name:
            break
    return out


# --------------------------------------------------------------------------
# spec 3  Record framing
# --------------------------------------------------------------------------
class Rec:
    __slots__ = ("offset", "length", "type", "tick_raw", "tick", "payload", "b2")

    def __init__(self, offset, length, rtype, tick_raw, tick, payload, b2):
        self.offset = offset
        self.length = length
        self.type = rtype
        self.tick_raw = tick_raw
        self.tick = tick
        self.payload = payload
        self.b2 = b2


class FrameStats:
    def __init__(self) -> None:
        self.records_ok = 0
        self.crc16_fail = 0
        self.crc8_fail = 0
        self.bad_length = 0
        self.resyncs = 0
        self.skipped_bytes = 0
        self.zero_pad_bytes = 0
        self.truncated_tail_bytes = 0
        self.protover_nonzero = 0
        self.len_over_255 = 0
        self.tick_rollovers = 0
        self.first_offset: Optional[int] = None
        self.last_end = 0

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def iter_records(buf: bytes, start: int, stats: FrameStats,
                 check_hdr_crc: bool = True) -> Iterator[Rec]:
    """Walk 0x55-framed records from `start`, resyncing on any damage.

    A record is accepted only if: byte0 == 0x55, 12 <= length <= remaining,
    header CRC-8 matches (unless check_hdr_crc is False) and the trailing
    CRC-16 matches. On failure the scan resumes at the next 0x55 after the
    failed start byte. Runs of 0x00 between records are padding.
    Payloads are returned de-obfuscated (XOR with tick & 0xFF, spec 4).
    """
    n = len(buf)
    pos = start
    tick_hi = 0          # rollover offset (spec 6.2)
    last_raw = 0
    in_resync = False
    find = buf.find
    while True:
        if pos >= n:
            break
        c = buf[pos]
        if c != 0x55:
            if c == 0 and not in_resync:
                z = pos
                while pos < n and buf[pos] == 0:
                    pos += 1
                stats.zero_pad_bytes += pos - z
                continue
            nxt = find(b"\x55", pos + 1)
            if nxt < 0:
                stats.skipped_bytes += n - pos
                break
            stats.skipped_bytes += nxt - pos
            pos = nxt
            continue
        if pos + 12 > n:
            stats.truncated_tail_bytes += n - pos
            break
        w = buf[pos + 1] | (buf[pos + 2] << 8)
        ln = w & 0x3FF
        ok = True
        if ln < 12:
            stats.bad_length += 1
            ok = False
        elif pos + ln > n:
            # could be a damaged length or a truncated final record
            if check_hdr_crc and crc8_dji(buf[pos:pos + 3]) == buf[pos + 3]:
                stats.truncated_tail_bytes += n - pos
                break
            stats.bad_length += 1
            ok = False
        elif check_hdr_crc and crc8_dji(buf[pos:pos + 3]) != buf[pos + 3]:
            stats.crc8_fail += 1
            ok = False
        elif crc16_dji(buf[pos:pos + ln - 2]) != (buf[pos + ln - 2] | (buf[pos + ln - 1] << 8)):
            stats.crc16_fail += 1
            ok = False
        if not ok:
            if not in_resync:
                stats.resyncs += 1
            in_resync = True
            nxt = find(b"\x55", pos + 1)
            if nxt < 0:
                stats.skipped_bytes += n - pos
                break
            stats.skipped_bytes += nxt - pos
            pos = nxt
            continue
        in_resync = False
        b2 = buf[pos + 2]
        if b2 >> 2:
            stats.protover_nonzero += 1
        if ln > 255:
            stats.len_over_255 += 1
        rtype = buf[pos + 4] | (buf[pos + 5] << 8)
        traw = struct.unpack_from("<I", buf, pos + 6)[0]
        # spec 6.2 rollover: same thresholds as DatCon DatFileV3
        if last_raw > 4_292_717_296 and traw < 2_225_000:
            tick_hi += 1 << 32
            stats.tick_rollovers += 1
        last_raw = traw
        key = traw & 0xFF
        raw_payload = buf[pos + 10:pos + ln - 2]
        payload = raw_payload.translate(_XOR_TABLES[key]) if key else raw_payload
        if stats.first_offset is None:
            stats.first_offset = pos
        stats.records_ok += 1
        stats.last_end = pos + ln
        yield Rec(pos, ln, rtype, traw, traw + tick_hi, payload, b2)
        pos += ln


_XOR_TABLES = [bytes(i ^ k for i in range(256)) for k in range(256)]


# --------------------------------------------------------------------------
# spec 5  Payload decoders (engineered layouts, DatCon parity)
# --------------------------------------------------------------------------
Decoder = Callable[[bytes], Dict[str, Any]]


def dec_osd_general(p: bytes) -> Dict[str, Any]:
    """Type 12, 50..55 bytes (spec 5.1)."""
    cs = _u32(p, 32)
    m1 = p[30]
    return {
        "lon_deg": math.degrees(_db(p, 0)),
        "lat_deg": math.degrees(_db(p, 8)),
        "height_m": f32(_i16(p, 16) / 10.0),
        "vgx_ms": _i16(p, 18) / 10.0,
        "vgy_ms": _i16(p, 20) / 10.0,
        "vgz_ms": _i16(p, 22) / 10.0,
        "pitch_deg": f32(_i16(p, 24) / 10.0),
        "roll_deg": f32(_i16(p, 26) / 10.0),
        "yaw_deg": f32(_i16(p, 28) / 10.0),
        "flyc_state": m1 & 0x7F,
        "rc_lost": bool(m1 & 0x80),
        "flyc_command": p[31] & 0x7F,
        "controller_state": cs,
        "on_ground": bool(cs & 0x02),
        "in_air": bool(cs & 0x04),
        "motor_on": bool(cs & 0x08),
        "gohome_state": (cs >> 5) & 0x7,
        "vision_used": bool(cs & 0x100),
        "voltage_warning": (cs >> 9) & 0x3,
        "rc_mode_channel": (cs >> 13) & 0x3,
        "gps_used": bool(cs & 0x8000),
        "compass_error": bool(cs & 0x10000),
        "wave_error": bool(cs & 0x20000),
        "wave_work": bool(cs & 0x10),
        "gps_level": (cs >> 18) & 0xF,
        "gps_nums": p[36],
        "flight_action": p[37],
        "start_fail_reason": p[38],
        "gps_state": p[39] & 0x07,
        "batt_remain": p[40],
        "vps_height_m": f32((p[41] & 0xFF) / 10.0),
        "motor_startup_time_ds": _i16(p, 42),   # 0.1 s units
        "product_type": p[48] if len(p) > 48 else None,
    }


def dec_osd_home(p: bytes) -> Dict[str, Any]:
    """Type 13 (spec 5.2)."""
    lon_r, lat_r = _db(p, 0), _db(p, 8)
    return {
        "home_lon_rad": lon_r, "home_lat_rad": lat_r,
        "home_lon_deg": math.degrees(lon_r), "home_lat_deg": math.degrees(lat_r),
        "home_alt_m": _fl(p, 16),
        "home_state": _u16(p, 20),
        "go_home_height_m": _i16(p, 22),
        "home_valid_datcon": lon_r < 100.0 and lat_r < 100.0,
    }


def quat_to_euler_datcon(w: float, x: float, y: float, z: float) -> Tuple[float, float, float]:
    """(pitch, roll, yaw) in degrees, DatCon Quaternion.toEuler convention."""
    sqw, sqx, sqy, sqz = w * w, x * x, y * y, z * z
    unit = sqx + sqy + sqz + sqw
    test = w * x + y * z
    if unit == 0.0:
        return (0.0, 0.0, 0.0)
    if test > 0.499 * unit:
        yaw = 2 * math.atan2(y, w)
        pitch = math.pi / 2
        roll = 0.0
    elif test < -0.499 * unit:
        yaw = -2 * math.atan2(y, w)
        pitch = -math.pi / 2
        roll = 0.0
    else:
        yaw = math.atan2(2.0 * (w * z - x * y), 1.0 - 2.0 * (sqz + sqx))
        roll = math.asin(max(-1.0, min(1.0, 2.0 * test / unit)))
        pitch = math.atan2(2.0 * (w * y - x * z), 1.0 - 2.0 * (sqy + sqx))
    return (math.degrees(pitch), math.degrees(roll), math.degrees(yaw))


def dec_imu_atti(p: bytes) -> Dict[str, Any]:
    """Types 2048/2049/2050 (V3) and 1 (V1), 120 bytes (spec 5.3)."""
    qw, qx, qy, qz = _fl(p, 48), _fl(p, 52), _fl(p, 56), _fl(p, 60)
    pitch, roll, yaw = quat_to_euler_datcon(qw, qx, qy, qz)
    vn, ve, vd = _fl(p, 76), _fl(p, 80), _fl(p, 84)
    return {
        "lon_rad": _db(p, 0), "lat_rad": _db(p, 8),
        "lon_deg": math.degrees(_db(p, 0)), "lat_deg": math.degrees(_db(p, 8)),
        "baro_raw_m": _fl(p, 16),
        "acc_x_g": _fl(p, 20), "acc_y_g": _fl(p, 24), "acc_z_g": _fl(p, 28),
        "gyro_x_dps": f32(math.degrees(_fl(p, 32))),
        "gyro_y_dps": f32(math.degrees(_fl(p, 36))),
        "gyro_z_dps": f32(math.degrees(_fl(p, 40))),
        "baro_smooth_m": _fl(p, 44),
        "qw": qw, "qx": qx, "qy": qy, "qz": qz,
        "pitch_deg": pitch, "roll_deg": roll, "yaw_deg": yaw,
        "vel_n": vn, "vel_e": ve, "vel_d": vd,
        "mag_x": _i16(p, 100), "mag_y": _i16(p, 102), "mag_z": _i16(p, 104),
        "imu_temp_c": _i16(p, 106) / 100.0,
        "num_sats": _i8(p, 116),
    }


def dec_gps(p: bytes) -> Dict[str, Any]:
    """Types 2096/2097/2098 (V3) and 5 (V1), 66..72 bytes (spec 5.4)."""
    out = {
        "gps_date": _u32(p, 0),          # yyyymmdd
        "gps_time": _u32(p, 4),          # hhmmss (UTC)
        "gps_lon_deg": _i32(p, 8) / 1.0e7,
        "gps_lat_deg": _i32(p, 12) / 1.0e7,
        "gps_hmsl_m": f32(_i32(p, 16) / 1000.0),
        "gps_vel_n": f32(_fl(p, 20) / 100.0),
        "gps_vel_e": f32(_fl(p, 24) / 100.0),
        "gps_vel_d": f32(_fl(p, 28) / 100.0),
        "hdop": _fl(p, 32), "pdop": _fl(p, 36),
        "f40": _fl(p, 40), "f44": _fl(p, 44),
        "gps_used": _u32(p, 56) if len(p) >= 60 else None,
        "gln_used": _u32(p, 60) if len(p) >= 64 else None,
        "num_sv": _u16(p, 64) if len(p) >= 66 else None,
    }
    if len(p) >= 56:
        out["hacc"] = _fl(p, 48)
        out["sacc"] = _fl(p, 52)
    return out


def dec_controller(p: bytes) -> Dict[str, Any]:
    """Type 1000 (V3), layout chosen by payload length (spec 5.5)."""
    L = len(p)
    if L in (39, 51, 52, 53):
        o = 4
        ax = {"pitch": _i16(p, o), "roll": _i16(p, o + 2), "yaw": _i16(p, o + 4),
              "thr": _i16(p, o + 6)}
        rest = {"ctrl_mode": p[12], "mode_switch": p[13], "motor_state": p[14],
                "sig_level": p[15], "ctrl_level": p[16]}
    elif L == 44:
        ax = {"pitch": int(_fl(p, 4) * 10000), "roll": int(_fl(p, 8) * 10000),
              "yaw": int(_fl(p, 12) * 10000), "thr": int(_fl(p, 16) * 10000)}
        rest = {"ctrl_mode": p[20], "mode_switch": p[21], "motor_state": p[22],
                "sig_level": p[23], "ctrl_level": p[24]}
    elif L == 49:
        ax = {"pitch": _i16(p, 0), "roll": _i16(p, 2), "yaw": _i16(p, 4),
              "thr": _i16(p, 6)}
        rest = {"ctrl_mode": p[8], "mode_switch": p[9], "motor_state": p[10],
                "sig_level": p[11], "ctrl_level": p[12]}
    elif L in (40, 41):
        ax = {"pitch": _i16(p, 20), "roll": _i16(p, 22), "yaw": _i16(p, 24),
              "thr": _i16(p, 26)}
        rest = {"mode_switch": p[31], "sig_level": p[37], "ctrl_level": p[38]}
    elif L in (36, 37):
        ax = {"pitch": _i16(p, 4), "roll": _i16(p, 6), "yaw": _i16(p, 8),
              "thr": _i16(p, 10)}
        rest = {"sig_level": p[17] if L == 36 else p[15],
                "ctrl_level": p[18] if L == 36 else p[16]}
        if L == 36:
            rest["mode_switch"] = p[13]
    elif L == 47:   # DatCon Controller_47_1000 has no roll field
        ax = {"pitch": _i16(p, 4), "roll": None, "yaw": _i16(p, 6), "thr": _i16(p, 8)}
        rest = {"ctrl_mode": p[10], "mode_switch": p[11], "motor_state": p[12],
                "sig_level": p[13], "ctrl_level": p[14]}
    else:
        raise ValueError("controller length %d not in known layouts" % L)
    out = {"ctrl_tick": _u32(p, 0) if L != 49 else None,
           "rc_pitch": ax["pitch"], "rc_roll": ax["roll"], "rc_yaw": ax["yaw"],
           "rc_thr": ax["thr"]}
    out.update(rest)
    return out


def dec_controller_v1(p: bytes) -> Dict[str, Any]:
    """Type 0 (V1), 43 bytes (spec 5.5)."""
    return {"rc_roll": _i16(p, 4), "rc_pitch": _i16(p, 6), "rc_thr": _i16(p, 8),
            "rc_yaw": _i16(p, 10)}


def _cells(p: bytes, off: int, n: int) -> List[float]:
    return [f32(_i16(p, off + 2 * i) / 1000.0) for i in range(n)]


def dec_battery_5000(p: bytes, cells: int) -> Dict[str, Any]:
    """Type 5000/5001/5002 'bat_dynamic_data' (spec 5.6)."""
    L = len(p)
    if L >= 38:
        return {"batt_v": f32(_i32(p, 0) / 1000.0),
                "batt_i": -f32(_i32(p, 4) / 1000.0),
                "batt_fcc_mah": float(_i32(p, 8)),
                "batt_remcap_mah": float(_i32(p, 12)),
                "batt_temp_c": f32(_i16(p, 16) / 10.0),
                "batt_pct": _i8(p, 18),
                "batt_cells_v": _cells(p, 19, cells)}
    if L >= 26:
        return {"batt_v": f32(_i16(p, 0) / 1000.0),
                "batt_i": -f32(_i32(p, 2) / 1000.0),
                "batt_fcc_mah": float(_i16(p, 6)),
                "batt_remcap_mah": float(_i16(p, 8)),
                "batt_temp_c": f32(float(_i8(p, 10))),
                "batt_pct": _i8(p, 12),
                "batt_cells_v": _cells(p, 18, min(cells, (L - 18) // 2))}
    raise ValueError("battery 5000 length %d" % L)


def dec_battery_info_1710(p: bytes) -> Dict[str, Any]:
    """Type 1710 'battery_info', 38 or 44 bytes (spec 5.6)."""
    L = len(p)
    if L == 38:
        return {"bi_vol": _fl(p, 0), "bi_ave_i": f32(_fl(p, 4) / 1000.0),
                "bi_r_time": _u16(p, 8), "bi_cell_v": f32(_u16(p, 10) / 1000.0),
                "bi_pack_v": f32(_i32(p, 16) / 1000.0),
                "bi_i": -f32(_i32(p, 20) / 1000.0),
                "bi_r_cap": _u16(p, 24), "bi_cap_pct": p[26],
                "bi_temp_c": f32(_i16(p, 27) / 10.0), "bi_full_cap": _u32(p, 34)}
    if L != 44:
        raise ValueError("battery_info length %d has no engineered layout" % L)
    return {"bi_ad_v": f32(_u16(p, 0) / 1000.0), "bi_r_time": _u16(p, 2),
            "bi_ave_i": f32(_fl(p, 4) / 1000.0), "bi_vol_t": _fl(p, 8),
            "bi_pack_v": f32(_i32(p, 12) / 1000.0),
            "bi_i": -f32(_i32(p, 16) / 1000.0),
            "bi_r_cap": _u16(p, 20), "bi_cap_pct": p[22],
            "bi_temp_c": f32(_i16(p, 23) / 10.0),
            "bi_right": p[25], "bi_l_cell": _u16(p, 26),
            "bi_dyna_cnt": _u32(p, 28), "bi_full_cap": _u32(p, 32)}


def dec_battery_v1(p: bytes, cells: int) -> Dict[str, Any]:
    """Type 17 (V1), 45 bytes (spec 5.6)."""
    return {"batt_fcc_mah": float(_u16(p, 0)), "batt_remcap_mah": float(_i16(p, 4)),
            "batt_v": f32(_i16(p, 6) / 1000.0),
            "batt_i": -f32((_u16(p, 8) - 65536) / 1000.0),
            "batt_pct": _i8(p, 11), "batt_temp_c": f32(float(_i8(p, 12))),
            "batt_cells_v": _cells(p, 18, cells)}


def dec_esc(p: bytes, motors: int) -> Dict[str, Any]:
    """Type 10090, one block per ESC; block stride depends on payload length
    (152 -> 19, 169 -> 21, 185 -> 23 bytes; spec 5.7)."""
    out: Dict[str, Any] = {}
    stride = {152: 19, 169: 21, 185: 23}.get(len(p))
    if stride is None:
        raise ValueError("esc_data length %d has no engineered layout" % len(p))
    names = ["rf", "lf", "lb", "rb"] if motors == 4 else ["rf", "lf", "ls", "lb", "rb", "rs"]
    for i, nm in enumerate(names):
        o = stride * i
        if o + 13 > len(p):
            break
        out[nm + "_status"] = p[o]
        out[nm + "_current_a"] = f32(_i16(p, o + 1) / 100.0)
        out[nm + "_rpm"] = _i16(p, o + 3)
        out[nm + "_volts"] = f32(_i16(p, o + 5) / 10.0)
        out[nm + "_temp_c"] = _i16(p, o + 7)
    return out


def dec_rc_debug_1700(p: bytes) -> Dict[str, Any]:
    out = {"fail_safe": p[2], "data_lost": p[4] == 1, "app_lost": p[5] == 1,
           "frame_lost": _i8(p, 6)}
    if len(p) >= 45:
        out["rc_connected"] = p[44] == 1
    return out


# --------------------------------------------------------------------------
# spec 5.10  Self-describing record definitions (type 0xFFFD "recDefs")
# --------------------------------------------------------------------------
_RD_TYPES = {"op.uint8_t": ("B", 1), "op.int8_t": ("b", 1), "op.uint16_t": ("H", 2),
             "op.int16_t": ("h", 2), "op.uint32_t": ("I", 4), "op.int32_t": ("i", 4),
             "op.fp32": ("f", 4), "op.float": ("f", 4), "op.double": ("d", 8),
             "op.fp64": ("d", 8)}


def parse_recdefs(text: str) -> Dict[int, Dict[str, Any]]:
    """Parse concatenated 0xFFFD text into {type: {name, fields:[(n,fmt,off)], size}}."""
    defs: Dict[int, Dict[str, Any]] = {}
    lines = [ln.strip() for ln in re.split(r"[\r\n\x00]+", text)]
    cur: Optional[Dict[str, Any]] = None
    name = None
    mode = None
    for ln in lines:
        if not ln:
            continue
        toks = ln.split()
        t0 = toks[0].lower()
        if t0 == "op.[config]":
            mode, name, cur = "config", None, None
            continue
        if t0 == "op.[string]":
            mode, name, cur = "string", None, None
            continue
        if mode is None:
            continue
        if t0 == "name" and len(toks) > 1:
            name = "_".join(toks[1:])
            continue
        if t0 == "type" and len(toks) > 1 and toks[1].isdigit():
            tid = int(toks[1])
            cur = {"name": name, "kind": mode, "fields": [], "size": 0}
            defs[tid] = cur
            continue
        if cur is not None and t0 in _RD_TYPES:
            fmt, sz = _RD_TYPES[t0]
            fname = toks[1] if len(toks) > 1 else "f%d" % len(cur["fields"])
            cur["fields"].append((fname, fmt, cur["size"]))
            cur["size"] += sz
    return defs


def decode_by_recdef(defn: Dict[str, Any], p: bytes) -> Dict[str, Any]:
    st = defn.get("_struct")
    if st is None:
        st = defn["_struct"] = struct.Struct("<" + "".join(f[1] for f in defn["fields"]))
        defn["_names"] = [f[0] for f in defn["fields"]]
    if st.size <= len(p):
        return dict(zip(defn["_names"], st.unpack_from(p, 0)))
    out = {}
    for fname, fmt, off in defn["fields"]:
        sz = struct.calcsize(fmt)
        if off + sz > len(p):
            break
        out[fname] = struct.unpack_from("<" + fmt, p, off)[0]
    return out


# --------------------------------------------------------------------------
# spec 6  Timebase and UTC anchoring
# --------------------------------------------------------------------------
def gps_to_epoch(date: int, time_: int) -> Optional[float]:
    y, m, d = date // 10000, (date // 100) % 100, date % 100
    hh, mm, ss = time_ // 10000, (time_ // 100) % 100, time_ % 100
    if not (2010 <= y <= 2099 and 1 <= m <= 12 and 1 <= d <= 31 and hh < 24 and mm < 60
            and ss < 61):
        return None
    try:
        return _dt.datetime(y, m, d, hh, mm, min(ss, 59),
                            tzinfo=_dt.timezone.utc).timestamp() + (1 if ss == 60 else 0)
    except ValueError:
        return None


class UtcAnchor:
    """Map ticks to UTC from GPS whole-second transitions (spec 6.3).

    For each record where the GPS hhmmss value increments, the true second
    boundary lies between the previous and current record ticks. A linear fit
    (epoch = a + tick/rate) over all transitions gives the anchor; the fitted
    rate also measures the drift of the flight-controller clock.
    """

    def __init__(self, nominal_hz: float) -> None:
        self.nominal_hz = nominal_hz
        self.trans: List[Tuple[int, int, float]] = []   # (tick_prev, tick, epoch)
        self._last: Optional[Tuple[int, float]] = None
        self.a: Optional[float] = None
        self.rate: Optional[float] = None
        self.resid_max_s: Optional[float] = None
        self.bound_s: Optional[float] = None
        self.method: Optional[str] = None
        self.n_used = 0

    def feed(self, tick: int, date: int, time_: int) -> None:
        ep = gps_to_epoch(date, time_)
        if ep is None:
            return
        if self._last is not None and ep == self._last[1] + 1:
            # boundary lies between the last record showing the old second
            # and this record
            self.trans.append((self._last[0], tick, ep))
        self._last = (tick, ep)

    def solve(self) -> bool:
        """Anchor ticks to UTC.

        1. Interval intersection at the nominal rate: each transition bounds
           the offset to [S - t_cur/rate, S - t_prev/rate]; the intersection
           over all transitions is the answer (midpoint, half-width = bound).
        2. If the intervals do not intersect (clock drift or GPS glitches),
           fall back to a least-squares fit of epoch on tick, which also
           yields an effective tick rate.
        """
        tr = [(tp, tc, ep) for tp, tc, ep in self.trans if tc > tp]
        if not tr:
            return False
        r = self.nominal_hz
        lo = max(ep - tc / r for tp, tc, ep in tr)
        hi = min(ep - tp / r for tp, tc, ep in tr)
        self.n_used = len(tr)
        if lo <= hi:
            self.a, self.rate = (lo + hi) / 2.0, r
            self.bound_s = (hi - lo) / 2.0
            self.method = "interval-intersection@nominal"
            return True
        pts = [((tp + tc) / 2.0, ep) for tp, tc, ep in tr]
        if len(pts) < 2:
            return False
        a = slope = 0.0
        res: List[float] = []
        for _ in range(3):
            n = len(pts)
            mx = sum(p[0] for p in pts) / n
            my = sum(p[1] for p in pts) / n
            sxx = sum((p[0] - mx) ** 2 for p in pts)
            if sxx <= 0:
                return False
            slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx
            a = my - slope * mx
            res = [p[1] - (a + slope * p[0]) for p in pts]
            keep = [p for p, e in zip(pts, res) if abs(e) < 0.5]
            if len(keep) == len(pts) or len(keep) < 2:
                break
            pts = keep
        self.a, self.rate = a, 1.0 / slope
        self.resid_max_s = max(abs(e) for e in res) if res else None
        self.bound_s = self.resid_max_s
        self.method = "least-squares (intervals did not intersect)"
        self.n_used = len(pts)
        return True

    def tick_to_epoch(self, tick: int) -> Optional[float]:
        if self.a is None or self.rate is None:
            return None
        return self.a + tick / self.rate


# --------------------------------------------------------------------------
# Main decode
# --------------------------------------------------------------------------
C3_COLUMNS = ["t_offset_ms", "t_utc", "lat", "lon", "alt_rel_m", "alt_msl_m",
              "vel_n", "vel_e", "vel_d", "speed_h_ms", "roll", "pitch", "yaw",
              "gimbal_pitch", "gimbal_roll", "gimbal_yaw", "rc_aileron", "rc_elevator",
              "rc_throttle", "rc_rudder", "rc_mode_switch", "flight_mode", "gps_sats",
              "gps_health", "battery_pct", "battery_v", "battery_cells_v",
              "battery_temp_c", "home_lat", "home_lon", "distance_m", "height_m",
              "motor_on", "is_flying", "vps_height_m"]

# Which decoder applies to which type. V1 types are only used for V1 files.
V3_DECODERS = {12: "osd", 13: "home", 2048: "imu0", 2049: "imu1", 2050: "imu2",
               2096: "gps0", 2097: "gps1", 2098: "gps2", 1000: "rc", 1700: "rcdbg",
               1710: "binfo", 5000: "batt0", 5001: "batt1", 5002: "batt2",
               10090: "esc"}
V1_DECODERS = {12: "osd", 13: "home", 1: "imu0", 5: "gps0", 0: "rc_v1", 17: "batt_v1",
               2048: "imu0", 2096: "gps0"}


# recDef record names that identify a decoder kind regardless of type number.
# Type numbers are reassigned between firmware generations (e.g. on 2020 P4P V2
# firmware type 5000 is 'rtcm_data' and battery cells are in type 2396
# 'bat_dynamic_data'), so when a file carries recDefs, names win (spec 5.10).
RECDEF_KIND_BY_NAME = {
    "IMU_ATTI_0": "imu0", "IMU_ATTI_1": "imu1", "IMU_ATTI_2": "imu2",
    "Controller": "rc", "battery_info": "binfo", "bat_dynamic_data": "bdyn",
    "esc_data": "esc", "rc_debug_info": "rcdbg", "gps_utc_data": "gpsutc",
}
# engineered type -> recDef names compatible with it
ENGINEERED_NAMES = {
    1000: ("Controller",), 1700: ("rc_debug_info",), 1710: ("battery_info",),
    2048: ("IMU_ATTI_0",), 2049: ("IMU_ATTI_1",), 2050: ("IMU_ATTI_2",),
    5000: ("bat_dynamic_data", "battery_dynamic_data"),
    5001: ("bat_dynamic_data_1",), 5002: ("bat_dynamic_data_2",), 10090: ("esc_data",),
}


def resolve_kinds(v1: bool, recdefs: Dict[int, Dict[str, Any]]) -> Dict[int, str]:
    """Type -> decoder kind for this file (engineered table + recDef names)."""
    table = dict(V1_DECODERS if v1 else V3_DECODERS)
    if not recdefs:
        return table
    for t in list(table):
        d = recdefs.get(t)
        if d is not None and t in ENGINEERED_NAMES and d.get("name") not in ENGINEERED_NAMES[t]:
            del table[t]          # type number reused for something else
    for t, d in recdefs.items():
        k = RECDEF_KIND_BY_NAME.get(d.get("name") or "")
        if k and (t not in table or table[t] in ("batt0", "batt1", "batt2")):
            table[t] = k
    return table


def _rd(defn: Optional[Dict[str, Any]], p: bytes) -> Optional[Dict[str, Any]]:
    if defn is None or defn["size"] > len(p) or not defn["fields"]:
        return None
    return decode_by_recdef(defn, p)


def dec_rc_recdef(v: Dict[str, Any]) -> Dict[str, Any]:
    def stick(name):
        x = v.get(name)
        if x is None:
            return None
        return int(x * 10000) if isinstance(x, float) else x   # fp32 -1..1 -> +-10000
    return {"ctrl_tick": v.get("ctrl_tick"), "rc_pitch": stick("ctrl_pitch"),
            "rc_roll": stick("ctrl_roll"), "rc_yaw": stick("ctrl_yaw"),
            "rc_thr": stick("ctrl_thr"), "ctrl_mode": v.get("ctrl_mode"),
            "mode_switch": v.get("mode_switch"), "motor_state": v.get("motor_state"),
            "sig_level": v.get("sig_level"), "ctrl_level": v.get("ctrl_level"),
            "raw": v}


def dec_binfo_recdef(v: Dict[str, Any]) -> Dict[str, Any]:
    out = {"raw": v}
    if "BatVol" in v:          # 2020+ 'battery_info' (40 bytes)
        out.update({"bi_pack_v": f32(v["BatVol"] / 1000.0),
                    "bi_i": -f32(v.get("BatCurrent", 0) / 1000.0),
                    "bi_cap_pct": v.get("CapPercnt"), "bi_r_cap": v.get("BatCap"),
                    "bi_temp_c": float(v["BatTemp"]) if "BatTemp" in v else None,
                    "bi_full_cap": v.get("FullCap"),
                    "bi_cell_v": f32(v["CellVol"] / 1000.0) if "CellVol" in v else None})
    return out


def dec_bdyn_recdef(v: Dict[str, Any]) -> Dict[str, Any]:
    cells = [f32(v[k] / 1000.0) for k in sorted((k for k in v if re.match(r"cell\d+$", k)),
                                               key=lambda k: int(k[4:]))]
    return {"batt_v": f32(v["voltage"] / 1000.0) if "voltage" in v else None,
            "batt_i": -f32(v["current"] / 1000.0) if "current" in v else None,
            "batt_pct": v.get("soc"), "batt_temp_c": None, "batt_temp_raw": v.get("temperature"),
            "batt_cells_v": cells, "raw": v}


def dec_esc_recdef(v: Dict[str, Any], motors: int) -> Dict[str, Any]:
    names = ["rf", "lf", "lb", "rb"] if motors == 4 else ["rf", "lf", "ls", "lb", "rb", "rs"]
    out: Dict[str, Any] = {}
    for i, nm in enumerate(names):
        e = "e%d_" % (i + 1)
        if e + "spd" not in v:
            break
        out[nm + "_status"] = v.get(e + "sta")
        out[nm + "_current_a"] = f32(v.get(e + "I", 0) / 100.0)
        out[nm + "_rpm"] = v.get(e + "spd")
        out[nm + "_volts"] = f32(v.get(e + "V", 0) / 10.0)
        out[nm + "_temp_c"] = v.get(e + "temp")
    return out


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """DatCon Util.distance: haversine, R = 6371 km, inputs in radians."""
    if lat1 == 0.0 or lon1 == 0.0 or lat2 == 0.0 or lon2 == 0.0:
        return 0.0
    if not all(map(math.isfinite, (lat1, lon1, lat2, lon2))):
        return 0.0        # undecodable position (unknown record layout): no distance
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    a = min(max(a, 0.0), 1.0)  # rounding can push a past 1 for garbage inputs
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)) * 1000.0


def _clean_text(p: bytes) -> str:
    s = p.split(b"\x00", 1)[0]
    return s.decode("latin-1").replace("\r", "").strip()


class DatDecode:
    """Full decode of one DAT buffer. Mirrors the C1 ParseResult shape."""

    def __init__(self, buf: bytes, name: str = "", hz: float = 10.0,
                 datcon_sequence: bool = True, keep_records: Optional[set] = None,
                 header: Optional[DatHeader] = None,
                 emit_ticks: Optional[set] = None, fast: Optional[bool] = None) -> None:
        self.buf = buf
        # fast: index all records with numpy, then CRC-verify and decode only the
        # records that frames/events/UTC need (spec 12). None = auto (numpy present).
        self.fast = (_np is not None) if fast is None else (fast and _np is not None)
        self.kinds: Dict[int, str] = {}
        # emit_ticks: emit a frame row exactly at these record ticks instead
        # of the rate-based sampler (used by dat_validate.py to align with a
        # DatCon CSV, whose Tick# column is the tick of the record after
        # which DatCon printed the row).
        self.emit_ticks = emit_ticks
        self.name = name
        self.hz = hz
        self.datcon_sequence = datcon_sequence
        self.keep_records = keep_records
        self.header = header or parse_header(buf, name)
        self.stats = FrameStats()
        self.type_stats: Dict[int, Dict[str, Any]] = {}
        self.recdef_text: List[str] = []
        self.recdefs: Dict[int, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []
        self.texts: List[Tuple[int, int, str]] = []
        self.warnings: List[str] = []
        self.decode_fail: Dict[int, int] = {}
        self.decoded = 0
        self.seq_dropped = 0
        self.jump_dropped = 0
        self.kept: Dict[int, List[Tuple[int, Dict[str, Any]]]] = {}
        self.rows: List[Dict[str, Any]] = []
        self.row_ticks: List[int] = []
        self.anchor = UtcAnchor(self.header.clock_hz)
        self.first_tick: Optional[int] = None
        self.details: Dict[str, Any] = {}
        self.is_tablet = False

    # ---- pass ---------------------------------------------------------
    def run(self) -> "DatDecode":
        h = self.header
        if h.kind not in ("dat_v1", "dat_v3", "dat_headerless"):
            return self
        if self.fast:
            return self._run_fast()
        v1 = h.kind == "dat_v1"
        self.recdefs = prescan_recdefs(self.buf, h.start_of_records)
        table = self.kinds = resolve_kinds(v1, self.recdefs)
        cells, motors = h.cells, h.motors
        clock = h.clock_hz
        st: Dict[str, Dict[str, Any]] = {}
        sample = clock / self.hz if self.hz > 0 else 0
        last_print = None
        last_tick = 0
        prev_actual = 0
        for r in iter_records(self.buf, h.start_of_records, self.stats):
            ts = self.type_stats.get(r.type)
            if ts is None:
                ts = self.type_stats[r.type] = {"count": 0, "lengths": {}, "first_tick": r.tick,
                                                "last_tick": r.tick, "decoded": 0,
                                                "failed": 0}
            ts["count"] += 1
            pl = len(r.payload)
            ts["lengths"][pl] = ts["lengths"].get(pl, 0) + 1
            ts["last_tick"] = r.tick
            # DatCon acceptance (spec 3.5): large tick jump, out-of-sequence.
            if not v1 and abs(prev_actual - r.tick) > 22_000_000:
                prev_actual = r.tick
                self.jump_dropped += 1
                continue
            # V3: DatCon drops tick <= previous; V1 records come in groups that
            # share one tick, so only a decreasing tick is out of sequence.
            if self.datcon_sequence and (r.tick < last_tick or
                                         (not v1 and r.tick == last_tick)):
                self.seq_dropped += 1
                continue
            prev_actual = r.tick
            last_tick = r.tick
            if self.first_tick is None:
                self.first_tick = r.tick
            if r.type in TEXT_TYPES:
                self._text(r)
            kind = table.get(r.type)
            if kind is not None:
                try:
                    vals = self._decode(kind, r.payload, cells, motors, r.type)
                except Exception as e:  # noqa: BLE001 - count and continue
                    ts["failed"] += 1
                    self.decode_fail[r.type] = self.decode_fail.get(r.type, 0) + 1
                    if self.decode_fail[r.type] == 1:
                        self.warnings.append("type %d len %d decode error: %s"
                                             % (r.type, pl, e))
                    vals = None
                if vals is not None:
                    ts["decoded"] += 1
                    self.decoded += 1
                    vals["_tick"] = r.tick
                    st[kind] = vals
                    if kind == "gps0":
                        self.anchor.feed(r.tick, vals["gps_date"], vals["gps_time"])
                    if self.keep_records is not None and r.type in self.keep_records:
                        self.kept.setdefault(r.type, []).append((r.tick, vals))
            if self.emit_ticks is not None:
                if r.tick in self.emit_ticks and st:
                    self._emit(r.tick, st)
            elif sample and (last_print is None or r.tick >= last_print + sample) and st:
                self._emit(r.tick, st)
                last_print = r.tick
        self.is_tablet = 12 not in self.type_stats and not v1
        self._finish()
        return self

    def _run_fast(self) -> "DatDecode":
        np = _np
        h = self.header
        v1 = h.kind == "dat_v1"
        buf = self.buf
        ix = index_records_np(buf, h.start_of_records)
        pos, ln, typ, traw, tick = ix["pos"], ix["len"], ix["type"], ix["traw"], ix["tick"]
        s = self.stats
        s.records_ok = len(pos)
        s.crc16_fail = ix["damaged"]
        s.resyncs = ix["damaged"]
        s.tick_rollovers = ix["rollovers"]
        s.skipped_bytes = ix["n"] - h.start_of_records - ix["covered"]
        s.len_over_255 = int((ln > 255).sum())
        if len(pos) == 0:
            self._finish()
            return self
        s.first_offset = int(pos[0])
        # acceptance (spec 3.5): sequence rule == tick > running max of earlier ticks
        prev = np.maximum.accumulate(tick)
        acc = np.ones(len(tick), dtype=bool)
        if v1:
            acc[1:] = tick[1:] >= prev[:-1]
        else:
            acc[1:] = tick[1:] > prev[:-1]
        self.seq_dropped = int((~acc).sum())
        if not v1 and self.datcon_sequence:
            at = tick[acc]
            if len(at) > 1 and (np.abs(np.diff(at)) > 22_000_000).any() or (len(at) and at[0] > 22_000_000):
                # rare: replay DatCon's sequential jump rule over accepted records
                idx = np.flatnonzero(acc)
                last = 0
                for j in idx.tolist():
                    t = int(tick[j])
                    if abs(last - t) > 22_000_000:
                        acc[j] = False
                        self.jump_dropped += 1
                    last = t
        A = np.flatnonzero(acc)
        if len(A) == 0:   # every record failed the sequence/jump rules
            self._finish()
            return self
        at = tick[A]
        aty = typ[A]
        self.first_tick = int(at[0])
        # per-type stats (all CRC-valid records, like the full path)
        order = np.argsort(typ, kind="stable")
        ts_ = typ[order]
        cuts = np.concatenate(([0], np.flatnonzero(np.diff(ts_)) + 1, [len(ts_)]))
        for a0, a1 in zip(cuts[:-1].tolist(), cuts[1:].tolist()):
            seg = order[a0:a1]
            t = int(ts_[a0])
            lens, lc = np.unique(ln[seg] - 12, return_counts=True)
            self.type_stats[t] = {"count": a1 - a0,
                                  "lengths": dict(zip(lens.tolist(), lc.tolist())),
                                  "first_tick": int(tick[seg[0]]), "last_tick": int(tick[seg[-1]]),
                                  "decoded": 0, "failed": 0}
        # recDefs + text records: decode all (few)
        for j in A[np.isin(aty, list(TEXT_TYPES))].tolist():
            p = _payload_at(buf, int(pos[j]), int(ln[j]), int(traw[j]))
            if p is None:
                continue
            self._text(Rec(int(pos[j]), int(ln[j]), int(typ[j]), int(traw[j]), int(tick[j]),
                           p, 0))
        self.recdefs = parse_recdefs("".join(self.recdef_text)) if self.recdef_text else {}
        self.recdef_text = []
        kinds = self.kinds = resolve_kinds(v1, self.recdefs)
        # rows
        if self.emit_ticks is not None:
            et = np.array(sorted(self.emit_ticks), dtype=np.int64)
            rows_t = et[np.isin(et, at)]
        else:
            sample = h.clock_hz / self.hz if self.hz > 0 else 0
            out = []
            i = 0
            nA = len(at)
            while i < nA:
                out.append(int(at[i]))
                if not sample:
                    i += 1
                    continue
                # integer target: a float would make numpy convert `at` each call
                i = int(np.searchsorted(at, int(math.ceil(int(at[i]) + sample)), "left"))
            rows_t = np.array(out, dtype=np.int64)
        cells, motors = h.cells, h.motors
        # which record of each kind is current at each row tick
        per_kind_rows: Dict[str, List[Optional[Dict[str, Any]]]] = {}
        cache: Dict[int, Optional[Dict[str, Any]]] = {}

        def decode_j(j: int) -> Optional[Dict[str, Any]]:
            if j in cache:
                return cache[j]
            t = int(typ[j])
            p = _payload_at(buf, int(pos[j]), int(ln[j]), int(traw[j]))
            vals = None
            if p is None:
                s.crc16_fail += 1
            else:
                try:
                    vals = self._decode(kinds[t], p, cells, motors, t)
                except Exception as e:  # noqa: BLE001
                    self.type_stats[t]["failed"] += 1
                    self.decode_fail[t] = self.decode_fail.get(t, 0) + 1
                    if self.decode_fail[t] == 1:
                        self.warnings.append("type %d len %d decode error: %s"
                                             % (t, len(p), e))
                if vals is not None:
                    vals["_tick"] = int(tick[j])
                    self.type_stats[t]["decoded"] += 1
                    self.decoded += 1
            cache[j] = vals
            return vals

        kind_types: Dict[str, List[int]] = {}
        for t, k in kinds.items():
            kind_types.setdefault(k, []).append(t)
        for k, tl in kind_types.items():
            sel = A[np.isin(aty, tl)]
            if len(sel) == 0:
                continue
            ktick = tick[sel]
            if k == "gps0":
                for j in sel.tolist():   # all GPS records feed the UTC anchor
                    v = decode_j(j)
                    if v is not None:
                        self.anchor.feed(int(tick[j]), v["gps_date"], v["gps_time"])
            if k == "home":
                for j in sel.tolist():
                    decode_j(j)
            r = np.searchsorted(ktick, rows_t, "right") - 1
            vals_list: List[Optional[Dict[str, Any]]] = []
            for ri in r.tolist():
                v = None
                back = 0
                while ri >= 0 and back < 50:
                    v = decode_j(int(sel[ri]))
                    if v is not None:
                        break
                    ri -= 1
                    back += 1
                vals_list.append(v)
            per_kind_rows[k] = vals_list
        # home validity persists (DatCon keeps the first valid home)
        for i, t in enumerate(rows_t.tolist()):
            st = {k: v[i] for k, v in per_kind_rows.items() if v[i] is not None}
            if st:
                self.row_ticks.append(t)
                self.rows.append(st)
        self.is_tablet = 12 not in self.type_stats and not v1
        self.warnings.append("fast path: record CRC-16 verified for every record used in frames, "
                             "events and the UTC anchor; framing of other records validated "
                             "by header CRC-8 and chain continuity")
        self._finish()
        return self

    def _decode(self, kind: str, p: bytes, cells: int, motors: int,
                rtype: int = -1) -> Optional[Dict[str, Any]]:
        L = len(p)
        rd = self.recdefs.get(rtype)
        if kind == "rc" and rd is not None and L != 53:
            v = _rd(rd, p)
            return dec_rc_recdef(v) if v else None
        if kind == "binfo" and rd is not None and L not in (38, 44):
            v = _rd(rd, p)
            return dec_binfo_recdef(v) if v else None
        if kind == "bdyn":
            v = _rd(rd, p)
            return dec_bdyn_recdef(v) if v else None
        if kind == "esc" and rd is not None and L not in (152, 169, 185):
            v = _rd(rd, p)
            return dec_esc_recdef(v, motors) if v else None
        if kind == "gpsutc":
            return _rd(rd, p)
        if kind == "osd":
            return dec_osd_general(p) if L >= 50 else None
        if kind == "home":
            return dec_osd_home(p) if L >= 24 else None
        if kind.startswith("imu"):
            return dec_imu_atti(p) if L >= 120 else None
        if kind.startswith("gps"):
            return dec_gps(p) if L >= 56 else None
        if kind == "rc":
            return dec_controller(p)
        if kind == "rc_v1":
            return dec_controller_v1(p) if L >= 12 else None
        if kind == "rcdbg":
            return dec_rc_debug_1700(p) if L >= 7 else None
        if kind == "binfo":
            return dec_battery_info_1710(p)
        if kind.startswith("batt") and kind != "batt_v1":
            return dec_battery_5000(p, cells)
        if kind == "batt_v1":
            return dec_battery_v1(p, cells) if L >= 45 else None
        if kind == "esc":
            return dec_esc(p, motors)
        return None

    def _text(self, r: Rec) -> None:
        if r.type == 0xFFFD:
            # older firmware: newline-separated text and one trailing NUL; 2020+
            # firmware: every line NUL-terminated
            self.recdef_text.append(r.payload.replace(b"\x00", b"\n").decode("latin-1"))
            return
        s = _clean_text(r.payload)
        if not s:
            return
        self.texts.append((r.tick, r.type, s))

    def _emit(self, tick: int, st: Dict[str, Dict[str, Any]]) -> None:
        self.row_ticks.append(tick)
        self.rows.append({k: v for k, v in st.items()})  # shallow snapshot

    # ---- post ---------------------------------------------------------
    def _finish(self) -> None:
        h = self.header
        if self.recdef_text:
            self.recdefs = parse_recdefs("".join(self.recdef_text))
        self.anchor.solve()
        ft = self.first_tick or 0
        for tick, rtype, s in self.texts:
            low = s.lower()
            sev = "info"
            if any(w in low for w in ("error", "fail", "lost", "warn", "abnormal", "err:")):
                sev = "warn"
            if any(w in low for w in ("crash", "critical", "landing protect",
                                      "force land")):
                sev = "critical"
            self.events.append({"t_offset_ms": int(round((tick - ft) * 1000.0 / h.clock_hz)),
                                "tick": tick, "kind": REC_NAMES.get(rtype, str(rtype)),
                                "severity": sev, "message": s, "raw": None})
        self._details()
        if self.stats.crc16_fail + self.stats.crc8_fail + self.stats.bad_length:
            bad = self.stats.crc16_fail + self.stats.crc8_fail + self.stats.bad_length
            ratio = bad / max(1, self.stats.records_ok + bad)
            self.warnings.append("%d damaged record starts skipped (%.3f%% of records); "
                                 "scan resynchronised %d times"
                                 % (bad, 100 * ratio, self.stats.resyncs))
            if ratio > 0.02:
                self.warnings.append("damage exceeds DatCon's 2%% abort threshold; DatCon "
                                     "3.5 would stop converting this file early")
        if self.seq_dropped:
            self.warnings.append("%d records with non-increasing tick were excluded from "
                                 "frames (DatCon sequence rule)" % self.seq_dropped)
        if self.jump_dropped:
            self.warnings.append("%d records excluded for a tick jump > 22,000,000 "
                                 "(DatCon rule)" % self.jump_dropped)
        if self.stats.truncated_tail_bytes:
            self.warnings.append("file ends inside a record (%d trailing bytes): "
                                 "truncated copy or log still open"
                                 % self.stats.truncated_tail_bytes)
        if not self.rows and self.stats.records_ok and h.kind in ("dat_v1", "dat_v3",
                                                                  "dat_headerless"):
            self.warnings.append(
                "%d records framed (CRC-valid), %d decoded; no position records recognized "
                "for this platform (%s, product code %s, %s Hz clock%s). Events and timing "
                "are reported; no flight track is produced." % (
                    self.stats.records_ok, self.decoded, h.era or h.kind, h.product_code,
                    "%.0f" % h.clock_hz,
                    ", board %s" % self.details["x"]["fc_board"]
                    if self.details.get("x", {}).get("fc_board") else ""))

    def _details(self) -> None:
        h = self.header
        d: Dict[str, Any] = {k: None for k in (
            "aircraft_model", "aircraft_name", "aircraft_sn", "fc_sn", "camera_sn",
            "rc_sn", "battery_sn", "app_platform", "app_version", "log_version",
            "start_time_utc", "duration_s", "total_distance_m", "max_height_m",
            "max_h_speed_ms", "max_v_speed_ms", "home_lat", "home_lon", "city", "area",
            "sub_street", "firmware_version", "record_line_count")}
        d["aircraft_model"] = h.model
        d["log_version"] = h.kind
        d["product_type_code"] = h.product_code
        d["dat_subtype"] = {"dat_v1": "dat_legacy", "dat_v3": "dat_v3_plain"}.get(h.kind, h.kind)
        d["dat_build_string"] = h.build
        d["dat_detect_confidence"] = h.confidence
        d["file_size_bytes"] = len(self.buf)
        d["records_unknown_type"] = sum(t["count"] for k, t in self.type_stats.items()
                                        if k not in self.kinds and k not in TEXT_TYPES)
        comp = {}
        if h.serial_obf:
            comp["dat_header_serial_xor55"] = h.serial_obf
        d["component_serials"] = comp
        d["x"] = {"clock_hz": h.clock_hz, "header_tick_hz": h.header_tick_hz,
                  "header_hex_id_xor55": h.hash_obf, "app_side_copy": self.is_tablet,
                  "utc_anchor_method": self.anchor.method,
                  "utc_anchor_bound_s": self.anchor.bound_s,
                  "recdef_types": len(self.recdefs), "fast_path": self.fast,
                  "dat_era": h.era, "model_basis": h.model_basis,
                  "model_hint": h.model_hint, "fc_board": None}
        d["firmware_version"] = h.build
        d["record_line_count"] = self.stats.records_ok
        # The XOR-0x55 header serial's meaning (aircraft vs FC serial) is not proven, so
        # it is reported only under component_serials, never as aircraft_sn.
        for tick, rtype, s in self.texts:
            m = re.search(r"Board\s*:\s*\"?([A-Za-z0-9_.-]+)", s)
            if m and not d["x"]["fc_board"]:
                board = m.group(1)
                d["x"]["fc_board"] = board
                if h.kind == "dat_v1" and h.model_basis is None:
                    for key, name in V1_BOARDS.items():
                        if key in board.lower():
                            h.model = d["aircraft_model"] = name
                            h.model_basis = d["x"]["model_basis"] = (
                                "V1 boot text 'Board: %s' (DatCon DatFileV1.java:395-413)"
                                % board)
                            break
            m = re.search(r"Mc\s+ID\s*:\s*(\S+)", s)
            if m and not d["fc_sn"]:
                d["fc_sn"] = m.group(1)
            m = re.search(r"Mc\s+Ver\s*:\s*(.+)$", s)
            if m and d["firmware_version"] == h.build:
                d["firmware_version"] = "%s (%s)" % (m.group(1).strip(), h.build)
            # pack serial = the barcode DJI prints on the battery ("Bat SN" / "Battery
            # barcode" / BAT_LOG sn); "[L-BATTERY]Serial number" is the gauge chip's own
            # small counter, kept separately so it is never reported as the pack serial
            m = (re.search(r"Bat\s+SN\s*:\s*([A-Z0-9]{8,})", s) or re.search(r"Battery barcode\s*:\s*([A-Z0-9]{6,})", s)
                 or re.search(r"\[bat\]sn:\s*([A-Z0-9]{8,})", s))
            if m and not d["battery_sn"]:
                d["battery_sn"] = m.group(1)
            m = re.search(r"\[L-BATTERY\]Serial number\s*:\s*(\S+)", s)
            if m:
                comp.setdefault("battery_gauge_serial", m.group(1))
        ft = self.first_tick
        if ft is not None and self.stats.records_ok:
            last = max(t["last_tick"] for t in self.type_stats.values())
            d["duration_s"] = round((last - ft) / h.clock_hz, 3)
            ep = self.anchor.tick_to_epoch(ft)
            if ep is not None:
                d["start_time_utc"] = _iso(ep)
        self.details = d

    # ---- frames -------------------------------------------------------
    def frames(self) -> Dict[str, List[Any]]:
        h = self.header
        cols: Dict[str, List[Any]] = {c: [] for c in C3_COLUMNS}
        ft = self.first_tick or 0
        gps_ok = False
        home = None
        maxh, maxhs, maxvs = None, None, None
        bad_pos = 0
        for tick, st in zip(self.row_ticks, self.rows):
            osd = st.get("osd")
            imu = st.get("imu0")
            gps = st.get("gps0")
            rc = st.get("rc") or st.get("rc_v1")
            bat = st.get("batt0") or st.get("batt_v1") or st.get("bdyn")
            binfo = st.get("binfo")
            hp = st.get("home")
            ep = self.anchor.tick_to_epoch(tick)
            cols["t_offset_ms"].append(int(round((tick - ft) * 1000.0 / h.clock_hz)))
            cols["t_utc"].append(_iso(ep) if ep is not None else None)
            lat = lon = None
            if imu is not None:
                lat, lon = imu["lat_deg"], imu["lon_deg"]
            elif osd is not None:
                lat, lon = osd["lat_deg"], osd["lon_deg"]
            if osd is not None and not gps_ok:
                gps_ok = osd["lat_deg"] != 0.0 and osd["lon_deg"] != 0.0
            if lat is not None and (abs(lat) < 1e-6 and abs(lon) < 1e-6):
                lat = lon = None   # never 0,0 (C3)
            if lat is not None and (abs(lat) < 1.0 and abs(lon) < 1.0):
                lat = lon = None   # < 0.0175 rad: DatCon's "no lock" test
            if lat is not None and not (abs(lat) <= 90.0 and abs(lon) <= 180.0):
                lat = lon = None   # NaN/inf/out of range: payload is not a position
                bad_pos += 1
            cols["lat"].append(lat)
            cols["lon"].append(lon)
            height = osd["height_m"] if osd is not None else None
            cols["alt_rel_m"].append(height)
            cols["height_m"].append(height)
            cols["alt_msl_m"].append(gps["gps_hmsl_m"] if gps is not None else None)
            if imu is not None:
                vn, ve, vd = imu["vel_n"], imu["vel_e"], imu["vel_d"]
                cols["vel_n"].append(vn)
                cols["vel_e"].append(ve)
                cols["vel_d"].append(vd)
                hs = math.sqrt(vn * vn + ve * ve)
                cols["speed_h_ms"].append(hs)
                cols["roll"].append(imu["roll_deg"])
                cols["pitch"].append(imu["pitch_deg"])
                cols["yaw"].append(imu["yaw_deg"])
                maxhs = hs if maxhs is None else max(maxhs, hs)
                maxvs = abs(vd) if maxvs is None else max(maxvs, abs(vd))
            else:
                for c in ("vel_n", "vel_e", "vel_d", "speed_h_ms"):
                    cols[c].append(None)
                if osd is not None:
                    cols["roll"].append(osd["roll_deg"])
                    cols["pitch"].append(osd["pitch_deg"])
                    cols["yaw"].append(osd["yaw_deg"])
                else:
                    for c in ("roll", "pitch", "yaw"):
                        cols[c].append(None)
            for c in ("gimbal_pitch", "gimbal_roll", "gimbal_yaw"):
                cols[c].append(None)
            if rc is not None:
                def nz(v):
                    return None if v is None else max(-1.0, min(1.0, v / 10000.0))
                cols["rc_aileron"].append(nz(rc["rc_roll"]))
                cols["rc_elevator"].append(nz(rc["rc_pitch"]))
                cols["rc_throttle"].append(nz(rc["rc_thr"]))
                cols["rc_rudder"].append(nz(rc["rc_yaw"]))
            else:
                for c in ("rc_aileron", "rc_elevator", "rc_throttle", "rc_rudder"):
                    cols[c].append(None)
            if osd is not None:
                ch = osd["rc_mode_channel"]
                cols["rc_mode_switch"].append({0: "P", 1: "Sport"}.get(ch, "RC_MODE_%d" % ch))
                cols["flight_mode"].append(FLYC_STATE.get(osd["flyc_state"], "OTHER"))
                cols["gps_health"].append(osd["gps_level"])
                cols["motor_on"].append(osd["motor_on"])
                cols["is_flying"].append(osd["in_air"])
                cols["vps_height_m"].append(osd["vps_height_m"] if osd["wave_work"] else None)
            else:
                for c in ("rc_mode_switch", "flight_mode", "gps_health", "motor_on",
                          "is_flying", "vps_height_m"):
                    cols[c].append(None)
            if imu is not None:
                cols["gps_sats"].append(imu["num_sats"])
            elif osd is not None:
                cols["gps_sats"].append(osd["gps_nums"])
            else:
                cols["gps_sats"].append(None)
            if bat is not None:
                # pct/V/temp: prefer battery_info (1710, ~50 Hz) when present; the
                # bat_dynamic_data record (cells) is ~1 Hz on 2020+ firmware
                b2 = binfo if (binfo is not None and binfo.get("bi_pack_v") is not None) else None
                pct = b2.get("bi_cap_pct") if b2 else bat.get("batt_pct")
                cols["battery_pct"].append(float(pct) if pct is not None else None)
                cols["battery_v"].append(b2["bi_pack_v"] if b2 else bat.get("batt_v"))
                cols["battery_cells_v"].append(bat["batt_cells_v"])
                bt = b2.get("bi_temp_c") if b2 else bat.get("batt_temp_c")
                cols["battery_temp_c"].append(bt)
            elif binfo is not None:
                cols["battery_pct"].append(float(binfo["bi_cap_pct"])
                                           if binfo.get("bi_cap_pct") is not None else None)
                cols["battery_v"].append(binfo.get("bi_pack_v"))
                cols["battery_cells_v"].append(None)
                cols["battery_temp_c"].append(binfo.get("bi_temp_c"))
            else:
                for c in ("battery_pct", "battery_v", "battery_cells_v", "battery_temp_c"):
                    cols[c].append(None)
            if hp is not None and hp["home_valid_datcon"]:
                home = hp
            if home is not None:
                cols["home_lat"].append(home["home_lat_deg"])
                cols["home_lon"].append(home["home_lon_deg"])
                if imu is not None and lat is not None:
                    cols["distance_m"].append(haversine_m(imu["lat_rad"], imu["lon_rad"],
                                                          home["home_lat_rad"],
                                                          home["home_lon_rad"]))
                else:
                    cols["distance_m"].append(None)
            else:
                cols["home_lat"].append(None)
                cols["home_lon"].append(None)
                cols["distance_m"].append(None)
            if height is not None:
                maxh = height if maxh is None else max(maxh, height)
        d = self.details
        d["max_height_m"] = maxh
        d["max_h_speed_ms"] = maxhs
        d["max_v_speed_ms"] = maxvs
        if home is not None:
            d["home_lat"], d["home_lon"] = home["home_lat_deg"], home["home_lon_deg"]
        dist = 0.0
        plat = plon = None
        for la, lo in zip(cols["lat"], cols["lon"]):
            if la is None:
                continue
            if plat is not None:
                dist += haversine_m(math.radians(plat), math.radians(plon),
                                    math.radians(la), math.radians(lo))
            plat, plon = la, lo
        d["total_distance_m"] = round(dist, 1) if plat is not None else None
        if bad_pos:
            msg = ("%d of %d frames had a position that is not a valid coordinate (NaN, infinite "
                   "or out of range) and were left without lat/lon; the position record layout "
                   "for this platform (product code %s) may differ from the known one"
                   % (bad_pos, len(self.rows), h.product_code))
            if msg not in self.warnings:
                self.warnings.append(msg)
        return cols

    def parse_result(self) -> Dict[str, Any]:
        h = self.header
        enc = bool(h.encrypted) or h.kind in ("logh_encrypted",)
        frames = self.frames() if not enc else {c: [] for c in C3_COLUMNS}
        failed = sum(t["failed"] for t in self.type_stats.values())
        src = "aircraft-dat"
        if self.is_tablet:
            src = "controller-dat"   # app/RC-side copy (no type 12), spec 8
        return {
            "adapter": "dat_ref_py",
            "adapter_version": "dat_ref.py %s" % DECODER_VERSION,
            "details": self.details,
            "frames": frames,
            "events": [{k: e[k] for k in ("t_offset_ms", "kind", "severity", "message",
                                           "raw")} for e in self.events],
            "records_total": self.stats.records_ok + self.stats.crc16_fail
            + self.stats.crc8_fail + self.stats.bad_length,
            "records_decoded": self.decoded,
            "records_failed": failed + self.stats.crc16_fail + self.stats.crc8_fail
            + self.stats.bad_length,
            "warnings": self.warnings,
            "encrypted_unreadable": enc,
            "_source_hint": src,
            "_fast_path": self.fast,
            "_timebase": {"clock_hz": h.clock_hz, "header_tick_hz": h.header_tick_hz,
                          "utc_fit_rate_hz": self.anchor.rate,
                          "utc_fit_points": self.anchor.n_used,
                          "utc_fit_resid_max_s": self.anchor.resid_max_s,
                          "utc_anchor_method": self.anchor.method,
                          "utc_anchor_bound_s": self.anchor.bound_s},
        }


def prescan_recdefs(buf: bytes, start: int, max_bytes: int = 8 << 20) -> Dict[int, Dict[str, Any]]:
    """Collect 0xFFFD recDef text from the start of the file (it precedes data)."""
    st = FrameStats()
    parts: List[str] = []
    seen_after = 0
    for r in iter_records(buf[:start + max_bytes], start, st):
        if r.type == 0xFFFD:
            parts.append(r.payload.replace(b"\x00", b"\n").decode("latin-1"))
            seen_after = 0
        elif parts:
            seen_after += 1
            if seen_after > 20000:
                break
    return parse_recdefs("".join(parts)) if parts else {}


try:  # numpy is optional; the fast path needs it
    import numpy as _np
except Exception:  # noqa: BLE001
    _np = None


def index_records_np(buf: bytes, start: int) -> Dict[str, Any]:
    """Vectorised record index (spec 3.4 rules, CRC-16 deferred).

    Candidates: every 0x55 with a sane 10-bit length, protocol version 0 and a
    valid header CRC-8. Walk: from a candidate, the next record is the first
    candidate at or after its end. If its end is not exactly a candidate start
    (or EOF / zero padding), its CRC-16 is checked; a bad one resumes at the next
    candidate after its start byte, exactly as the full scanner does.
    """
    np = _np
    a = np.frombuffer(buf, dtype=np.uint8)
    n = len(a)
    c = np.flatnonzero(a[start:] == 0x55).astype(np.int64) + start
    c = c[c + 12 <= n]
    b1 = a[c + 1].astype(np.int64)
    b2 = a[c + 2].astype(np.int64)
    L = b1 | ((b2 & 3) << 8)
    ok = (L >= 12) & (c + L <= n) & ((b2 >> 2) == 0)
    c, L, b1, b2 = c[ok], L[ok], b1[ok], b2[ok]
    t8 = np.array(_CRC8_TABLE, dtype=np.uint8)
    h0 = int(_CRC8_TABLE[CRC8_SEED ^ 0x55])
    hc = t8[t8[h0 ^ b1] ^ b2]
    ok = hc == a[c + 3]
    c, L = c[ok], L[ok]
    end = c + L
    nxt = np.searchsorted(c, end)
    nxt_pos = np.where(nxt < len(c), c[np.minimum(nxt, len(c) - 1)], -1)
    exact = (nxt_pos == end) | (end >= n)
    endc = np.minimum(end, n - 1)
    exact |= (a[endc] == 0) & (end < n)
    keep = []
    damaged = 0
    cl, Ll, nl, el = c.tolist(), L.tolist(), nxt.tolist(), exact.tolist()
    N = len(cl)
    i = 0
    app = keep.append
    while i < N:
        if el[i]:
            app(i)
            i = nl[i]
            continue
        p0, ln = cl[i], Ll[i]
        if crc16_dji(buf[p0:p0 + ln - 2]) == (buf[p0 + ln - 2] | (buf[p0 + ln - 1] << 8)):
            app(i)
            i = nl[i]
        else:
            damaged += 1
            i += 1
    k = np.array(keep, dtype=np.int64)
    pos = c[k]
    ln = L[k]
    typ = a[pos + 4].astype(np.int64) | (a[pos + 5].astype(np.int64) << 8)
    traw = (a[pos + 6].astype(np.int64) | (a[pos + 7].astype(np.int64) << 8) |
            (a[pos + 8].astype(np.int64) << 16) | (a[pos + 9].astype(np.int64) << 24))
    # spec 6.2 rollover (DatCon thresholds), stragglers ignored in the fast path
    wrap = np.zeros(len(traw), dtype=np.int64)
    if len(traw) > 1:
        w = (traw[:-1] > 4_292_717_296) & (traw[1:] < 2_225_000)
        wrap[1:] = np.cumsum(w)
    tick = traw + (wrap << 32)
    covered = int(ln.sum())
    return {"pos": pos, "len": ln, "type": typ, "traw": traw, "tick": tick,
            "damaged": damaged, "covered": covered, "n": n,
            "rollovers": int(wrap[-1]) if len(wrap) else 0}


def _payload_at(buf: bytes, pos: int, ln: int, traw: int, verify: bool = True) -> Optional[bytes]:
    if verify and crc16_dji(buf[pos:pos + ln - 2]) != (buf[pos + ln - 2] | (buf[pos + ln - 1] << 8)):
        return None
    raw = buf[pos + 10:pos + ln - 2]
    k = traw & 0xFF
    return raw.translate(_XOR_TABLES[k]) if k else raw


def _iso(ep: float) -> str:
    t = _dt.datetime.fromtimestamp(ep, tz=_dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


# --------------------------------------------------------------------------
# C1-shaped entry points
# --------------------------------------------------------------------------
def detect(path: str) -> Dict[str, Any]:
    with open(path, "rb") as f:
        head = f.read(1 << 20)
    h = parse_header(head, os.path.basename(path))
    sub = {"dat_v1": "dat_legacy", "dat_v3": "dat_v3_plain",
           "logh_encrypted": "dat_encrypted", "dji_assistant_export": "dat_assistant_export",
           "dat_headerless": "dat_headerless"}.get(h.kind)
    reasons = list(h.reasons)
    base = os.path.basename(path)
    if re.match(r"^(\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})"
                r"_FLY\d+\.DAT$", base, re.I):
        reasons.append("filename matches app-side MCDatFlightRecords pattern "
                       "(date-time prefix + _FLYnnn.DAT)")
    elif re.match(r"^FLY\d+\.DAT$", base, re.I):
        reasons.append("filename matches aircraft FLYnnn.DAT pattern")
    if h.kind in ("dat_v1", "dat_v3", "dat_headerless"):
        st = FrameStats()
        n = 0
        texty = 0
        for r in iter_records(head, h.start_of_records, st):
            n += 1
            if r.type == 0x8000:
                s = r.payload.split(b"\x00", 1)[0]
                if s and sum(32 <= c < 127 or c in (9, 10, 13) for c in s) >= 0.9 * len(s):
                    texty += 1
            if n >= 3000:
                break
        reasons.append("%d CRC-valid records in first MiB" % n)
        if n == 0:
            reasons.append("header present but no valid records: damaged or encrypted")
        elif texty == 0 and any(True for _ in [0]) and h.kind != "dat_headerless":
            reasons.append("no readable 0x8000 text after XOR in first MiB")
    return {"kind": "dji_dat" if h.kind != "unknown" else "unknown", "subtype": sub,
            "version": h.version, "encrypted": h.encrypted, "confidence": h.confidence,
            "reasons": reasons, "model": h.model, "product_code": h.product_code}


def parse_dat(path: str, hz: float = 10.0) -> Dict[str, Any]:
    with open(path, "rb") as f:
        buf = f.read()
    h = parse_header(buf, os.path.basename(path))
    if h.kind == "dji_assistant_export":
        ents = [e for e in assistant_entries(buf) if e.get("name", "").upper().split("/")[-1]
                .startswith("FLY") and e.get("data")]
        if not ents:
            return {"adapter": "dat_assistant_export", "adapter_version": DECODER_VERSION,
                    "details": {}, "frames": {c: [] for c in C3_COLUMNS}, "events": [],
                    "records_total": 0, "records_decoded": 0, "records_failed": 0,
                    "warnings": ["Assistant export contained no FLY*.DAT entry"],
                    "encrypted_unreadable": False}
        res = DatDecode(ents[0]["data"], ents[0]["name"], hz=hz).run().parse_result()
        if len(ents) > 1:
            res["warnings"].append("Assistant export holds %d FLY entries; decoded the "
                                   "first (%s)" % (len(ents), ents[0]["name"]))
        return res
    dd = DatDecode(buf, os.path.basename(path), hz=hz, header=h).run()
    return dd.parse_result()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, list):
        return json.dumps(v)
    return str(v)


def write_csv(res: Dict[str, Any], out) -> int:
    w = csv.writer(out, lineterminator="\n")
    w.writerow(C3_COLUMNS)
    cols = res["frames"]
    n = len(cols["t_offset_ms"])
    for i in range(n):
        w.writerow([_fmt(cols[c][i]) for c in C3_COLUMNS])
    return n


def records_report(dd: DatDecode) -> str:
    h = dd.header
    lines = []
    lines.append("file: %s" % dd.name)
    lines.append("kind: %s  model: %s  product_code: %s  build: %s" % (
        h.kind, h.model, h.product_code, h.build))
    lines.append("clock: %.1f Hz (header u32@0x30: %s)  records start at %d" % (
        h.clock_hz, h.header_tick_hz, h.start_of_records))
    if h.serial_obf:
        lines.append("header serial (XOR 0x55 @0x3A): %s   hex id @0x4A: %s" %
                     (h.serial_obf, h.hash_obf))
    s = dd.stats
    lines.append("records ok: %d  crc16 fail: %d  crc8 fail: %d  bad length: %d  "
                 "resyncs: %d  skipped bytes: %d  zero pad: %d  truncated tail: %d" % (
                     s.records_ok, s.crc16_fail, s.crc8_fail, s.bad_length, s.resyncs,
                     s.skipped_bytes, s.zero_pad_bytes, s.truncated_tail_bytes))
    lines.append("tick rollovers: %d  protover!=0: %d  len>255: %d  seq dropped: %d  "
                 "jump dropped: %d" % (s.tick_rollovers, s.protover_nonzero, s.len_over_255,
                                       dd.seq_dropped, dd.jump_dropped))
    if dd.anchor.rate:
        lines.append("UTC anchor: %s over %d GPS second transitions, rate %.1f Hz "
                     "(nominal %.1f), bound +/-%.3f s, first tick UTC %s" % (
                         dd.anchor.method, dd.anchor.n_used, dd.anchor.rate, h.clock_hz,
                         dd.anchor.bound_s or 0.0, dd.details.get("start_time_utc")))
    dur = None
    if dd.first_tick is not None:
        dur = dd.details.get("duration_s")
    lines.append("duration: %s s   tablet/app-side (no type 12): %s" % (dur, dd.is_tablet))
    lines.append("")
    lines.append("%6s %6s %-24s %8s %8s %8s %9s  %s" % (
        "type", "hex", "name", "count", "decoded", "failed", "rate_hz", "payload lengths"))
    for t in sorted(dd.type_stats):
        ts = dd.type_stats[t]
        span = (ts["last_tick"] - ts["first_tick"]) / h.clock_hz if h.clock_hz else 0
        rate = (ts["count"] - 1) / span if span > 0 else 0.0
        nm = REC_NAMES.get(t) or (dd.recdefs.get(t, {}).get("name")) or ""
        if t in dd.recdefs and dd.recdefs[t].get("name") and t not in REC_NAMES:
            nm = "recdef:" + dd.recdefs[t]["name"]
        lens = ",".join("%d" % k if v == ts["count"] else "%d(x%d)" % (k, v)
                        for k, v in sorted(ts["lengths"].items(), key=lambda kv: -kv[1])[:4])
        lines.append("%6d %6s %-24s %8d %8d %8d %9.2f  %s" % (
            t, "%04X" % t, nm[:24], ts["count"], ts["decoded"], ts["failed"], rate, lens))
    if dd.recdefs:
        lines.append("")
        lines.append("recDefs (type 0xFFFD) define %d record types" % len(dd.recdefs))
    if dd.warnings:
        lines.append("")
        lines.extend("warning: " + w for w in dd.warnings)
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="DroneDog DJI .DAT reference decoder")
    ap.add_argument("dat")
    ap.add_argument("--csv", help="write C3 frame columns to this CSV ('-' = stdout)")
    ap.add_argument("--records", action="store_true", help="print per-record-type stats")
    ap.add_argument("--json", help="write the C1 ParseResult as JSON")
    ap.add_argument("--detect", action="store_true", help="print C1 detect() result")
    ap.add_argument("--text", help="write 0x8000/0xFFxx text records to this file")
    ap.add_argument("--hz", type=float, default=10.0, help="frame sample rate (default 10)")
    ap.add_argument("--full", action="store_true", help="full scan (CRC-16 on every record); no numpy fast path")
    a = ap.parse_args(argv)
    if a.detect:
        print(json.dumps(detect(a.dat), indent=2))
    if not (a.csv or a.records or a.json or a.text):
        if not a.detect:
            ap.print_help()
        return 0
    with open(a.dat, "rb") as f:
        buf = f.read()
    h = parse_header(buf, os.path.basename(a.dat))
    name = os.path.basename(a.dat)
    if h.kind == "dji_assistant_export":
        ents = assistant_entries(buf)
        print("DJI Assistant export: %d entries" % len(ents), file=sys.stderr)
        for e in ents:
            print("  entry %-40s %10s bytes marker=0x%02X" % (
                e.get("name"), e.get("length"), e.get("marker", 0)), file=sys.stderr)
        fly = [e for e in ents if e.get("name", "").upper().split("/")[-1].startswith("FLY")]
        if not fly:
            return 2
        buf, name = fly[0]["data"], fly[0]["name"]
        h = parse_header(buf, name)
    if h.kind in ("logh_encrypted", "unknown"):
        print("%s: %s" % (name, "; ".join(h.reasons)), file=sys.stderr)
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump({"adapter": h.kind, "encrypted_unreadable": h.kind == "logh_encrypted",
                           "warnings": h.reasons, "frames": {}, "details": {}}, f, indent=1)
        return 3
    dd = DatDecode(buf, name, hz=a.hz, header=h, fast=None if not a.full else False).run()
    res = dd.parse_result()
    if a.records:
        print(records_report(dd))
    if a.csv:
        if a.csv == "-":
            write_csv(res, sys.stdout)
        else:
            with open(a.csv, "w", newline="", encoding="utf-8") as f:
                n = write_csv(res, f)
            print("wrote %d rows to %s" % (n, a.csv), file=sys.stderr)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1, default=str)
    if a.text:
        ft = dd.first_tick or 0
        with open(a.text, "w", encoding="utf-8") as f:
            for tick, rtype, s in dd.texts:
                f.write("%10.3f %-12s %s\n" % ((tick - ft) / h.clock_hz,
                                               REC_NAMES.get(rtype, rtype), s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
