# DroneDog — Architecture & Interface Contracts (v1)

Read `HANDOFF.md` (brief) and `DECISIONS.md` (overrides) first. This file freezes the
contracts that parallel workstreams build against. Change a contract only by editing
this file and bumping the version noted on that section.

## Layout

```
DroneDog/
  core/                     Rust crate `dronedog_core` (PyO3 cdylib, built with maturin)
    src/lib.rs              Python bindings only; logic lives in modules
    src/detect.rs           format detection
    src/txt.rs              DJI app TXT logs via dji-log-parser (library, no network)
    src/dat/                aircraft/controller DAT framing + record decoding
    src/frame.rs            normalized columnar frame builder
  dronedog/                Python package (app layer)
    case/                   Case model, SQLite store, custody/methodology log
    ingest/                 sources: loose files, folders, zips, disk images, mounted SD
    adapters/               ParserAdapter implementations (thin, call dronedog_core)
    analysis/               timeline, reconstruction, correlation, event classifier
    report/                 PDF / KML / GeoJSON / JSON bundle / hash manifest
    ui/                     pywebview host + js_api bridge
      web/                  index.html, app.js, styles.css, vendor/ (pinned, offline)
    cli.py                  `python -m dronedog ...`
  tests/                    pytest; Rust tests live in core/tests
  tools/                    corpus scripts (build-time only, never shipped)
  corpus/                   test data (git-ignored except INDEX.md + baselines/)
```

Python: 3.13, venv at `.venv`. Rust: stable MSVC. Build the core into the venv with
`.venv\Scripts\maturin develop -m core\Cargo.toml --release`.

**Runtime has zero network calls.** Never call dji-log-parser's `fetch_keychains`.
All JS/CSS/fonts/map data are vendored under `ui/web/vendor/`. No CDN references.

## C1. Rust core API (`import dronedog_core`) — v1

All functions take a filesystem path (str) and never write to it.

```python
dronedog_core.version() -> str

dronedog_core.detect(path: str) -> dict
# {
#   "kind": "dji_txt" | "dji_dat" | "unknown",
#   "subtype": str | None,    # "txt_v<N>" | "dat_v3" | "dat_legacy" | "dat_encrypted"
#                             #   | "dat_assistant_zlib" | "dat_unknown"   (v1.1)
#   "version": int | None,    # TXT log version byte, or DAT format version
#   "encrypted": bool | None, # records not readable without a key/decryptor
#   "confidence": float,      # 0..1
#   "reasons": [str],         # human-readable evidence for the decision (goes in methodology log)
# }

dronedog_core.parse_txt(path: str, keychains_json: str | None = None) -> dict
dronedog_core.parse_dat(path: str) -> dict
# Both return a ParseResult:
# {
#   "adapter": "txt_djilog" | "dat_v3" | ...,
#   "adapter_version": str,             # crate version + upstream lib version
#   "details": { ...plaintext header/metadata, see C2... },
#   "frames": { column_name: [values...] },   # columnar, all lists same length, see C3
#   "events": [ {"t_offset_ms": int, "kind": str, "severity": "info"|"warn"|"critical",
#                "message": str, "raw": str|None} ],
#   "records_total": int, "records_decoded": int, "records_failed": int,
#   # v1.1: records_total = decoded + failed + details["records_unknown_type"]
#   "warnings": [str],                   # parse anomalies, stated plainly
#   "encrypted_unreadable": bool,        # true => frames empty, details still filled
# }
```

## C2. `details` keys (any may be null)

`aircraft_model, aircraft_name, aircraft_sn, fc_sn, camera_sn, rc_sn, battery_sn,
app_platform, app_version, log_version, start_time_utc (ISO-8601 Z), duration_s,
total_distance_m, max_height_m, max_h_speed_ms, max_v_speed_ms, home_lat, home_lon,
city, area, sub_street, firmware_version, record_line_count`

v1.1 additions (blessed): `street, product_type_code, photo_count, video_time_s,
firmware_versions{}, component_serials{}, records_unknown_type`; DAT adds `dat_subtype,
dat_build_string, dat_detect_confidence, file_size_bytes`. Further adapter-specific keys
go under `details["x"] = {...}` so the top level stays a stable contract.

## C3. Normalized Frame columns — schema v1 (FROZEN after Phase 1; extend, never rename)

SI units internally (m, m/s, deg, °C, V). Missing = None.

