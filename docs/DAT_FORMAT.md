# DJI flight-controller .DAT logs: format specification (DroneDog)

Status: implementation spec for `core/src/dat/` (Rust). The executable reference is
`tools/dat_ref.py`; validation against DatCon is in `corpus/baselines/dat/VALIDATION.md`.
Every structure below is either cited to a public source (file:line) or marked
**[observed]** with the corpus files it was verified on. Anything not verified is marked
**[unverified]**.

Scope and boundary (HANDOFF.md section 2, binding): this document covers DATs whose
records are plaintext or only obfuscated with the publicly documented per-record XOR.
Encrypted DATs are covered only as far as *recognising* them and listing the plaintext
metadata that stays visible (section 7). Nothing here attacks a cipher, derives keys or
reads keys from firmware.

---

## 0. Sources and licences

| tag | source | version read | licence | used for |
|---|---|---|---|---|
| DC | DatCon (aero-oli fork of DatCon 3.5.0, original by "BudWalker", datfile.net), `corpus/_downloads/DatCon` | commit 0f8ed8b (2025-12-05) | custom permissive (BSD-style: redistribution of source must keep the disclaimer; no copyleft) | framing, XOR, timebase, record layouts, DatCon CSV semantics |
| FT | o-gs/dji-firmware-tools, `corpus/_downloads/dji-firmware-tools` | commit 1956922 (2026-07-21) | GPL-3.0 | CRC seeds, header CRC-8, DUPC framing, record field names/enums (`comm_dissector/wireshark/dji-p3-flyrec-proto.lua`) |
| DROP | BiTLab DROP (Clark et al., "DROP (DRone Open source Parser) your drone", DFRWS EU 2017), `corpus/_downloads/DROP` | HEAD | MIT | independent confirmation of V1 framing/XOR; Phantom 3 sample DATs |
| D3VL | D3VL/DJI-DAT-Decryptor (read from git objects of a clone another agent had already emptied; not re-cloned, not in the repo) | 40012021 (2023-03-17) | MIT + Commons Clause | *recognition facts only*: LOGH magic and Assistant entry header offsets (`src/config.js` lines 1-13, `src/core/dat-item.js`). Decryption code and key material were not read. Per D9 no DroneDog code is derived from it; `dat_ref.py` uses only the DatCon-documented entry layout. |
| DRDP | EveZzy/DRDP `dat_parser.py` | HEAD | none stated (all rights reserved) | corroboration only (product-code table with later models); nothing copied |
| FRPL | dji-sdk FlightRecordParsingLib | HEAD | MIT | TXT-side field semantics only (no DAT content) |
| Fodogu | Fodogu dji-dat-tool (Mini 2 "E2" DATs) | not read | none stated | not used (D9): recognition notes in section 7 come from HANDOFF.md only |
| AFDYLS | DataverseNO doi:10.18710/AFDYLS (Meier, Hann et al.) | v1 | CC0 | ground truth: Phantom 4 Pro DATs + DatCon CSVs |

GPL code (FT) was read as a specification only. `tools/dat_ref.py` is an independent
implementation written from this document; no GPL or DatCon source is copied into the repo.
Lookup tables for CRCs are generated from the polynomials, not pasted.

Every citation carries its source licence in brackets, e.g. `DC[BSD-style]`, `FT[GPL-3.0]`, `D3VL[MIT+Commons-Clause]`, `DROP[MIT]`, `DRDP[no licence]` (DECISIONS.md D9).

Citation format: `DC Files/DatFile.java:521` means
`corpus/_downloads/DatCon/DatCon/src/Files/DatFile.java`, line 521.
`FT lua:2643` means `comm_dissector/wireshark/dji-p3-flyrec-proto.lua` line 2643.

---

## 1. Artifact families at a glance

| artifact | where found | container | header | record stream | status |
|---|---|---|---|---|---|
| Aircraft `FLYnnn.DAT` (V1 era) | aircraft internal microSD; Phantom 3, Inspire 1, early M100 | none | 128-byte, `BUILD` at 16 | 0x55 records, 600 Hz ticks | **decodes** |
| Aircraft `FLYnnn.DAT` (V3 era) | aircraft internal microSD; P4, P4P/P4A, Mavic Pro, Spark, I2, M200, M600, Mavic Air | none | 256-byte, `BUILD` at 16, `DJI_LOG_V3` at 242 | 0x55 records, ~4.5 MHz ticks | **decodes** |
| DJI Assistant 2 export (`*.DAT`) | PC export from Assistant 2 (DatCon era, e.g. Spark, Mavic Pro) | zlib stream of 283-byte-header entries | entry name `FLYnnn.DAT` | inner file is a V3 DAT | **decodes** (inflate, then V3) |
| App-side `MCDatFlightRecords/<date>_FLYnnn.DAT` | phone/tablet running DJI GO 4 (and later apps) | none | V3 header | V3 records, decimated subset, **no type 12** | **decodes** (subset) |
| Encrypted DAT (`LOGH`) | newer aircraft (Mavic Air 2 / Mini 2 onward), Assistant exports of Avata/O3/Goggles 2 | `LOGH` header | n/a | encrypted | **recognise only** (section 7) |
| Controller (RC) DAT | RC internal storage / Assistant export of the RC | **[unverified]**: no sample in corpus | - | - | not covered |

Implementations must decide by content (section 2), never by filename alone; the filename
only adds evidence (`reasons[]`).

---

## 2. File header and identification

### 2.1 Decision procedure (in order)

1. Bytes 0-1 = `78 9C` (or another zlib header byte pair) -> DJI Assistant export
   container (section 2.4). Source: DC[BSD-style] Files/DJIAssistantFile.java:19-37.
2. Bytes 0-3 = `LOGH` -> encrypted container, recognise only (section 7). DatCon raises
   "Probably an encrypted .DAT" on this magic: DC[BSD-style] Files/DatFile.java:146-148.
3. Bytes 16-20 = `BUILD` -> plaintext DAT: DC[BSD-style] Files/DatFile.java:138-139.
   - bytes 242-251 = `DJI_LOG_V3` -> **V3**, first record at offset 256;
   - otherwise **V1**, first record at offset 128. DC[BSD-style] Files/DatFile.java:151-157.
4. Otherwise, if at least 8 CRC-valid records (section 3) are found near the start ->
   headerless record stream (carved fragment or stripped header); decode with V3 rules
   and nominal 4.5 MHz clock, confidence lowered.
5. Otherwise unknown.

### 2.2 Header layout

All integers little-endian.

| offset | size | V1 | V3 | meaning | source |
|---:|---:|:-:|:-:|---|---|
| 0x00 | 1 (u32 slot) | x | x | flight-controller **product code** (table 2.3) | DC[BSD-style] Files/DatHeader.java:17-63 |
| 0x04 | u32 | x | x | observed 3 on every file **[observed]**; meaning unknown | - |
| 0x08 | u32 | x | x | observed 9 (P3 2015-16), 0x13 (Mavic Pro 2017), 0x5C (2023 app-side) **[observed]**; likely a log-format revision; unknown | - |
| 0x10 | 32 | x | x | ASCII `BUILD Mmm dd yyyy hh:mm:ss` = flight-controller firmware build timestamp, NUL padded | DC[BSD-style] Files/DatHeader.java:105-118 (reads 21..32) |
| 0x30 | u32 | x | x | **tick rate in Hz** (600 on V1; 4,500,000 on Mavic Pro and P4P; 3,840,000 on the 2023 code-89 file) **[observed]**. DatCon ignores this field and hard-codes the rate per product (2.3). | [observed] on all corpus DATs |
| 0x34 | 6 | | x | observed `07 00 00 00 00 03` **[observed]**; unknown | - |
| 0x3A | 14 | | x | ASCII serial number, each byte XOR 0x55, zero bytes stored as 0x55 **[observed]**: `08RDE4E00102YL` (Mavic Pro), `1581F6GKB235K0` (code 89). Likely the aircraft/FC serial; treat as *attribution evidence*, label "header serial (XOR 0x55)". | [observed] |
| 0x4A | 32 | | x | 32 lowercase hex digits XOR 0x55 on the Mavic Pro file (empty on the 2023 file) **[observed]**; meaning unknown (looks like an MD5/UUID) | [observed] |
| 0xF2 | 10 | | x | ASCII `DJI_LOG_V3` | DC[BSD-style] Files/DatFile.java:151 |
| 0x80 | - | x | | first record (V1) | DC[BSD-style] Files/DatFile.java:156 |
| 0x100 | - | | x | first record (V3) | DC[BSD-style] Files/DatFile.java:153 |

