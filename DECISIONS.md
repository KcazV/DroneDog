# DroneDog — Decisions

Running log of product and architecture decisions. Newest at the bottom.
`HANDOFF.md` is the original brief; where this file differs, this file wins.

## 2026-10-21 — Scope reset by operator

**D1. Start-to-finish, self-contained.** The examiner should not need FTK Imager,
DatCon, CsvView, dji-log-parser or any other external tool. AeroTrace ingests,
extracts, parses, maps, analyzes and reports on its own. External tools are used
only by us, at build time, as spec sources and baseline generators for tests.

**D2. Input = device extractions.** Accept both:
- loose artifacts and folders (FLYxxx.DAT, DJI Assistant exports, DJIFlightRecord_*.txt,
  phone logical extractions / backups as zip or folder), and
- raw images (.E01, .001/.dd/.img, split sets) and mounted SD cards. AeroTrace walks
  the filesystem (FAT/exFAT first), finds every flight-log artifact and extracts it
  with source offset + hash recorded.
Bulk import: drop a folder or a set of images and every DAT/TXT is imported in one pass.

**D3. Native desktop window.** Python core + HTML/JS UI hosted in a pywebview window,
shipped as a single Windows .exe (PyInstaller). No browser, no listening port exposed
off-host.

**D4. Fully air-gapped.** No network calls at runtime, ever, unless the examiner
explicitly runs the keychain step (D5) on a connected machine. Maps use offline tiles:
a bundled low-zoom world basemap + an importable MBTiles/PMTiles pack for regional
street/satellite detail, with a plain coordinate grid fallback.

**D5. DJI keychain (TXT v13+): no API key.** Operator has no DJI developer key.
v13+ TXT logs get their plaintext header (model, serials, start time, app version,
totals) plus correlation/attribution; record content is reported as encrypted.
The keychain path stays a dormant slot: if a key is ever obtained, the air-gapped
flow is export keychain *request* file → fetch on a connected machine → import and
cache beside the evidence, logged in the methodology log. Not built in v1.