| column | type | notes |
|---|---|---|
| t_offset_ms | int | from first frame; may be None before the first valid timestamp |
| t_utc | str | ISO-8601 Z if derivable |
| lat, lon | float | WGS84 deg; None if no fix (never 0,0) |
| alt_rel_m | float | relative to takeoff |
| alt_msl_m | float | |
| vel_n, vel_e, vel_d | float | m/s |
| speed_h_ms | float | horizontal ground speed |
| roll, pitch, yaw | float | deg |
| gimbal_pitch, gimbal_roll, gimbal_yaw | float | deg |
| rc_aileron, rc_elevator, rc_throttle, rc_rudder | float | -1..1 |
| rc_mode_switch | str | |
| flight_mode | str | |
| gps_sats | int | |
| gps_health | int | 0..5 |
| battery_pct | float | |
| battery_v | float | |
| battery_cells_v | list[float] | |
| battery_temp_c | float | |
| home_lat, home_lon | float | |
| distance_m | float | from home, computed (haversine) by DroneDog |
| height_m | float | same source as alt_rel_m (OSD height); kept for compatibility |
| motor_on | bool | |
| is_flying | bool | |
| vps_height_m | float | v1.1: downward vision/ultrasonic height above ground |
| distance_app_m | float | v1.1: app-recorded distance as logged; cumulative path distance in TXT v8+, NOT distance from home |
| rc_signal_up_pct, rc_signal_down_pct | float | v1.1: link quality 0..100 |

Sign conventions: vel_n/e/d are North-East-Down (vel_d positive = descending).
RC sticks: (raw-1024)/660 clamped to [-1,1].

## C4. Python ParserAdapter — v1 (`dronedog/adapters/base.py`)

```python
class ParserAdapter(Protocol):
    name: str                      # "txt_djilog"
    def accepts(self, detection: dict) -> float: ...   # 0..1 confidence
    def parse(self, path: Path, ctx: ParseContext) -> dict: ...   # returns ParseResult (C1)
```
Registry picks the highest `accepts()`; ties → first registered. Adapters never touch
the evidence store directly; they get a path to a read-only working copy.

## C5. Case store — v1

A case is a folder:
```
<Case>/
  case.json            id, name, examiner, agency, case_number, created_utc, tool_version
  case.db              SQLite (schema below)
  evidence/            original inputs copied verbatim (read-only attribute set)
  extracted/           artifacts pulled out of images/zips, named <sha256[:12]>_<origname>
  exports/             reports
  custody.log          append-only JSONL, hash-chained (see C6)
```

SQLite tables (minimum):
- `evidence(id PK, label, src_path, kind['file'|'folder'|'zip'|'image'|'sd'], size, sha256, sha1, added_utc, examiner)`
- `artifacts(id PK, evidence_id FK, rel_path, inner_path, offset, size, sha256, sha1, detected_kind, detected_subtype, detection_json, status['pending'|'parsed'|'failed'|'unsupported'|'encrypted'], error)`
  `inner_path` = path inside image/zip; `offset` = byte offset in image when known.
- `flights(id PK, artifact_id FK, adapter, adapter_version, source['aircraft-dat'|'controller-dat'|'app-txt'], details_json, start_utc, duration_s, frame_count, bbox_json, warnings_json)`
- `frames(flight_id, idx, <C3 columns>)` (battery_cells_v as JSON text) — index (flight_id, idx)
- `events(id PK, flight_id, t_offset_ms, kind, severity, message, raw)`
- `links(id PK, flight_a, flight_b, kind, score, reasons_json)` (correlation, Phase 3)

## C6. Custody / methodology log — v1

`custody.log`, one JSON object per line:
`{"seq": n, "ts_utc": "...", "actor": "examiner|system", "action": "...", "inputs": [{"path","sha256"}], "outputs": [...], "tool": "dronedog", "tool_version": "...", "component": "ingest.image", "params": {...}, "prev_hash": "<sha256 of previous line>", "hash": "<sha256 of this line without 'hash'>"}`
Actions: `case.create, evidence.add, evidence.verify, artifact.extract, artifact.detect,
artifact.parse, analysis.run, report.export`. `verify_chain()` must detect any edit.

## C7. UI bridge — v1 (`dronedog/ui/api.py`, exposed as `window.pywebview.api`)

All methods return JSON-serializable dicts; long jobs return `{"job_id"}` and progress
is pushed via `window.dronedog.onProgress(job)` evaluated from Python.

```
case_new(meta) / case_open(path) / case_info()
pick_paths(kind: 'files'|'folder'|'image')        -> [paths]  (native dialog)
import_paths(paths: [str])                         -> {job_id}  (ingest+extract+detect+parse)
job_status(job_id)
list_evidence() / list_artifacts() / list_flights()
get_flight(flight_id, max_points=5000)             -> {details, frames(columnar, decimated), events}
get_track_geojson(flight_ids: [int])
basemaps() / tile(basemap_id, z, x, y)             -> offline tiles (base64 png/jpg)
export_report(flight_ids, formats: ['pdf','kml','geojson','json'])
verify_custody()
```
