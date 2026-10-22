"""End-to-end validation run: import every test input through the real pipeline.

Each job is a fresh case (evidence referenced in place, never copied or modified), run with
the same code path as the UI (expand_inputs -> ingest -> extract -> detect -> parse ->
summary). Writes docs/VALIDATION_RUN.md and docs/validation_run.json.

    python tools/corpus_run.py [--out DIR] [--only NAME ...]
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dronedog import __version__  # noqa: E402
from dronedog.case import Case  # noqa: E402
from dronedog.pipeline import ImportOptions, run_import  # noqa: E402
from dronedog.service import Service  # noqa: E402

C = ROOT / "corpus" / "raw" / "cfreds"
JOBS = [
    ("DF040 Agras MG-1S internal SD (E01)", "CFReDS / VTO Labs", [C / "DF040/2017_December/SDCard_Internal_3F/df040_microSD_Internal_3F.E01"]),
    ("DF041 Agras MG-1S internal SD #2 (folder holding an E01)", "CFReDS / VTO Labs", [C / "DF041/2017_December/SDCard_internal_acquisition_2"]),
    ("Constructed P4P internal SD image, deleted FLY140 (raw .dd)", "DroneDog demo (built from EPFL CC0 DATs)", [ROOT / "corpus/demo/DroneDog_DEMO_P4P_internalSD_CONSTRUCTED.dd"]),
    ("DF001 Phantom 3 flight logs (zip)", "CFReDS / VTO Labs", [C / "DF001/2018_June/flight_logs/flight_logs.zip"]),
    ("DF010 Inspire 1 flight logs (zip)", "CFReDS / VTO Labs", [C / "DF010/2018_June/flight_logs/flight_logs.zip"]),
    ("DF019 Mavic Pro iOS backup (zip)", "CFReDS / VTO Labs", [C / "DF019/2017_August/mobile_iOS_backup/df019_flight_ios_backup.zip"]),
    ("DF019 Mavic Pro DJI Assistant export (zip)", "CFReDS / VTO Labs", [C / "DF019/2018_June/DJI_Assistant_export/DJI_Assistant_Export.zip"]),
    ("DF007 Spark DJI Assistant export (zip)", "CFReDS / VTO Labs", [C / "DF007/2018_June/DJI_Assistant_export/DJI_Assistant_Export.zip"]),
    ("DF007 Spark iOS backup (zip)", "CFReDS / VTO Labs", [C / "DF007/2018_June/mobile_iOS/ios_backup.zip"]),
    ("DF008 Spark DJI Assistant export (zip)", "CFReDS / VTO Labs", [C / "DF008/2018_June/DJI_Assistant_Export/DJI_Assistant_Export.zip"]),
    ("DF034 Matrice 600 Pro iOS backup (zip)", "CFReDS / VTO Labs", [C / "DF034/2018_June/mobile_iOS_backup/ios_backup.zip"]),
    ("DF043 S1000+ iOS backup (zip)", "CFReDS / VTO Labs", [C / "DF043/2018_January/mobile_iOS_backup/iOS_Backup.zip"]),
    ("DF049 Mavic Air iOS backup (zip)", "CFReDS / VTO Labs", [C / "DF049/2018_April/mobile_iOS_backup/MC 05 iOS.zip"]),
    ("DF060 Matrice 210 Assistant export (zip)", "CFReDS / VTO Labs", [C / "DF060/2018_June/DJI_Assistant_export_ADB/DJI_Assistant_Export_ADBS.zip"]),
    ("DF068 Mavic 2 Zoom iOS backup (zip)", "CFReDS / VTO Labs", [C / "DF068/2018_September/mobile_iOS_backup/iOS_Backup.zip"]),
    ("DF069 Mavic 2 Pro Android logical (zip)", "CFReDS / VTO Labs", [C / "DF069/2018_September/mobile_android_logical/Android_Logical.zip"]),
    ("DF081 Mavic 2 Enterprise Android logical (zip)", "CFReDS / VTO Labs", [C / "DF081/2018_November/mobile_android/mobile_android_logical.zip"]),
    ("DF082 Mavic 2 Enterprise Android logical (zip)", "CFReDS / VTO Labs", [C / "DF082/2018_November/mobile_android/mobile_android_logical.zip"]),
    ("EPFL Phantom 4 Pro flights (DataverseNO AFDYLS, CC0)", "DataverseNO (ground truth: DatCon CSVs)", [ROOT / "corpus/_work/dataverse"]),
    ("Public GitHub / Zenodo / HF samples (DAT + TXT)", "public repositories, see SOURCE.md per folder",
     sorted(p for p in (ROOT / "corpus" / "raw").glob("gh-*") if p.is_dir())
     + sorted((ROOT / "corpus" / "raw").glob("zenodo-*")) + sorted((ROOT / "corpus" / "raw").glob("hf-*"))),
]


def run_job(name, origin, paths, out: Path) -> dict:
    paths = [p for p in paths if Path(p).exists()]
    res = {"name": name, "origin": origin, "inputs": [str(Path(p).relative_to(ROOT)) for p in paths]}
    if not paths:
        res["skipped"] = "input not present on this machine"
        return res
    d = out / "".join(ch if ch.isalnum() else "_" for ch in name)[:60]
    shutil.rmtree(d, ignore_errors=True)
    case = Case.create(d, name=name, examiner="validation run")
    t0 = time.perf_counter()
    summ = run_import(case, [str(p) for p in paths], options=ImportOptions(copy=False))
    case.close()
    svc = Service()
    svc.case_open(str(d))
    rows = svc.list_flights()
    cls = collections.Counter(r["classification"] for r in rows)
    real = [r for r in rows if r["is_real_flight"]]
    st = svc.counts()
    res.update({
        "seconds": round(time.perf_counter() - t0, 1), "state": summ.get("state"),
        "evidence": st["evidence"], "artifacts": st["artifacts"], "parsed": st["parsed"], "encrypted": st["encrypted"],
        "unsupported": st["unsupported"], "failed": st["failed"], "deleted_found": st["deleted"],
        "flight_records": len(rows), "classes": dict(cls), "real_flights": len(real),
        "real_with_track": sum(1 for r in real if r["bbox"]),
        "real_with_place": sum(1 for r in real if r["location_label"] or r["app_location"]),
        "records_with_battery_sn": sum(1 for r in rows if r["battery_serials"]),
        "records_with_fc_or_aircraft_id": sum(1 for r in rows if r["fc_ids"] or r["aircraft_sn"]),
        "places": sorted({r["location_label"] for r in real if r["location_label"]}),
        "models": sorted({r["aircraft_model"] for r in rows if r["aircraft_model"]}),
        "errors": [f"{e.get('stage')}: {e.get('source', '')} {e.get('error')}" for e in summ.get("errors") or []][:10],
        "failed_artifacts": [f"{a['name']}: {a['error']}" for a in svc.list_artifacts() if a["status"] == "failed"][:10],
        "custody_ok": svc.verify_custody()["ok"],
    })
    svc._close()
    return res


def write_md(results, path: Path):
    tot = collections.Counter()
    for r in results:
        for k in ("artifacts", "parsed", "encrypted", "failed", "flight_records", "real_flights", "real_with_track", "deleted_found"):
            tot[k] += r.get(k) or 0
    L = [f"# Validation run ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}, DroneDog {__version__})", "",
         "Every input imported through the production pipeline into its own fresh case (evidence referenced "
         "read-only). Generated by `tools/corpus_run.py`.", "",
         f"**Totals:** {len(results)} jobs, {tot['artifacts']} flight-log artifacts found, {tot['parsed']} parsed, "
         f"{tot['encrypted']} encrypted (header only), {tot['failed']} failed, {tot['deleted_found']} recovered deleted, "
         f"{tot['flight_records']} flight records, **{tot['real_flights']} real flights** ({tot['real_with_track']} with GPS track).", "",
         "| Input | Source | Artifacts | Parsed | Enc. | Failed | Records | Real flights (track) | Other records | Batt SN | Time | Custody |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r.get("skipped"):
            L.append(f"| {r['name']} | {r['origin']} | – | – | – | – | – | – | {r['skipped']} | – | – | – |")
            continue
        other = ", ".join(f"{k}: {v}" for k, v in sorted(r["classes"].items()) if k != "flight") or "–"
        L.append(f"| {r['name']} | {r['origin']} | {r['artifacts']} | {r['parsed']} | {r['encrypted']} | {r['failed']} | "
                 f"{r['flight_records']} | {r['real_flights']} ({r['real_with_track']}) | {other} | {r['records_with_battery_sn']} | "
                 f"{r['seconds']} s | {'ok' if r['custody_ok'] else 'BROKEN'} |")
    L += ["", "## Notes per input", ""]
    for r in results:
        if r.get("skipped"):
            continue
        L.append(f"- **{r['name']}**: models {', '.join(r['models'][:6]) or '–'}; places {', '.join(r['places'][:4]) or '–'}"
                 + (f"; errors: {'; '.join(r['errors'])}" if r["errors"] else "")
                 + (f"; failed: {'; '.join(r['failed_artifacts'])}" if r["failed_artifacts"] else ""))
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "dronedog_validation"))
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for name, origin, paths in JOBS:
        if a.only and not any(o.lower() in name.lower() for o in a.only):
            continue
        print(f"== {name}", flush=True)
        try:
            r = run_job(name, origin, paths, out)
        except Exception as e:  # noqa: BLE001
            r = {"name": name, "origin": origin, "skipped": f"job error {type(e).__name__}: {e}"}
        print(json.dumps({k: v for k, v in r.items() if k not in ("inputs",)}, default=str), flush=True)
        results.append(r)
        (ROOT / "docs" / "validation_run.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        write_md(results, ROOT / "docs" / "VALIDATION_RUN.md")


if __name__ == "__main__":
    main()
