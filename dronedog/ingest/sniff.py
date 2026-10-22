"""Pure-Python header sniffing and the fallback detector.

Used (a) by the walker to recognise flight logs by content whatever their name
(deleted FAT entries lose their first letter: FLY001.DAT -> _LY001.DAT), and (b) as
the C1 ``detect()`` fallback when the Rust core ``dronedog_core`` is not built.

Rules are taken from public format descriptions:
* DJI app TXT logs (dji-log-parser ``layout/prefix.rs``): u64 LE detail_offset,
  u16 LE detail_length, u8 version, then reserved zero bytes up to offset 100.
* DJI aircraft DAT (DatCon ``DatFile.java``): ASCII ``BUILD`` at offset 16;
  ``DJI_LOG_V3`` at 242 for V3 framing; ``LOGH`` at 0 marks encrypted DATs.
* DJI Assistant exported .DAT (DatCon ``DJIAssistantFile.java``): zlib stream
  (78 9C) containing [283-byte header, u32 length at 1, name at 7] + data.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

HEAD_BYTES = 512
FALLBACK_VERSION = "pysniff-1"


def _txt(head: bytes, size: int, name: str) -> dict | None:
    if len(head) < 12 or size < 12:
        return None
    detail_offset, detail_len, version = struct.unpack_from("<QHB", head, 0)
    if not 1 <= version <= 20:
        return None
    reasons = [f"TXT prefix: version byte {version}, detail_offset {detail_offset}, detail_length {detail_len}"]
    named = name.lower().startswith("djiflightrecord") or name.lower().endswith(".txt")
    if version < 12:
        if not (12 <= detail_offset <= size and detail_offset + detail_len <= size):
            return None
        if version >= 6:
            if len(head) < 100 or any(head[12:100]):
                return None
            reasons.append("bytes 12..99 reserved/zero as expected for v6-v11")
        if not 50 <= detail_len <= 8192:
            return None
        conf = 0.9 if named else 0.6
        encrypted = False
    else:
        if len(head) < 100 or not 100 <= detail_offset <= size:
            return None
        if any(head[20:100]):
            return None
        reasons.append("bytes 20..99 reserved/zero as expected for v12+")
        conf = 0.85 if named else 0.5
        encrypted = version >= 13
        if encrypted:
            reasons.append("v13+ records are AES-encrypted (keychain required); header still readable")
    if named:
        reasons.append(f"filename {name!r} matches DJI app log naming")
    return {"kind": "dji_txt", "subtype": f"txt_v{version}", "version": version,
            "encrypted": encrypted, "confidence": conf, "reasons": reasons}


def _dat(head: bytes, size: int, name: str) -> dict | None:
    lname = name.lower()
    if len(head) >= 21 and head[16:21] == b"BUILD":
        v3 = len(head) >= 252 and head[242:252] == b"DJI_LOG_V3"
        reasons = ["ASCII 'BUILD' at offset 16 (DJI aircraft/RC DAT header)"]
        if v3:
            reasons.append("'DJI_LOG_V3' at offset 242")
        if lname.endswith(".dat"):
            reasons.append(f"filename {name!r} has .DAT extension")
        return {"kind": "dji_dat", "subtype": "dat_v3" if v3 else "dat_v1", "version": 3 if v3 else 1,
                "encrypted": None, "confidence": 0.9 if lname.endswith(".dat") else 0.75,
                "reasons": reasons}
    if len(head) >= 4 and head[:4] == b"LOGH":
        return {"kind": "dji_dat", "subtype": "dat_encrypted", "version": None, "encrypted": True,
                "confidence": 0.8 if lname.endswith(".dat") else 0.6,
                "reasons": ["'LOGH' magic at offset 0 (encrypted DJI DAT)"]}
    return None


def assistant_export(head: bytes, name: str) -> bool:
    """DJI Assistant 2 exported .DAT bundle (zlib stream of FLY files)."""
    return len(head) >= 2 and head[0] == 0x78 and head[1] in (0x9C, 0xDA, 0x01, 0x5E) and \
        name.lower().endswith(".dat")


def sniff(head: bytes, name: str = "", size: int | None = None) -> dict | None:
    """Return a C1-shaped detection dict if the bytes look like a DJI log, else None."""
    if size is None:
        size = len(head)
    return _dat(head, size, name) or _txt(head, size, name)


def fallback_detect(path: str | os.PathLike) -> dict:
    """C1 ``detect()`` equivalent built only on header bytes + filename."""
    p = Path(path)
    size = p.stat().st_size
    with open(p, "rb") as fh:
        head = fh.read(HEAD_BYTES)
    d = sniff(head, p.name, size)
    if d is None:
        if assistant_export(head, p.name):
            d = {"kind": "unknown", "subtype": "dji_assistant_export", "version": None, "encrypted": None,
                 "confidence": 0.5, "reasons": ["zlib stream with .DAT extension (DJI Assistant export bundle)"]}
        else:
            d = {"kind": "unknown", "subtype": None, "version": None, "encrypted": None,
                 "confidence": 0.0, "reasons": ["no known DJI header signature"]}
    d = dict(d)
    d["reasons"] = list(d["reasons"]) + [f"detector: python fallback {FALLBACK_VERSION} (dronedog_core not loaded)"]
    return d