**D6. Language: Rust core + Python app layer.** (Operator: "Rust pref and Python, or
whatever's best.")
- `core/` Rust crate, exposed to Python via PyO3/maturin: format detection, DAT
  record framing + decoding, TXT parsing (the MIT `dji-log-parser` crate used as a
  library with network features off), hashing. Binary-heavy, speed-sensitive,
  single compiled module.
- Python: image/filesystem extraction (pytsk3 + pyewf), case + custody log,
  analysis, reporting, pywebview host.
Toolchain: Rust stable (MSVC) installed via rustup 2026-10-21; Python 3.13 venv at
`.venv`. External tools (DatCon etc.) are used only to produce golden baselines.

**D7. Boundaries unchanged** (HANDOFF §2): no cipher breaking, no key extraction, no
device exploit code. Encrypted aircraft DATs are parsed only via published
decryption paths named in the handoff; otherwise they get attribution/correlation
only, stated plainly in the report.

**D8. Name + brand: DroneDog.** (Operator, 2026-10-21.) Replaces the working name
AeroTrace everywhere user-facing and in code: Python package `dronedog`, Rust crate
`dronedog_core`, CLI `python -m dronedog`, exe `DroneDog.exe`. Logo: golden retriever
holding a drone, in `assets/brand/` (transparent PNGs + multi-size `dronedog.ico` built
by `tools/make_brand.py`). Palette taken from the logo: retriever gold `#E0A43A`
(accent), deep navy `#14234F`, outline black `#0B0D12`, cream `#F6E7C1`. The repo
folder stays `AeroTrace` until integration to avoid breaking in-flight work.

**D9. Licensing rules for third-party code (checked 2026-10-21).**
- Bundled (permissive, keep notices in THIRD_PARTY.md): dji-log-parser (MIT), every Rust
  crate in its tree (MIT/Apache/ISC/BSD/Zlib/Unicode), PyO3 (Apache-2.0), pywebview
  (BSD-3), Leaflet (BSD-2), uPlot (MIT), Lucide (ISC), Inter/JetBrains Mono (OFL),
  Natural Earth (public domain), Pillow (HPND), pytsk3 (Apache-2.0 bindings; TSK
  itself is a mix of licenses, so audit before the first release).
- libewf / pyewf: LGPL-3.0. OK to ship as a separately replaceable dynamic library with
  notice + source offer; never statically link or modify it in-tree.
- DatCon / CsvView: permissive custom licence (redistribution allowed with its
  disclaimer). Used as a format spec + baseline generator only; no code copied. Credit
  it in THIRD_PARTY.md anyway.
- dji-firmware-tools (GPL-3.0): read-only reference. Never copy code into DroneDog.
- DJI FlightRecordParsingLib: MIT (portions). Reference only.
- **D3VL DJI-DAT-Decryptor: MIT + Commons Clause, which forbids selling a product
  whose value derives substantially from it. Do not bundle, port or copy it.**
- **Fodogu: no licence = all rights reserved. Do not bundle, port or copy it.**
  The HANDOFF coverage rows for Avata/O3/Goggles 2 and Mini 2 E2 are therefore on hold
  until the operator gets written permission/a commercial licence from the authors, or
  counsel clears an independent path. Until then those DATs get recognize + attribution only.
- Test data: DataverseNO AFDYLS is CC0. CFReDS/VTO Labs is a public research dataset; use
  it for testing/validation, don't ship it inside the product.

**D10. Demo-first (2026-10-21 18:10).** Operator is showing DroneDog in class on
2026-10-22. It is not being sold now (maybe later, low odds). Priorities until the demo:
one polished end-to-end path — create case → drop a device image / folder / zip →
DroneDog finds every DAT/TXT (incl. deleted) → flights list → map + playback + charts →
custody verify → report export. Breadth (more formats, Rust DAT port, correlation,
classifier) waits. D9 licensing still stands as the rule for anything we ship; for a
non-commercial class demo it is not a blocker, but we are not spending demo time on
D3VL/Fodogu integration.

**D11. Optional online maps + visible network state (2026-10-21, operator).** For class
use he wants full-USA satellite and street maps over Wi-Fi. This amends D4: DroneDog is
offline by default, and the examiner can turn on "Online maps". In that mode only map
tiles are fetched (USGS Imagery and USGS Topo, both public domain; OpenStreetMap street
tiles under its tile policy: identified User-Agent, attribution, no bulk prefetch), and
only by the Python backend through one monitored choke point. The webview is locked by a
CSP so it cannot reach the network itself. A top-bar light shows green/Offline or
red/Online with a detail panel (hosts, purposes, bytes, plus the OS-level sockets of the app
and its WebView2 children). Toggling is written to the custody log. The disclosure shown:
tile requests reveal the viewed map area to the provider; no evidence or case data is sent.

**D12. Examiner-first output (2026-10-22, operator, after the class demo).** Practicals ask:
UUID / serials, battery serial, when were the flights, did it take off and how do you know,
map of the path, what city/state. So:
- Every flight record gets a derived summary (`dronedog/analysis/summary.py`, stored in
  `flights.analysis_json`, never inside the hashed parse result): classification (real flight /
  motors only / power-on / events-only / encrypted / empty) with the evidence and rule used,
  takeoff/landing time + place, home point timeline (home in effect at takeoff, updates, RTK
  reference), RTH periods, stats, every identifier found (with the log line it came from), and
  a key-moment timeline. DJI writes a DAT at every power-on, so the Flights list defaults to
  "Real flights only".
- City/county/state come from an offline GeoNames gazetteer (nearest named place, CC BY 4.0),
  worded as "N km DIR of Place", never as an address.
- Events keep the original text and add a plain-English explanation (`analysis/explain.py`,
  glossary in `docs/EVENT_GLOSSARY.md`).
- The report is a handoff exhibit: per real flight a static map (track, takeoff, landing, home),
  narrative, identifiers, timeline; non-flight power-on records listed with their reason.
- Case form: no placeholder text and no default case location; the examiner picks the folder.
- Import: one "Add evidence item(s)" picker for any type; content sniffing decides E01/raw/DAT/
  TXT/zip/folder.
- Online maps add Esri World Imagery (sub-metre, z19, worldwide) because USGS imagery stops at
  z16. Esri's basemap terms require attribution and restrict commercial use without an ArcGIS
  account; acceptable for this non-commercial class use (D10), revisit before any sale.