V1 bytes 0x34-0x7F were zero on all four Phantom 3 / Inspire 1 files **[observed]**.

### 2.3 Product codes (header byte 0) and nominal tick rate

DatCon 3.5 (DC[BSD-style] Files/DatHeader.java:17-63) and the flight-controller product-type enum
in FT[GPL-3.0] lua:2600-2621 agree where both have the code.

| code | model | family/era | DatCon clock (Hz) | header 0x30 seen | cells | source |
|---:|---|---|---:|---:|---:|---|
| 5 | Matrice 100 | V1/V3 **[unverified]** | 85,000,000 | - | 6 | DC[BSD-style] DatHeader.java:21-25 |
| 6 | Phantom 3 (Std/Adv/Pro) / Inspire 1 | V1 | 600 | 600 | 4 (I1: 6) | DC[BSD-style] DatHeader.java:26-30 |
| 11 | Phantom 4 | V3 | 4,500,000 | - | 4 | DC[BSD-style] DatHeader.java:31-34; FT[GPL-3.0] lua:2612 |
| 14 | Matrice 600 | V3 | 4,500,000 | - | 6 | DC[BSD-style] DatHeader.java:35-38 |
| 16 | Mavic Pro (wm220) | V3 | 4,500,000 | 4,500,000 | 3 | DC[BSD-style] DatHeader.java:39-42; FT[GPL-3.0] lua:2616 |
| 17 | Inspire 2 | V3 | 4,500,000 | - | 6 | DC[BSD-style] DatHeader.java:43-46 |
| 18 | Phantom 4 Pro | V3 | 4,500,000 | see VALIDATION.md | 4 | DC[BSD-style] DatHeader.java:47-50 |
| 20 | S900 | V3 | 4,500,000 | - | 6 | DC[BSD-style] DatHeader.java |
| 21 | Spark | V3 | 4,500,000 | - | 3 | DC[BSD-style] DatHeader.java |
| 23 | Matrice 600 Pro | V3 | 4,500,000 | - | 6 | DC[BSD-style] DatHeader.java |
| 24 | Mavic Air | V3 | 4,500,000 | - | 3 | DC[BSD-style] DatHeader.java |
| 25 | Matrice 200 | V3 | 4,500,000 | - | 6 | DC[BSD-style] DatHeader.java |
| 27 | Phantom 4 Advanced | V3 | 4,500,000 | - | 4 | DC[BSD-style] DatHeader.java |
| 35 | *hint only:* seen on Phantom 4 Pro units, 2020 FC firmware | V3 | - | 4,687,500 | 4 | [observed] AFDYLS EPFL DATs (dataset metadata, not the file) |
| 41 | *hint only:* seen on a Mavic 2 Zoom | V3, app-side copy | - | 3,840,000 | 4 | [observed] CFReDS DF068 iOS backup (case metadata) |
| 89 | unknown 2023 aircraft (SN prefix `1581F6GK`) | V3, app-side copy | - | 3,840,000 | - | [observed] isensuiuc corpus |

**Model-naming rule (court honesty).** `details.aircraft_model` names a model only when the
file proves it: a product code from the published DatCon table above (`model_basis` =
"header product code N ...") or, for V1, a boot-text `Board:` name in DatCon's board list
(`wm320v2`, `wm610`, `wm321`, `wm328`). Otherwise it is `Unidentified DJI (V1-era DAT,
product code N)` / `(V3-era ...)`, with `details.x.dat_era`, `details.x.fc_board` and an
external association, if any, in `details.x.model_hint`. **Code 6 is not proof of a
Phantom 3/Inspire 1:** CFReDS DF040 (Agras MG-1S) V1 DATs also carry code 6, with boards
`ag405rtk`/`zd900` and a 4,125,000 Hz header clock **[observed]**. The XOR-0x55 header
serial (2.2) is reported only under `component_serials`, never as `aircraft_sn`, because
whether it is the aircraft or the flight-controller serial is not established.

**Agras / A3-family V1 DATs [observed, DF040, 48 files]:** CRC-valid V1 framing, readable
0x8000 boot/event text (`<FLY>Board:"zd900"`, `Mc  ID  :`, `Mc  Ver :v2.0.0.47`), but no
recDefs and none of the record types in section 5 (types seen: 199, 1006, 1033, 1075,
1085, 2286-2311, 52720, ...). A scan for float64-radian or int32-1e-7-degree coordinate
pairs found no verifiable candidate, so no layout is assumed: these files yield events,
timing and serials, zero frames, and the warning "N records framed, M decoded; no position
records recognized for this platform".

Later DatCon releases (closed source) handle further codes; DRDP[no licence] lists e.g. 36 = P4P V2,
41 = Mavic 2, 24 = Mavic Air at 3.85 MHz. Those are **[unverified]** here.

V1 sub-model: all V1 files carry code 6; DatCon refines it from the boot text records:
`wm320v2` -> P3 Adv/Pro, `wm610` -> Inspire 1, `wm321`/`wm328` -> P3 Standard
(DC[BSD-style] V1/Files/DatFileV1.java:395-413).

**Implementation rule:** use header 0x30 as the tick rate when it is non-zero and not
0xFFFFFFFF; fall back to the table. Report both, and report the UTC-fit rate (6.3).

### 2.4 DJI Assistant 2 export container

`78 9C ...` = a single zlib (RFC 1950) stream. Inflated, it is a sequence of entries:

