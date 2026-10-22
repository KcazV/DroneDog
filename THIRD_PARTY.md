# DroneDog — Third-party notices

DroneDog bundles or builds on the following third-party software and data. Full license
texts ship alongside the respective components (see `dronedog/ui/web/vendor/VENDOR.md`
for every vendored web asset with version, source URL, license and SHA-256).

## Bundled

| Component | Use | License |
|---|---|---|
| dji-log-parser 0.5.7 (Luc Vauvillier) | DJI app TXT log parsing (Rust library, linked into `dronedog_core`) | MIT |
| Rust crates in the dji-log-parser / PyO3 dependency tree | runtime support | MIT / Apache-2.0 / ISC / BSD / Zlib / Unicode-3.0 |
| PyO3 | Rust ↔ Python bindings | Apache-2.0 OR MIT |
| GeoNames cities500 + admin1/admin2/countryInfo (downloaded 2026-10-22, filtered, re-encoded) | offline reverse geocoding (nearest named place), `dronedog/data/places.npz` | CC BY 4.0 — Contains GeoNames data, CC BY 4.0, geonames.org |
| CPython 3.13 | runtime | PSF License |
| pywebview 6.2.1 | desktop window host | BSD-3-Clause |
| pythonnet / clr-loader | pywebview Windows backend | MIT |
| pytsk3 / The Sleuth Kit 4.15 | disk image volume + file-system walking | Apache-2.0 (bindings); TSK: IPL-1.0 / CPL-1.0 / others (audit before redistribution) |
| libewf / pyewf (Joachim Metz) | E01/Ex01 reading | LGPL-3.0-or-later, shipped as a separate replaceable library |
| NumPy | DAT decoding | BSD-3-Clause |
| Leaflet 1.9.4 | maps | BSD-2-Clause |
| uPlot 1.6.32 | charts | MIT |
| Lucide icons | UI icons | ISC |
| Inter, JetBrains Mono | fonts | SIL OFL 1.1 |
| Natural Earth | offline basemap | Public domain |

## Used as format references / validation baselines (no code copied)

| Source | Use | License |
|---|---|---|
| DatCon / CsvView (BudWalkerJava) | DAT record layouts; golden CSV baselines | permissive custom licence (disclaimer reproduced below) |
| DJI FlightRecordParsingLib | TXT field semantics cross-check | MIT (portions) |
| o-gs/dji-firmware-tools | DUML/DAT framing reference (read only) | GPL-3.0 |
| DROP (Clark et al., 2017) | DAT reference + sample data | MIT |

DatCon disclaimer: THIS SOFTWARE IS PROVIDED BY ITS CREATOR "AS IS" AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL
THE CREATOR OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE.

## Test data (not shipped)

DataverseNO doi:10.18710/AFDYLS (CC0); NIST CFReDS / VTO Labs Drone Forensics Program
images; public GitHub/Zenodo/Hugging Face samples listed per source in
`corpus/raw/*/SOURCE.md`.
