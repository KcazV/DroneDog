# DroneDog validation status

**DroneDog is a training and research tool. It has not been independently validated for
casework.** It has not been tested by a third party (for example NIST CFTT), peer reviewed, or
subjected to an error-rate study. Anyone considering it for casework must validate it under their
own laboratory's procedures and verify every finding with an independent, validated tool.

This page states what has been tested, how, and what has not. It applies to DroneDog 0.2.x
(October 2026).

## Tested against known data or an independent tool

| Function | How it was tested | Evidence |
|---|---|---|
| Evidence hashing (SHA-256, SHA-1, MD5), verify-after-copy, read-only handling | Automated tests; imager sidecar hashes compared | `tests/test_case_store.py`, `tests/test_ingest_*.py` |
| E01 media hash compared with the hash stored in the E01 | NIST CFReDS E01 images | `docs/VALIDATION_RUN.md` |
| Hash-chained custody log, tamper detection | Automated tests | `tests/test_custody_chain.py` |
| Plaintext DJI aircraft DAT decoding (V3, V1 events) | Compared with DatCon output on public datasets (DataverseNO AFDYLS Phantom 4 Pro, DROP Phantom 3); record CRC-16 checked | `corpus/baselines/dat`, `docs/DAT_FORMAT.md` |
| DJI app TXT flight records, versions up to 12 | Decoded by the third-party dji-log-parser library; regression baselines kept | `corpus/baselines/txt` |
| Deleted-file recovery on FAT | Constructed SD image with a known deleted log; recovered bytes match the original SHA-256 | `tests/test_ingest_image.py` |
| End-to-end import | 20 public inputs (NIST CFReDS / VTO Labs, DataverseNO, public samples): 320 artifacts, 0 processing failures | `docs/VALIDATION_RUN.md` |

"0 processing failures" means every input was processed without error. It does not mean every
decoded value was checked against ground truth.

## Implemented but not validated

| Function | Status |
|---|---|
| `fc_log.log` ring log splitting (`MISC/LOG/flylog/`) | Layout worked out from **one** sample, which is not public. Index date fields and three index values not understood. See `docs/DAT_FORMAT.md` section 13 |
| Tar / tar.gz / Android "quick image" ingest | Tested on synthetic archives and one real extraction, which is not public |
| Flight classification (flight, GPS movement, motor start, power-on) | DroneDog's own thresholds (in-air flag 2 s; height 1 m / 2 m; GPS movement 50 m). Not validated against ground truth |
| Identifier extraction and labels | Pattern-based. Several values are labelled as unproven or not established (DAT header ID, GEO-module user ID). Product codes beyond those documented show as "Unidentified DJI" |
| Correlation of aircraft and app logs | Rule-based (serial, time overlap, home point, track distance); not validated against a ground-truth set |
| Event explanations | DroneDog's reading of flight-controller text; the original text is always shown beside it |
| Place names | Nearest named populated place from the offline GeoNames gazetteer; not addresses or boundary determinations |
| Height before the first GPS fix | Excluded by rule; not validated |

## Known gaps

- Encrypted DATs, and app TXT v13/v14 flight records, are reported from their plaintext headers only.
- V1-era Agras DATs: events and serials only, no position track.
- One DAT extracted from an `fc_log.log` decodes no telemetry although its records pass their CRC checks; under investigation.
- Raw NAND dumps and flash file systems (UBIFS, YAFFS2, JFFS2) are not supported.
- ext file systems on drone storage are untested.
- Autel, Parrot and Skydio are not supported.

## Before using DroneDog on a case

1. Validate it under your laboratory's procedures, on data representative of the case.
2. Verify each finding with an independent, validated tool (for example DatCon / CsvView for DATs).
3. Record the DroneDog version and core version (shown in the app and in every report).
4. Keep the original evidence hashes from your imaging tool; DroneDog's hashes are a check, not a substitute.