| offset in entry | size | meaning | source |
|---:|---:|---|---|
| 0 | 1 | marker byte (0xA4 in D3VL's generation) | D3VL[MIT+Commons-Clause] dat-item.js (magic check), config.js:3 |
| 1 | u32 | data length N | DC[BSD-style] Files/DJIAssistantFile.java:89; D3VL[MIT+Commons-Clause] config.js:4 |
| 5 | 2 | unknown | D3VL[MIT+Commons-Clause] config.js:5 |
| 7 | string | NUL-terminated name; DatCon-era exports put `FLYnnn.DAT` here | DC[BSD-style] DJIAssistantFile.java:91 |
| 7 / 0x0C | 5 / 255 | newer exports: product tag at 7 (e.g. `G-E3T`), file path at 0x0C | D3VL[MIT+Commons-Clause] config.js:6-7 |
| 0x10B | 16 | unknown | D3VL[MIT+Commons-Clause] config.js:8 |
| 283 (0x11B) | N | entry data | DC[BSD-style] DJIAssistantFile.java:15 (`hearerLen = 283`); D3VL[MIT+Commons-Clause] dat-item.js (`headerLength = 0x11B`) |

Entries follow back to back. Entries named `FLY*.DAT` are V3 DATs (decode normally);
other entries are skipped. If an inner entry starts with `LOGH` it is encrypted (section 7).
DatCon only extracts the first FLY entry and warns if there are more
(DC[BSD-style] DJIAssistantFile.java:124-134); DroneDog must extract **all** of them, each with its
entry index and inflated offset recorded for custody.

---

## 3. Record framing

### 3.1 Layout

```
offset size  field
0      1     start byte 0x55
1      2     u16 LE: bits 0-9 = record length L (whole record, 12..1023)
                     bits 10-15 = protocol version (0 for flight records)
3      1     header CRC-8 over bytes 0..2
4      2     u16 LE record type
6      4     u32 LE tick
10     L-12  payload (XOR-obfuscated, section 4)
L-2    2     u16 LE CRC-16 over bytes 0..L-3
```

Sources: FT[GPL-3.0] lua:8601-8615 (field list), FT[GPL-3.0] lua:8640-8690 (length mask 0x3FF, protover
`>>10`), DC[BSD-style] V3/Files/DatFileV3.java:119-133 (DatCon reads length from byte 1 only and
requires byte 2 == 0, line 123/151), DC[BSD-style] Files/DatFile.java:43-45 (header 10, checksum 2).

Notes:
- DatCon treats byte 2 as "always 0". That is equivalent for records up to 255 bytes;
  DroneDog uses the 10-bit length so a longer record is still framed. No record longer than
  255 bytes was seen in the corpus **[observed]**; one in a real file would be silently
  dropped by DatCon, so report `len_over_255`.
- Protocol version != 0 means the bytes are a DUML v1 packet (live link capture), not a
  flight record (FT[GPL-3.0] dji-p3.lua:24-30). None seen in DATs **[observed]**.

### 3.2 Payload length

payload length = L - 12 (DC[BSD-style] V3/Files/DatFileV3.java:201-202).

### 3.3 Checksums

- **CRC-16** (record): reflected polynomial 0x8408 (CRC-16/CCITT 0x1021 bit-reversed),
  initial value **0x3692**, no final XOR, computed over bytes 0..L-3, stored LE at L-2.
  DC[BSD-style] Files/DatFile.java:555-624 (table + `v = 0x3692`), FT[GPL-3.0] comm_dat2pcap.py:75-120
  (same table; comment "0x3692 # P3/P4/Mavic"; other seeds listed there: 0x1012 Naza M,
  0x1013 Phantom 2, 0x7000 Naza M V2). Equivalent to CRC-16/KERMIT with init 0x3692.
  Bitwise form: `v = seed; for b: v ^= b; repeat 8: v = (v>>1) ^ (0x8408 if v&1 else 0)`.
- **CRC-8** (header): reflected polynomial 0x8C (Dallas/Maxim 1-Wire), initial value
  **0x77**, over bytes 0..2, stored at byte 3. FT[GPL-3.0] comm_dat2pcap.py:123-147 and :314
  (`calc_pkt55_hdr_checksum(0x77, packet, 3)`); same table in DC[BSD-style] Files/DatFile.java:521-553
  (defined but never called by DatCon).
- Test vectors (`tools/dat_ref.py`; the fast `binascii` form and the bitwise form agree
  on 2000 random buffers):
  - CRC-16(seed 0x3692) of ASCII `123456789` = **0x7109**
  - CRC-8(seed 0x77) of ASCII `123456789` = **0xFB**
  - Real record, `20-07-10-08-31-09_FLY256.DAT` offset 256 (41 bytes):
    `552900a80080706b97000b021f1f040d393e363f4a1c19122f021f0504152f191e1904501f1b70eb2d`
    -> header CRC-8 of `55 29 00` = 0xA8 (byte 3), CRC-16 of bytes 0..38 = 0x2DEB
    (stored `eb 2d`).
  - Header CRC-8 is valid on 100% of CRC-16-valid records in every corpus DAT
    **[observed]** (see `corpus/baselines/dat/corpus_scan.md`), so it is a cheap and
    strong pre-filter before the CRC-16.

### 3.4 Walking and resynchronisation

1. Start at the first-record offset (2.2).
2. Skip runs of 0x00 (Spark DATs pad with zeros, DC[BSD-style] V3/Files/DatFileV3.java:110-117).
3. Expect 0x55; check L >= 12, record fits in the file, CRC-8, then CRC-16.
4. On any failure: count it, then resume the search at the next 0x55 **after the failed
   start byte** (DC[BSD-style] DatFileV3.java:224-239 scans from `startOfRecord + 1`). Do not trust
   the failed record's length.
5. A record whose header CRC-8 is good but which runs past EOF is a truncated tail
   (file copied while open, or carved); report `truncated_tail_bytes`, stop.

Observed damage pattern in app-side DATs: records are cut short and a new record begins
inside them (a 0x8000 record of declared length 68 contains `55 3E 00 ...` at its byte 60).
Step 4 recovers the embedded record. On the 2023 code-89 app-side file this happened to
about 10% of records **[observed]**.

### 3.5 DatCon acceptance rules (needed for parity, and stated in the methodology)

DatCon 3.5 applies these after a record passes the CRC (DC[BSD-style] V3/Files/DatFileV3.java):

| rule | lines | DroneDog |
|---|---|---|
| byte 2 must be 0 | 123, 151 | framing uses 10-bit length; reports `len_over_255` |
| tick rollover handling | 155-173 | same (6.2) |
| `abs(tick - last_accepted) > 22,000,000` -> drop record, but set `last_accepted` to it; near EOF (< 40 kB left) -> stop | 177-185 | drop + count (`jump_dropped`), never stop |
| sequence: drop if tick <= last accepted tick | 198 | same for V3 frames (count `seq_dropped`); V1 groups share a tick, so only *decreasing* ticks are dropped |
| abort when > 1000 records and damaged/total > 2% | 220-223 | **never abort**; warn. App-side files routinely exceed 2%. |
| on damage within 600 bytes of EOF -> stop | 214-216 | keep scanning to EOF |

V1 (DC[BSD-style] V1/Files/DatFileV1.java:76-188) groups consecutive records with equal tick into a
"tick group"; records with a lower tick than the current group are ignored (line 151).

---

## 4. Payload obfuscation (public, not encryption)

Every payload byte is XORed with the low 8 bits of that record's tick:

`plain[i] = stored[i] XOR (tick & 0xFF)`

DC[BSD-style] DatConRecs/Payload.java:43-46 (`xorKey = (byte)(tickNo % 256)`); FT[GPL-3.0] lua:8619-8632
(`bxor(payload[i], band(seqctr, 0xFF))`); DROP[MIT] README ("payload decryption understood
through reverse engineering DatCon"). It applies to every record type in V1 and V3,
including text. With a rollover offset of 2^32 added the low byte is unchanged, so the raw
32-bit tick can be used. The header and CRCs are not obfuscated; the CRC-16 covers the
*obfuscated* bytes.

Distinct from this, V3 header bytes 0x3A..0x69 are XOR 0x55 (section 2.2).

---

## 5. Record catalog (key types)

Types are u16 at record offset 4. Names follow the recDefs text in the files (5.10) and
FT[GPL-3.0] lua:7-60. Rates are typical for a P4P aircraft DAT **[observed]** (see
`corpus/baselines/dat/records_*.txt`).

| type | hex | name | V1/V3 | typical rate (aircraft) |
|---:|---|---|---|---|
| 0 | 0000 | Controller (V1) | V1 | 50 Hz |
| 1 | 0001 | IMU_ATTI (V1) | V1 | 200 Hz |
| 5 | 0005 | GPS_GLNS (V1) | V1 | 5 Hz |
| 12 | 000C | osd_general | V1+V3 (aircraft only) | 10 Hz |
| 13 | 000D | osd_home | V1+V3 | 1 Hz |
| 17 | 0011 | battery_info (V1) | V1 | 1 Hz |
| 18 | 0012 | smart_battery_info (V1) | V1 | 1 Hz |
| 1000 | 03E8 | Controller | V3 | 50 Hz |
| 1001 | 03E9 | aircraft_condition | V3 | 50 Hz |
| 1700 | 06A4 | rc_debug_info | V3 | 50 Hz |
| 1710 | 06AE | battery_info | V3 | 10 Hz |
| 1712 | 06B0 | smart_battery_info | V3 | 10 Hz |
| 2048/2049/2050 | 0800-0802 | IMU_ATTI_0/1/2 | V3 | 50-200 Hz |
| 2064-2066 | 0810-0812 | IMU_EX_0/1/2 | V3 | |
| 2096/2097/2098 | 0830-0832 | GPS_0/1/2 | V3 | 5 Hz |
| 5000-5002 | 1388-138A | bat_dynamic_data 0/1/2 | V3 (Spark, Mavic) | |
| 10090 | 276A | esc_data (motors) | V3 | 50 Hz |
| 32768 | 8000 | fly_log (event text) | V1+V3 | events |
| 65280 | FF00 | sd_logs (text) | V1+V3 | |
| 65530 | FFFA | drv_log (text) | V1+V3 | |
| 65532 | FFFC | module name (text) | V3 | |
| 65533 | FFFD | recDefs (text schema) | V3 | at start |
| 65534 | FFFE | svn_info (text) | V1+V3 | at start |
| 65535 | FFFF | sys_cfg (text) | V1+V3 | at start |

Field tables below give the offset into the de-XORed payload, the stored type, the scale
to SI, and the source. "f4" = IEEE float32, "f8" = float64, all LE.

### 5.1 Type 12 osd_general (50-55 bytes) - "OSD", what the app displays

| off | type | field | scale / unit | source |
|---:|---|---|---|---|
| 0 | f8 | longitude | radians -> deg | FT[GPL-3.0] lua:2643, DC[BSD-style] GoTxt50_12.java:288 |
| 8 | f8 | latitude | radians -> deg | FT[GPL-3.0] lua:2644, DC[BSD-style] :289 |
| 16 | i16 | relative height | 0.1 m (above home/takeoff) | FT[GPL-3.0] lua:2645, DC[BSD-style] :290 |
| 18/20/22 | i16 x3 | vgx, vgy, vgz | 0.1 m/s (ground) | FT[GPL-3.0] lua:2646-2648 |
| 24/26/28 | i16 x3 | pitch, roll, yaw | 0.1 deg | FT[GPL-3.0] lua:2649-2651, DC[BSD-style] :291-293 |
| 30 | u8 | bits 0-6 flight-controller state (FLYC_STATE, below); bit 7 = RC link lost | | FT[GPL-3.0] lua:2652-2653, DC[BSD-style] :294,297 |
| 31 | u8 | latest command (bits 0-6) | enum FT[GPL-3.0] lua:2392 | DC[BSD-style] :295 |
| 32 | u32 | controller state flags: 0x02 on ground, 0x04 in air, 0x08 motor on, 0x10 ultrasonic on, 0xE0 go-home state, 0x100 vision used, 0x600 battery req go-home/land, 0x6000 RC mode switch, 0x8000 GPS used, 0x10000 compass error, 0x20000 ultrasonic error, 0x3C0000 **GPS level 0-5**, ... | | FT[GPL-3.0] lua:2655-2675; DC[BSD-style] :298-306 |
| 36 | u8 | number of GNSS satellites | | FT[GPL-3.0] lua:2676 |
| 37 | u8 | go-home / landing reason (FLIGHT_ACTION) | | FT[GPL-3.0] lua:2677; DC[BSD-style] :296 |
| 38 | u8 | motor start failure reason | | FT[GPL-3.0] lua:2678 |
| 39 | u8 | bits 0-3 GPS state ("nonGPSCause") | | FT[GPL-3.0] lua:2679-2681; DC[BSD-style] :308 |
| 40 | u8 | battery remaining % (often 0 in DAT) | | FT[GPL-3.0] lua:2683 |
| 41 | u8 | ultrasonic (VPS) height | 0.1 m | FT[GPL-3.0] lua:2684; DC[BSD-style] :309 |
| 42 | u16 | motor startup time (flight time) | 0.1 s | FT[GPL-3.0] lua:2685; DC[BSD-style] :310 (`*100` -> ms) |
| 44 | u8 | motor startup count | | FT[GPL-3.0] lua:2686 |
| 45/46 | u8 | battery alarm levels 1/2 | | FT[GPL-3.0] lua:2687-2692 |
| 47 | u8 | version match | | FT[GPL-3.0] lua:2693 |
| 48 | u8 | product type (same enum as header byte 0) | | FT[GPL-3.0] lua:2694 |
| 49 | i8 | IMU init fail reason | | FT[GPL-3.0] lua:2695 |

FLYC_STATE (byte 30 & 0x7F): 0 Manual, 1 Atti, 2 Atti_CL, 3 Atti_Hover, 4 Hover,
5 GPS_Blake, 6 GPS_Atti, 7 GPS_CL, 8 GPS_HomeLock, 9 GPS_HotPoint, 10 AssitedTakeoff,
11 AutoTakeoff, 12 AutoLanding, 13 AttiLangding, 14 NaviGo, 15 GoHome, 16 ClickGo,
17 Joystick, 23 Atti_Limited, 24 GPS_Atti_Limited, 25 NaviMissionFollow,
26 NaviSubMode_Tracking, 27 NaviSubMode_Pointing, 28 PANO, 29 Farming, 30 FPV, 31 SPORT,
32 NOVICE, 33 FORCE_LANDING, 35 TERRAIN_TRACKING, 36 NAVI_ADV_GOHOME,
37 NAVI_ADV_LANDING, 38 TRIPOD_GPS, 39 TRACK_HEADLOCK, 41 ASST_TAKEOFF, 43 GENTLE_GPS
(FT[GPL-3.0] lua:2353-2390, DC[BSD-style] GoTxt50_12.java:101-153; spellings kept verbatim because DatCon
CSVs use them). FLIGHT_ACTION (byte 37): DC[BSD-style] GoTxt50_12.java:46-99 (0 NONE ... 29
MC_PROTECT_GOHOME, e.g. 12 APP_AUTO_GOHOME, 15 OUTOF_CONTROL_GOHOME = signal-loss RTH).

DatCon treats a DAT with no type-12 record as a "tablet" (app-side) DAT
(DC[BSD-style] V3/Files/DatFileV3.java:335-339).

### 5.2 Type 13 osd_home (V3: 24..74 bytes, V1: 34/68 bytes)

| off | type | field | unit | source |
|---:|---|---|---|---|
| 0 | f8 | home longitude | rad | DC[BSD-style] Created4V3/HomePoint24_13.java:47; recDef comment :67-80 |
| 8 | f8 | home latitude | rad | :48 |
| 16 | f4 | home altitude | m (baro/MSL reference; not WGS84 ellipsoid) | :49 |
| 20 | u16 | home state bits: 0 set, 1 method, 2 heading, 3 dynamic, 6 multiple, 12 IOC | | :72-80 |
| 22 | u16 | go-home (RTH) height | m | :51 |
| 24 | i16 | course-lock torsion | | :74 |

DatCon accepts the home point once both radians values are < 100 (:54-58). The 2023
code-89 app-side file has an 83-byte variant whose first 24 bytes decode the same
**[observed]**.

### 5.3 IMU_ATTI: type 2048 (V3, IMU 0), 2049/2050 (IMU 1/2), type 1 (V1); 120 bytes

The fused navigation solution (position, velocity, attitude) at the IMU rate.

| off | type | field | unit | source |
|---:|---|---|---|---|
| 0 | f8 | longitude | rad | DC[BSD-style] RecIMU.java:182 |
| 8 | f8 | latitude | rad | :183 |
| 16 | f4 | barometric altitude, raw | m | :184 |
| 20/24/28 | f4 x3 | accelerometer x,y,z | g | :185-187 |
| 32/36/40 | f4 x3 | gyro x,y,z | rad/s (DatCon prints deg/s, :188-190) | |
| 44 | f4 | barometric altitude, smoothed | m | :191 |
| 48/52/56/60 | f4 x4 | attitude quaternion w,x,y,z | | :192-195 |
| 64/68/72 | f4 x3 | ag x,y,z (accel, ground frame) | | :196-198 |
| 76/80/84 | f4 x3 | velocity N, E, D | m/s | :199-201 |
| 88/92/96 | f4 x3 | gyro bias x,y,z | | :202-204 |
| 100/102/104 | i16 x3 | magnetometer x,y,z | raw | :205-207 |
| 106 | i16 | IMU temperature | 0.01 degC (V3); V1 stores whole degC (DatCon multiplies back, Created4V1/Record_1.java) | :208 |
| 108-115 | i16 x4 | unknown | | :209-212 |
| 116 | i8 | number of satellites used | | :213 |

The recDefs in the files name the same fields `long0 lati0 press0 ax0 ay0 az0 wx0 wy0 wz0
alti0 qw0 qx0 qy0 qz0 agx0 agy0 agz0 vgx0 vgy0 vgz0 gbx0 gby0 gbz0 mx0 my0 mz0 ...`
**[observed]** (Mavic Pro and 2023 files), which confirms the order.

**Attitude from the quaternion** (DatCon convention, DC[BSD-style] Files/Quaternion.java:195-226),
with w = scalar:
```
unit  = w^2 + x^2 + y^2 + z^2
test  = w*x + y*z
if test >  0.499*unit: yaw =  2*atan2(y, w); pitch =  pi/2; roll = 0
if test < -0.499*unit: yaw = -2*atan2(y, w); pitch = -pi/2; roll = 0
else:
   yaw   = atan2(2(w*z - x*y), 1 - 2(z^2 + x^2))
   roll  = asin(2*test/unit)
   pitch = atan2(2(w*y - x*z), 1 - 2(y^2 + x^2))
```
Angles in degrees; yaw is true-north referenced, range (-180, 180]. Cross-check: this
agrees with type 12's 0.1-degree pitch/roll/yaw (VALIDATION.md, internal consistency).

### 5.4 GPS: types 2096/2097/2098 (V3, 66/68/72/76 bytes), type 5 (V1, 68 bytes)

| off | type | field | unit | source |
|---:|---|---|---|---|
| 0 | u32 | date | decimal yyyymmdd (UTC) | DC[BSD-style] GpsGroup.java:103,160-164 |
| 4 | u32 | time | decimal hhmmss (UTC) | :104,165-168 |
| 8 | i32 | longitude | 1e-7 deg | :105 |
| 12 | i32 | latitude | 1e-7 deg | :106 |
| 16 | i32 | height MSL | mm | :107 |
| 20/24/28 | f4 x3 | velocity N/E/D | cm/s (DatCon divides by 100) | :108-110 |
| 32 | f4 | hDOP | | :111 |
| 36 | f4 | pDOP | | :112 |
| 40 | f4 | DatCon "hacc"; FromViewer calls it gps_fix | | :113; DC[BSD-style] FromViewer/GPS_GLNS_5.java:52 |
| 44 | f4 | DatCon "sAcc"; FromViewer calls it gnss_flag | | :114; GPS_GLNS_5.java:53 |
| 48 | f4 | hacc (FromViewer) | | GPS_GLNS_5.java:54 |
| 52 | f4 | sacc (FromViewer) | | GPS_GLNS_5.java:55 |
| 56 | u32 | GPS satellites used | | :115 |
| 60 | u32 | GLONASS satellites used | | :116 |
| 64 | u16 | total SVs | | :117 |
| 66 | u16 | gps state (68-byte layout) | | GPS_GLNS_5.java:59 |

The two DatCon class families disagree on the meaning of offsets 40-55; DroneDog stores
them under neutral names and does not use them in C3. DatCon only trusts date/time once
`sAcc` (offset 44) < 8000 (GpsGroup.java:118).

### 5.5 Controller (stick inputs): type 1000 (V3), type 0 (V1)

Stick values are -10000..+10000 (FT[GPL-3.0] lua:116-127). Layout depends on payload length
(DC[BSD-style] Created4V3/Dictionary.java:46-56 maps lengths to classes):

| payload len | pitch(elevator) | roll(aileron) | yaw(rudder) | throttle | mode_switch | sig_level | source |
|---:|---:|---:|---:|---:|---:|---:|---|
| 36 | i16@4 | i16@6 | i16@8 | i16@10 | u8@13 | u8@17 | DC[BSD-style] Controller_36_1000.java:47-56 |
| 37 | i16@4 | i16@6 | i16@8 | i16@10 | - | u8@15 | Controller_37_1000.java:13-18 |
| 39, 51, 52, 53 | i16@4 | i16@6 | i16@8 | i16@10 | u8@13 | u8@15 | Controller_53_1000.java:64-73 (u32 ctrl_tick @0, ctrl_mode u8@12, motor_state u8@14, ctrl_level u8@16) |
| 40, 41 | i16@20 | i16@22 | i16@24 | i16@26 | u8@31 | u8@37 | Controller_40_1000.java:63-76 |
| 44 | f4@4 x10000 | f4@8 | f4@12 | f4@16 | u8@21 | u8@23 | Controller_44_1000.java:48-57 |
| 47 | i16@4 | (none) | i16@6 | i16@8 | u8@11 | u8@13 | Controller_47_1000.java:59-66 |
| 49 | i16@0 | i16@2 | i16@4 | i16@6 | u8@9 | u8@11 | Controller_49_1000.java:62-70 |

The 53-byte layout is the one named in the Mavic Pro recDefs: `ctrl_tick, ctrl_pitch,
ctrl_roll, ctrl_yaw, ctrl_thr, ctrl_mode, mode_switch, motor_state, sig_level, ctrl_level,
sim_model, max_height, max_radius, D2H_x, D2H_y, ...` **[observed]**. When a file carries
recDefs for type 1000, prefer them over the length table.

V1 type 0 (43 bytes): aileron i16@4, elevator i16@6, throttle i16@8, rudder i16@10
(DC[BSD-style] Created4V1/RecController43_0.java:32-35).

DatCon CSV mapping: `RC:Aileron` = roll, `RC:Elevator` = pitch, `RC:Rudder` = yaw,
`RC:Throttle` = throttle (DC[BSD-style] Created4V3/RecController.java:57-66). C3 `rc_*` = value/10000.

### 5.6 Battery

**Type 1710 battery_info** (V3 aircraft; P4/P4P/Mavic Pro):

| len 44 off | len 38 off | type | field | unit | source |
|---:|---:|---|---|---|---|
| 0 | - | u16 | ad_v | mV | DC[BSD-style] Created4V3/BattInfo_44_1710.java:52 |
| 2 | 8 | u16 | remaining flight time | s | :53 / BattInfo_38_1710.java:49 |
| 4 | 4 | f4 | average current | mA | :54 / :48 |
| 8 | 0 | f4 | vol_t / Vol | V (44) | :55 / :47 |
| 12 | 16 | i32 | pack voltage | mV | :56 / :53 |
| 16 | 20 | i32 | current (DatCon negates) | mA | :57 / :54 |
| 20 | 24 | u16 | remaining capacity | mAh | :58 / :55 |
| 22 | 26 | u8 | **remaining %** | % | :59 / :56 |
| 23 | 27 | i16 | temperature | 0.1 degC | :60 / :57 |
| 25 | 29 | u8 | right/auth | | :61 / :58 |
| 26 | 10 | u16 | lowest cell | mV | :62 / :50 |
| 28 | 30 | u32 | data counter | | :63 / :59 |
| 32 | 34 | u32 | full capacity | mAh | :64 / :60 |
| 36,40 | - | f4 | out_ctl, out_ctl_f | | :65-66 |

**Type 5000/5001/5002 bat_dynamic_data** (Spark, Mavic Air; index = battery slot):

| off (38 bytes) | type | field | unit | source |
|---:|---|---|---|---|
| 0 | i32 | pack voltage | mV | DC[BSD-style] Created4V3/RecBatt38_500X.java:58, recDef comment :82-103 |
| 4 | i32 | current (negated by DatCon) | mA | :59 |
| 8 | u32 | full capacity | mAh | :60 |
| 12 | u32 | remaining capacity | mAh | :61 |
| 16 | i16 | temperature | 0.1 degC | :62 |
| 18 | u8 | state of charge | % | :63 |
| 19 + 2i | u16 | cell i voltage | mV | :64-67 |

26-byte variant: u16 pack mV@0, i32 current@2, u16 fcc@6, u16 remcap@8, i8 temp degC@10,
u8 soc@12, status u8@14..17, cells u16@18+2i (DC[BSD-style] Created4V3/RecBatt26_5000.java:61-76).

**V1 type 17** (45 bytes): design cap u16@0, full charge cap u16@2, remaining cap u16@4,
pack mV u16@6, current i16@8, life % u8@10, capacity % u8@11, temperature@12, cycle count
u16@14, serial u16@16, cells 1-6 u16@18..28 (mV), average current i16@30 (DC
Created4V1/RecBatt45_17.java:51-89). DatCon reads temperature as an i8 whole degC.

**Smart battery 1712** (10 bytes): go-home count u8@0, go-home cmd u8@1, go-home level
u16@2 %, land level u16@4 %, flight time for go-home u16@6 s, for land u16@8 s (DC
Created4V3/RecSmartBatt10_1712.java:44-64).

Cell count by model: DC[BSD-style] Files/DatFile.java:419-469 (Mavic/Spark 3, Phantom 4, Inspire and
Matrice 6).

### 5.7 Motors / ESC: type 10090 (V3), type 52721 (V1, 76 bytes)

One block per ESC in the order RF, LF, LB, RB (hexacopters: RF, LF, LS, LB, RB, RS). Block
stride depends on payload length: 152 -> 19, 169 -> 21, 185 -> 23 bytes
(DC[BSD-style] Created4V3/RecMotor152_10090.java:36-80, RecMotor169_10090.java:37-48,
RecMotor185_10090.java:38-126). Within a block:

| off | type | field | unit |
|---:|---|---|---|
| 0 | u8 | status | |
| 1 | i16 | current | 0.01 A |
| 3 | i16 | speed | rpm |
| 5 | i16 | volts | 0.1 V |
| 7 | i16 | ESC temperature | degC |
| 9 | i16 | PPM received | |
| 11 | i16 | V out | 0.1 V |

### 5.8 Gimbal

No gimbal record is decoded by DatCon from flight-controller DATs; the gimbal logs in its
own module. C3 `gimbal_*` columns stay empty for DATs unless a recDef for a gimbal type is
present **[observed: none in corpus]**.

### 5.9 Text records (event log)

All are NUL-terminated ASCII after XOR. DatCon's `eventLog` CSV column is the
concatenation of type 0x8000 texts within one output row (DC[BSD-style] String/RecFlyLog_32768.java:38-108).

| type | content | examples **[observed]** |
|---|---|---|
| 0x8000 fly_log | flight-controller event/warning log | `[L-FMU/MOTOR]Start.`, `[L-FMU/MOTOR] Stop.`, `[L-BATTERY]Serial number   :...`, `[L-TAKEOFF]alti: ...`, `Mc  ID  :...`, `Mc  Ver :...`, `{root}INFO:...` |
| 0xFF00 sd_logs | SD logger status | |
| 0xFFFA drv_log | driver log | `Info:2>write time_out ...` |
| 0xFFFC | module names | |
| 0xFFFE svn_info | firmware SVN/build info | |
| 0xFFFF sys_cfg | system configuration lines | |
| 0xFFFD recDefs | record schema text (5.10) | |

Attribution values DatCon extracts from 0x8000 (RecFlyLog_32768.java:58-90): `Mc  ID  :`
-> flight-controller serial ("mcID(SN)"), `Mc  Ver :` -> FC firmware version,
`[L-BATTERY]Serial number   :` -> battery serial, `[L-TAKEOFF]alti:` -> take-off barometric
altitude. Motor start/stop ticks come from `[L-FMU/MOTOR]Start.` / ` Stop.` (V3,
DC[BSD-style] DatFileV3.java:281-292) and `M.Start` / `M. Stop` (V1, DatFileV1.java:388-393).

### 5.10 recDefs (type 0xFFFD): self-describing schema

V3 files carry their own record definitions as text, split across many 0xFFFD records at
the start of the file; concatenate the payloads (each NUL-terminated) in file order:

```
Op.[config]
name<TAB>IMU_ATTI_0
type<TAB>2048
Op.fp64<TAB>long0 0
Op.fp64<TAB>lati0 0
Op.fp32<TAB>press0 0
...
Op.[config]
name<TAB>Controller
type<TAB>1000
Op.uint32_t<TAB>ctrl_tick 0
...
Op.[string]
name ...
type ...
```

**Line separator varies by firmware [observed]:** 2017 Mavic Pro recDefs are
newline-separated with one NUL per record; 2020 P4P (code 35) firmware NUL-terminates every
line (`Op.uint32_t\tctrl_tick 0\0Op.fp32\tctrl_pitch 0\0...`). Treat `\r`, `\n` and `\0` all
as line breaks.

**Type numbers are reassigned between firmware generations [observed]**, so recDef *names*
must take precedence over the type-number tables in section 5. On code-35 firmware:
type 5000 is `rtcm_data` (6 bytes), not a battery; 5001/5003 are `cb_can0/1`; battery cells
are in type 2396 `bat_dynamic_data` (27 bytes: u16 temperature, u16 voltage mV, i32 current,
i32 average current, u16 soc, u16 cell1..cell4 mV, u32 status, u8 poweroff); type 1000
`Controller` is 48 bytes with **fp32 sticks in -1..1** (`ctrl_tick u32, ctrl_pitch f4,
ctrl_roll f4, ctrl_yaw f4, ctrl_thr f4, ctrl_mode u8, mode_switch u8, ...`); type 1710
`battery_info` is 40 bytes (`Vol f4, Current f4, R_time u16, CellVol u16, LowVolThreshold f4,
AdVol u16, BatVol i32 mV, BatCurrent i32 mA, BatCap u16, CapPercnt u8, BatTemp i16 degC,
BatAuth u8, BatDataCnt u32, FullCap u32`); type 15000 `gps_utc_data` carries GPS/PPS UTC
fields and a measured `ticks_per_sec` (4,687,472 on FLY134 vs header 4,687,500). Types 12,
13 and 2096 have no recDef and keep their engineered layouts (validated, VALIDATION.md).
`dat_ref_impl.resolve_kinds()` implements this precedence.

Field types: `Op.uint8_t/int8_t/uint16_t/int16_t/uint32_t/int32_t` (1/1/2/2/4/4 bytes),
`Op.fp32`/`Op.float` (4), `Op.fp64`/`Op.double` (8); `Op.expr` lines are derived
expressions, no storage. Fields are packed with no padding in declaration order; the
trailing number is a default value. Parser: DC[BSD-style] DatConRecs/RecDef/OpConfig.java:83-251 and
RecDef/Field.java:65-112. The Mavic Pro app-side DAT defines 21 types this way, including
`IMU_ATTI_0`, `Controller`, `battery_info`, `esc_data` **[observed]**. Field names may
repeat (DatCon de-duplicates by suffixing, OpConfig.java:278-303).

Rule for the port: engineered layouts (5.1-5.7) are authoritative where validated; for any
other type, decode generically from the file's own recDefs; never guess.

---

## 6. Timebase

### 6.1 Ticks to seconds

`t_s = (tick - t0) / clock_hz`, clock_hz from header 0x30 (2.2) or the product table
(2.3). V1 = 600 Hz; V3 aircraft = 4.5 MHz. DatCon: DC[BSD-style] Files/DatFile.java:403-409
(`time = (tick - offset) / clockRate`). DatCon's `offsetTime` column subtracts an offset
chosen in its GUI; DroneDog's `t_offset_ms` is relative to the first accepted record.

### 6.2 32-bit rollover

At 4.5 MHz a u32 tick wraps after 954 s. Rule (DC[BSD-style] V3/Files/DatFileV3.java:155-173): when
the previous raw tick > 4,292,717,296 and the current raw tick < 2,225,000, add 2^32 to an
offset; for the next 100 records, a raw tick still > 4,292,717,296 uses the previous offset
(late stragglers). Ticks therefore have to be held in 64 bits after unwrapping.

### 6.3 Anchoring to UTC

The only absolute time source is the GPS record (5.4): decimal date/time with 1 s
resolution, at ~5 Hz. Method (DroneDog, documented in the methodology log):

1. For consecutive GPS records where hhmmss advances by exactly one second, the true
   second boundary S lies between the previous record's tick tp and this record's tick tc.
2. At the nominal clock rate each transition bounds the offset a = S - tick/rate to
   [S - tc/rate, S - tp/rate]. Intersect all intervals; use the midpoint, and state the
   half-width as the quantisation bound.
3. If the intervals do not intersect (clock drift, GPS glitches, app-side gaps), fall back
   to a least-squares fit epoch = a + tick/rate', report rate' and the max residual.
4. The bound excludes GPS receiver latency between fix time and record write (unknown,
   expected << 1 s); say so in the report.

A third clock source exists on 2020+ firmware: type 15000 `gps_utc_data` (5.10) with PPS
time and `ticks_per_sec`; it is not yet used by the anchor.

DatCon itself only prints `GPS:dateTimeStamp` per row (GpsGroup.java:160-176) and does
not interpolate. Observed: P3 V1 files intersect at the nominal 600 Hz with a +/-0.1 s
bound; the Mavic Pro app-side file needed the fallback (fitted 4,499,781 Hz, -49 ppm)
**[observed]**.

---

## 7. Encrypted DATs: recognition only

Recognise, report, stop. Do not attempt decryption (HANDOFF section 2; DECISIONS D7).

| signal | meaning | source |
|---|---|---|
| bytes 0-3 `LOGH` | encrypted DAT container. Visible: the 4-byte magic, 3 unknown bytes, u64 LE at 8 = offset of the encrypted body. Everything else is ciphertext. | DC[BSD-style] Files/DatFile.java:146-148; D3VL[MIT+Commons-Clause] config.js:10-13 |
| Assistant export entry whose data starts with `LOGH`, or whose product tag at entry offset 7 is a newer code (e.g. `G-E3T` = Goggles 2 per D3VL) | encrypted Assistant export (Avata, O3 Air Unit, Goggles 2): route to the D3VL adapter (HANDOFF coverage map), not this decoder | D3VL[MIT+Commons-Clause] README |
| Mini 2 FTP-retrieved "E2" DAT | per HANDOFF coverage map, handled by the external Fodogu tool; no sample in the corpus and no format details are documented here (Fodogu is unlicensed, D9). Recognise only by failing every plaintext test above and report as unsupported/encrypted. | HANDOFF.md section 3 |
| `BUILD` header present but zero CRC-valid records, or no 0x8000 record readable after XOR | damaged, or an unknown newer scheme | heuristic in `dat_ref.detect()` |

Plaintext metadata still available on an encrypted DAT: file name (FLYnnn sequence, app
date-time prefix), file size, file-system timestamps, the Assistant entry name/product tag,
and the LOGH body offset. Correlation/attribution then relies on those plus the paired app
TXT header (HANDOFF section 5, Correlation Engine).

Observation that matters for coverage: the 2023 app-side `MCDatFlightRecords` files of a
code-89 aircraft (SN prefix `1581F6GK`) are **not** encrypted at the record level: V3 header,
CRC-valid records, XOR-only payloads, plausible IMU/GPS/home values **[observed]**. Do not
assume "new aircraft = encrypted DAT"; detect by content.

---

## 8. Aircraft vs app-side vs controller DATs

| property | aircraft FLYnnn.DAT | app-side MCDatFlightRecords | controller DAT |
|---|---|---|---|
| location | aircraft internal microSD (or Assistant export) | device running the flight app, in a `MCDatFlightRecords` folder: phone/tablet (DJI GO 4, 2020 sample named `yy-MM-dd-HH-mm-ss_FLYnnn.DAT`) or a screen remote controller's internal storage (2023 sample, `yyyy-MM-dd_HH-mm-ss_FLYnnn.DAT`, copied off the RC per the isensuiuc README) | RC firmware's own logs: **[unverified]**, no sample |
| header | V1 or V3 | V3 (same product code, BUILD and serial fields as the aircraft) | |
| type 12 osd_general | present | **absent** (DatCon's "tablet" test) | |
| rates | IMU 50-200 Hz, OSD 10 Hz | decimated: IMU ~7 Hz, GPS ~4 Hz, controller ~3.6 Hz **[observed]** | |
| record damage | rare (< 0.03%) | common: truncated/interleaved records, ~0.03% (2020) to ~10% (2023) **[observed]** | |
| frames in DroneDog | full C3 | C3 without OSD-derived columns (alt_rel_m, flight_mode, gps_health, motor_on, is_flying are empty) | |
| source tag (C1/C5) | `aircraft-dat` | `controller-dat` (the controller/app side of the link) | |

The app-side file is the flight controller's own log stream relayed over the radio link
and written by the app, which is why it shares the FC header and record types. Because it
lacks type 12, height above takeoff must come from the barometer (IMU offset 16) minus the
take-off altitude (`[L-TAKEOFF]alti:`), exactly as DatCon does
(DC[BSD-style] Files/ConvertDat.java:340-367); DroneDog leaves `alt_rel_m` empty instead of deriving
it silently, and lists the barometric value as a separate, labelled derivation if needed.

---

## 9. Mapping to the C3 frame model (as implemented in dat_ref.py)

Rows are emitted with DatCon's sampler: a row at the first accepted record whose tick is
>= last row tick + clock/hz (DC[BSD-style] V3/Files/ConvertDatV3.java:60, 108-120; DatCon never
resets `processedPayload`, so effectively every record can trigger a row). Each row is the
sample-and-hold state of the latest decoded record of every type at that tick. Default
10 Hz.

| C3 column | source | notes |
|---|---|---|
| t_offset_ms | (tick - first accepted tick) / clock * 1000 | |
| t_utc | 6.3 | ISO-8601 Z, ms |
| lat, lon | IMU_ATTI_0 (5.3) radians -> deg; fallback type 12 | None when \|lat\|,\|lon\| < 1 deg (no fix; DatCon's 0.0175 rad test, DatFileV3.java:299-301) |
| alt_rel_m, height_m | type 12 @16 | 0.1 m |
| alt_msl_m | GPS height MSL (5.4 @16) | |
| vel_n/e/d | IMU_ATTI_0 @76/80/84 | m/s |
| speed_h_ms | sqrt(vn^2+ve^2) | = DatCon `velH` |
| roll, pitch, yaw | quaternion (5.3) | DatCon convention |
| gimbal_* | none | 5.8 |
| rc_aileron/elevator/throttle/rudder | type 1000 roll/pitch/thr/yaw / 10000 | clamped -1..1 |
| rc_mode_switch | type 12 controller-state bits 13-14: 0 -> "P", 1 -> "Sport", else `RC_MODE_n` | DatCon's mapping (GoTxt50_12.java:324-327); meaning of 2 **[unverified]** |
| flight_mode | FLYC_STATE name (5.1) | |
| gps_sats | IMU_ATTI_0 @116 (fallback type 12 @36) | |
| gps_health | type 12 GPS level (bits 18-21) | 0..5 |
| battery_pct, battery_v, battery_temp_c | type 5000 if present, else 1710 (`remaining %`, pack V, temp) | |
| battery_cells_v | type 5000 / V1 type 17 cells | 1710 only has the lowest cell |
| home_lat, home_lon | type 13 | |
| distance_m | haversine(IMU position, home), R = 6371 km (DC[BSD-style] Files/Util.java:18-32) | |
| motor_on | type 12 flags bit 3 | |
| is_flying | type 12 flags bit 2 (in air) | |

`details` (C2): aircraft_model (2.3), aircraft_sn (header serial, 2.2), fc_sn (`Mc  ID`),
battery_sn, firmware_version (`Mc Ver` + BUILD), start_time_utc (6.3), duration_s,
home, max height/speeds, record_line_count = CRC-valid records.

---

## 9a. DatCon versions and the CSVs used as ground truth

- DatCon 3.5 (open source, read here) and 3.7.6 (Svalbard CSVs) use column names like
  `IMU_ATTI(0):roll`, `GPS(0):Lat`, `General:relativeHeight`, `BattInfo:Remaining%`.
- DatCon 4.0.5 (EPFL CSVs, closed source) uses `Clock:Tick#`, `GPS:Lat`,
  `osd_data:relativeHeight`, and prints recDef fields verbatim with a `:D` suffix (raw mV,
  mA, degC, fp32 sticks); `:C` = computed. Its `GPS:hDOP` is the stored value / 100 on
  code-35 firmware (stored 96 -> printed 0.96); dat_ref keeps the stored value.
- Both put `Tick#` = tick of the record after which the row was printed, which is what
  makes exact row alignment possible (section 9, VALIDATION.md).

## 9b. Fast path (what the app runs)

`DatDecode(fast=None)` uses numpy when importable: (1) vectorised candidate search
(0x55, 10-bit length, protocol version 0, header CRC-8), (2) a single Python walk over
candidates following record lengths, checking CRC-16 only where the chain does not land
exactly on the next candidate (resync rule of 3.4), (3) DatCon's sequence rule as
`tick > running max`, (4) the DatCon row sampler via `searchsorted`, (5) CRC-16 check, XOR
and decode only for the records that are current at a frame row, plus all text, GPS and
home records. On FLY134 (171 MB, 18 min, 3.29 M records) this takes about 3.5 s versus
15 s for the full scan, and gives identical DatCon parity (VALIDATION.md). Without numpy
the full scan runs (`--full` forces it).

## 10. Pitfalls (each one has bitten an existing tool)

1. **Do not abort on damage.** DatCon stops at 2% damaged records (3.5); app-side files
   exceed that routinely. Resync and count.
2. **Resync from the start byte + 1**, not from start + declared length: damaged lengths
   are common, and records can begin inside a truncated record (3.4).
3. **Check the header CRC-8.** It is valid on every good record and rejects most false
   0x55 starts for the cost of 3 table lookups.
4. **Ticks wrap** at 2^32 (~16 min at 4.5 MHz); unwrap to 64 bits (6.2).
5. **Duplicate and out-of-order ticks**: drop for frame building (DatCon's rule) but count
   them; V1 tick groups legitimately share one tick.
6. **Clock rate**: use the header field; DatCon's table is wrong for at least the 2023
   file (3.84 MHz) and later models. Measure drift via 6.3 and report it.
7. **Layouts vary by payload length** for the same type (types 12, 13, 1000, 1710, 2096,
   5000, 10090). Dispatch on (type, length), and prefer the file's recDefs.
8. **0,0 coordinates** and tiny radian values mean "no fix", never a location (C3 rule).
9. **Units hidden in names**: positions are radians in IMU/OSD/home records but 1e-7 deg
   in GPS; GPS velocities are cm/s floats; IMU temperature is 0.01 degC in V3 but whole
   degC in V1; battery current sign is flipped by DatCon.
10. **Two DatCon class families disagree** on GPS offsets 40-55 (5.4); keep them out of
    evidentiary columns.
11. **App-side DATs have no type 12**, so anything derived from OSD is absent there (8).
12. **Assistant exports may hold several FLY files**; DatCon reads only the first.
13. **Text records** need NUL-termination and CR stripping; DatCon replaces commas with
    `.` inside its CSV `eventLog`.
14. **Java float formatting**: DatCon prints float32 values with `Float.toString`, which
    round-trips; compare at float32 precision, not to a fixed number of decimals.
15. **Never infer encryption from the model name** (7): detect by content.
16. **Type numbers are not stable across firmware**; trust the file's recDef names (5.10).

---

## 11. Test vectors for the port

Generated by `tools/dat_ref.py`; files referenced by SHA-256 in
`corpus/baselines/dat/VALIDATION.md` and `corpus/baselines/dat/corpus_scan.md`.

1. CRC: `crc16_dji(b"\x55\x0e\x00")` etc. -- the Rust unit test should compare the
   table-driven and the bitwise forms over random buffers and over the first 1000 records
   of each corpus file (every one must validate).
2. Header CRC-8 seed 0x77 over bytes 0..2 of every record in the corpus files: 100% match.
3. XOR: first record of `20-07-10-08-31-09_FLY256.DAT` at offset 256, type 0x8000, tick
   9,923,440 (key 0x70) decodes to `{root}INFO:lib_route_init ok`.
4. First record of `FLY003.DAT` (DROP sample) at offset 128, type 0x8000, tick 466 (key 0xD2)
   decodes to `      0 IST8303[2:0x18]:Init...`.
5. Header serial of `20-07-10-08-31-09_FLY256.DAT` = `08RDE4E00102YL`, clock 4,500,000.
6. Per-file record counts / CRC failures / type histograms: `corpus/baselines/dat/records_*.txt`
   (golden output of `dat_ref.py --records`).
7. Field-level golden values: DatCon CSV rows in the AFDYLS dataset (VALIDATION.md).

---

## 12. Port plan: `core/src/dat/` (Rust, `dronedog_core`)

Module breakdown (one-to-one with `tools/dat_ref.py` sections, so diffs stay reviewable):

| module | contents | dat_ref.py counterpart |
|---|---|---|
| `dat/mod.rs` | `detect(path) -> Detection`, `parse(path) -> ParseResult` (C1 shapes); routes Assistant/LOGH/V1/V3/headerless | `detect`, `parse_dat` |
| `dat/crc.rs` | `crc16_dji(&[u8]) -> u16` (const-generated 0x8408 table, seed 0x3692), `crc8_dji` (0x8C table, seed 0x77) | `crc16_dji_slow`, `crc8_dji` |
| `dat/header.rs` | `DatHeader` parse (2.2), product table (2.3), XOR-0x55 serial | `parse_header`, `PRODUCTS` |
| `dat/container.rs` | Assistant export: inflate (`miniz_oxide`/`flate2`, no network), iterate 283-byte entries, return *all* FLY entries with inflated offsets; LOGH recognition | `assistant_entries` |
| `dat/framing.rs` | zero-copy `RecordIter<'a>` over `&[u8]`: zero-pad skip, CRC-8 then CRC-16, resync at start+1, truncated-tail stop, 64-bit tick unwrap, XOR into a reused `Vec<u8>`; `FrameStats` | `iter_records`, `FrameStats` |
| `dat/accept.rs` | DatCon acceptance (jump > 22e6, sequence; V1 equal-tick groups) with counters | `DatDecode.run` (rules block) |
| `dat/records/{osd,home,imu,gps,controller,battery,esc,text}.rs` | decoders dispatched on `(type, payload_len)`; return typed structs; errors are counted, never panic | `dec_*` |
| `dat/recdefs.rs` | 0xFFFD parser and generic field decoder | `parse_recdefs`, `decode_by_recdef` |
| `dat/timebase.rs` | rollover, `UtcAnchor` (interval intersection, LS fallback, bound) | `UtcAnchor` |
| `dat/frames.rs` | sample-and-hold sampler (DatCon cadence, configurable Hz, or explicit tick list for parity tests) -> C3 columns via `frame.rs` | `DatDecode.frames` |
| `dat/events.rs` | text records -> C1 events, severity heuristics, attribution extraction (Mc ID, battery SN) | `_finish`, `_details` |

Port rules: no `unsafe` needed; all reads bounds-checked (`get(..)`), never index past the
payload; a malformed record must cost a counter increment, not a panic. Emulate Java
float32 rounding where DatCon parity requires it (`(x as f64 / 1000.0) as f32`).

Test vectors to carry into `core/tests/`:

1. CRC vectors in 3.3 (`123456789` -> 0x7109 / 0xFB; FLY256 record @256).
2. XOR vectors in 11.3-11.4; header vector 11.5.
3. Golden per-file record statistics: `corpus/baselines/dat/records/*.txt` and
   `corpus_scan.json` (records_ok, damage counts, type histogram, frame counts,
   UTC start), keyed by SHA-256 so tests skip cleanly when a corpus file is absent.
4. DatCon parity fixtures: `corpus/baselines/dat/fixtures/*.json` (sampled DatCon CSV rows,
   CC0 data) with tick + expected values for each validated field; the Rust test runs the
   explicit-tick sampler at those ticks and asserts the VALIDATION.md tolerances.
5. Robustness: truncation at random offsets, random byte flips, inserted garbage and
   zero runs (as in the dat_ref fuzz run) must never panic and must keep
   `records_ok + damaged` consistent.

## 13. `fc_log.log` ring log (`MISC/LOG/flylog/`)

**[observed]** on one sample, which is not public: the exFAT SD card of a DJI aircraft that
writes V3-era DATs, with `MISC/LOG/camera_log.log`, `MISC/LOG/linux_log.log` and
`MISC/IDX/idx00..idx11` alongside. The file is preallocated (512 MiB) and holds several complete
V3 DATs written one after another into a ring. The layout has not been checked against a second
sample.

| Offset | Size | Field |
|---|---|---|
| 0x00 | u32 | write position (end offset of the newest log) |
| 0x04 | u32 | number of index entries |
| 0x08 | 24 | 0xFF |
| 0x20 + 64*k | 64 | index entry k, in write order |

Index entry (64 bytes, little-endian):

| Offset | Size | Field |
|---|---|---|
| 0x00 | u32 | log number (increasing by one per log; matches FLY numbering **[unverified]**) |
| 0x04 | u32 | start offset of the log in the file |
| 0x08 | u32 | end offset |
| 0x0C | u32 | length |
| 0x10 | 4 | ASCII `LGY ` |
| 0x14 | 6 x u32 | month, day, year, hour, minute, second. Meaning and time zone not established: in the sample these differ from the log's own GPS-derived start by more than a day |
| 0x2C | 3 x u32 | the same three values in every entry of the sample (meaning not established) |
| 0x38 | 8 | 0xFF |

Ring behaviour: when `end < start`, the log wraps from the end of the file to the start of the
data area. The data area start is fixed by that entry: `size - start + end - length` (0x40000 in
the sample). Logs are written in index order, so an earlier log's bytes that fall inside a later
log's range are overwritten. In the sample, later logs had overwritten the start of the oldest
log, leaving its tail without a DAT header.

DroneDog (`dronedog/ingest/walker.py`, `fclog_index`): recognises the file by the `LGY ` tag at
0x30 and a plausible entry count, extracts each log from its index entry as
`fc_log.log!/fc_log_NNN.DAT` (wrapped logs rejoined; provenance records log number, index slot,
start/end/length, the byte ranges read and the raw index time), stops a log at the first byte a
later log overwrote, and reports a log whose start was overwritten with its surviving byte ranges
instead of extracting it. Each extracted log then goes through normal DAT detection and decoding;
in the sample every extracted log starts with the standard V3 header.
