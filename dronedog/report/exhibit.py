"""Printable HTML examination report (the "pdf" export): a self-contained handoff exhibit.

Layout, top to bottom:
  1. Case block + summary of findings (record counts, recorded flights, date range, places,
     every aircraft / flight-controller / battery identifier across the case).
  2. Overview map of all recorded flights.
  3. One section per recorded flight: narrative, map (track, takeoff, landing, home), takeoff /
     landing / home table with place names, statistics, classification basis,
     identifiers, key-moment timeline, key explained events.
  4. Other records (power-on / motors-only / events-only / encrypted / empty) with the reason.
  5. Evidence and source artifacts (hashes, inner paths, deleted flags), custody verification,
     method and limitations.

Maps are PNGs embedded as data: URIs so the file stands alone. Basemap tiles come from the
caller (offline map packs, or online tiles through the monitored choke point when the
examiner has online maps on); without tiles the map is drawn on a coordinate graticule.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

from dronedog.report.staticmap import png_data_uri, render_map

esc = html.escape

TOOL_STATUS = ("DroneDog is a training and research tool. It has not been independently validated for casework; "
               "every finding in this report must be verified with an independent, validated tool before it is relied on.")


def _v(x, fmt="{}", none="—"):
    return none if x is None or x == "" else fmt.format(x)


def _dur(s):
    if s is None:
        return "—"
    s = int(round(s))
    return f"{s // 3600}h {s % 3600 // 60:02d}m {s % 60:02d}s" if s >= 3600 else f"{s // 60}m {s % 60:02d}s"


def _m(x):
    return "—" if x is None else f"{x:,.0f} m ({x * 3.28084:,.0f} ft)"


def _spd(x):
    return "—" if x is None else f"{x:.1f} m/s ({x * 2.23694:.0f} mph)"


def _utc(s):
    return "—" if not s else esc(str(s).replace("T", " ").replace("Z", " UTC"))


def _coord(p):
    if not p or p.get("lat") is None:
        return "—"
    return f"{p['lat']:.6f}, {p['lon']:.6f}"


def _place(p):
    loc = (p or {}).get("location") or {}
    return esc(loc.get("summary") or "—")


def _map_png(tracks, markers, tile_sources, title=None, size=(1000, 560)):
    """Try each tile source in order; keep the first that actually supplied tiles."""
    last = None
    for src in tile_sources or []:
        try:
            r = render_map(tracks, markers, tile_fetch=src["fetch"], size=size, max_zoom=18,
                           attribution=src.get("attribution"), title=title)
        except ValueError:
            return None
        except Exception:  # noqa: BLE001 - a basemap problem must not lose the map
            continue
        if r.get("basemap") == "tiles" and r.get("tiles_used") and r.get("tiles_missing", 0) <= r["tiles_used"]:
            r["attribution_used"] = src.get("attribution") or src.get("name")
            return r
        last = r
    try:
        return render_map(tracks, markers, tile_fetch=None, size=size, max_zoom=18, title=title)
    except ValueError:
        return last


def _markers(a, include_home_updates=True):
    out = []
    to, la, home = a.get("takeoff") or {}, a.get("landing") or {}, a.get("home") or {}
    if to.get("lat") is not None:
        out.append({"lon": to["lon"], "lat": to["lat"], "kind": "takeoff", "label": "Takeoff"})
    if la.get("lat") is not None:
        out.append({"lon": la["lon"], "lat": la["lat"], "kind": "landing", "label": "Landing"})
    if home.get("lat") is not None:
        out.append({"lon": home["lon"], "lat": home["lat"], "kind": "home", "label": "Home point"})
    if include_home_updates:
        for h in a.get("home_points") or []:
            if h.get("rtk") or (home and abs(h["lat"] - home.get("lat", 0)) < 2e-5 and abs(h["lon"] - home.get("lon", 0)) < 2e-5):
                continue
            out.append({"lon": h["lon"], "lat": h["lat"], "kind": "home_update", "label": "Home update"})
    return out


COLORS = ["#E0A43A", "#3A8EE0", "#D9534F", "#4CAF6A", "#9B6BD6", "#17A2B8", "#E67E22", "#2C3E91"]

CSS = """
*{box-sizing:border-box} body{font:12px/1.45 "Segoe UI",Arial,sans-serif;margin:28px auto;max-width:1060px;color:#0B0D12;padding:0 18px}
h1{color:#14234F;margin:0 0 4px;font-size:24px} h2{color:#14234F;border-bottom:2px solid #E0A43A;padding-bottom:3px;margin:28px 0 10px;font-size:18px}
h3{color:#14234F;margin:16px 0 6px;font-size:14px} .sub{color:#555}
table{border-collapse:collapse;width:100%;margin:6px 0 14px} td,th{border:1px solid #cfd3da;padding:4px 7px;text-align:left;vertical-align:top}
th{background:#F6E7C1;font-weight:600} td.k{background:#fafafa;width:200px;color:#333;font-weight:600}
.mono,.h{font-family:Consolas,"Courier New",monospace} .h{font-size:10px;word-break:break-all}
.flight{page-break-before:always;break-before:page} .map{width:100%;border:1px solid #cfd3da;margin:6px 0 4px}
.cap{font-size:10.5px;color:#555;margin-bottom:12px} .narr{font-size:13px;background:#F8F4E8;border-left:4px solid #E0A43A;padding:8px 12px;margin:8px 0 12px}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600}
.ok{background:#DCF3E3;color:#1E6B39} .no{background:#ECEEF2;color:#444} .warn{background:#FFF0D6;color:#8A5300}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px} ul{margin:4px 0 10px 18px;padding:0}
.small{font-size:10.5px;color:#555} .toc a{color:#14234F}
@media print{body{margin:0;max-width:none} h2{break-after:avoid} table,.map{break-inside:avoid}}
"""


def build_html(svc, flights: list[dict], tile_sources=None) -> str:
    from dronedog import PRODUCT_NAME, __version__, core_bridge
    c = svc._need()
    m = c.meta
    cu = c.verify_custody()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    analyses = {f["id"]: svc.analysis(f["id"]) for f in flights}
    real = [f for f in flights if analyses[f["id"]].get("is_real_flight")]
    other = [f for f in flights if not analyses[f["id"]].get("is_real_flight")]
    real.sort(key=lambda f: ((analyses[f["id"]].get("takeoff") or {}).get("t_utc") or f.get("start_utc") or "", f["id"]))

    # case-wide identifiers: (group, label, value) -> flight ids
    ids: dict = {}
    for f in flights:
        for i in analyses[f["id"]].get("identifiers") or []:
            if i["group"] in ("File", "Sensors", "Components") or i["label"] == "Product type code":
                continue
            ids.setdefault((i["group"], i["label"], i["value"]), set()).add(f["id"])
    places: dict = {}
    for f in real:
        loc = analyses[f["id"]].get("location") or {}
        if loc.get("label"):
            places.setdefault(loc["label"], []).append(f["id"])
    times = sorted(t for t in ((analyses[f["id"]].get("takeoff") or {}).get("t_utc") for f in real) if t)

    P = []
    P.append(f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(str(m.get('name') or 'Case'))} - DroneDog examination report</title>"
             f"<style>{CSS}</style></head><body>")
    P.append("<h1>Drone flight-log examination report</h1>")
    P.append(f"<div class='sub'>Generated {now} by {PRODUCT_NAME} {__version__} (core {esc(str(core_bridge.core_version()))})</div>")
    P.append(f"<p class=small><b>Tool status:</b> {esc(TOOL_STATUS)}</p>")
    P.append("<table style='margin-top:12px'>"
             f"<tr><td class=k>Case name</td><td>{esc(str(m.get('name') or ''))}</td><td class=k>Case number</td><td>{esc(str(m.get('case_number') or ''))}</td></tr>"
             f"<tr><td class=k>Examiner</td><td>{esc(str(m.get('examiner') or ''))}</td><td class=k>Agency</td><td>{esc(str(m.get('agency') or ''))}</td></tr>"
             f"<tr><td class=k>Case ID</td><td class=mono>{esc(str(m.get('id') or ''))}</td><td class=k>Custody log</td>"
             f"<td>{'Hash chain verified' if cu['ok'] else '<b>HASH CHAIN BROKEN</b>'} ({cu['entries']} entries)</td></tr></table>")

    # ---- findings
    P.append("<h2>Summary of findings</h2>")
    P.append("<table>"
             f"<tr><td class=k>Flight records examined</td><td>{len(flights)}</td></tr>"
             f"<tr><td class=k>Records with a recorded flight</td><td><b>{len(real)}</b></td></tr>"
             f"<tr><td class=k>Records without a recorded flight</td><td>{len(other)} (DJI aircraft write a log at every power-on; each is listed below with its classification basis)</td></tr>"
             f"<tr><td class=k>First / last takeoff</td><td>{_utc(times[0]) if times else '—'} &nbsp;/&nbsp; {_utc(times[-1]) if times else '—'}</td></tr>"
             f"<tr><td class=k>Flight locations</td><td>{'<br>'.join(f'{esc(k)} ({len(v)} flight{chr(115) if len(v) > 1 else str()})' for k, v in places.items()) or '—'}</td></tr>"
             "</table>")
    if ids:
        P.append("<h3>Identifiers found (all records)</h3><table><tr><th>Group</th><th>Identifier</th><th>Value</th><th>Records</th></tr>")
        order = ["Aircraft", "Flight controller", "Battery", "Camera", "Remote controller", "Motors / ESC", "Navigation", "Components", "App"]
        for (g, lab, val), fids in sorted(ids.items(), key=lambda kv: (order.index(kv[0][0]) if kv[0][0] in order else 99, kv[0][1], kv[0][2])):
            P.append(f"<tr><td>{esc(g)}</td><td>{esc(lab)}</td><td class=mono>{esc(val)}</td><td>{', '.join('#' + str(x) for x in sorted(fids)[:12])}{' …' if len(fids) > 12 else ''}</td></tr>")
        P.append("</table>")

    # ---- overview map + flight table
    if real:
        # one overview per area: flights whose takeoff points are within 25 km share a map
        clusters: list[list[dict]] = []
        for f in real:
            to = analyses[f["id"]].get("takeoff") or analyses[f["id"]].get("home") or {}
            if to.get("lat") is None:
                continue
            for cl in clusters:
                c0 = analyses[cl[0]["id"]].get("takeoff") or analyses[cl[0]["id"]].get("home")
                if abs(c0["lat"] - to["lat"]) < 0.25 and abs(c0["lon"] - to["lon"]) < 0.35:
                    cl.append(f)
                    break
            else:
                clusters.append([f])
        for cl in clusters:
            tracks = []
            for f in cl:
                pts = svc.track(f["id"], 1500)
                if len(pts) >= 2:
                    tracks.append({"coords": [[q[0], q[1]] for q in pts], "color": COLORS[real.index(f) % len(COLORS)], "label": f"#{f['id']}"})
            mk = [dict(mm, label="") for f in cl for mm in _markers(analyses[f["id"]], False) if mm["kind"] == "takeoff"]
            area = (analyses[cl[0]["id"]].get("location") or {}).get("label") or "flight area"
            r = _map_png(tracks, mk, tile_sources, title=f"{area}: {len(cl)} flight{'s' if len(cl) > 1 else ''}", size=(1000, 560))
            if r:
                P.append(f"<h3>Overview: {esc(area)}</h3><img class=map src='{_img_uri(r)}' alt='Overview map'>"
                         f"<div class=cap>Flights {', '.join('#' + str(f['id']) for f in cl)}. "
                         f"{'Basemap: ' + esc(r.get('attribution_used') or '') if r.get('basemap') == 'tiles' else 'No basemap tiles available offline; WGS 84 graticule.'} "
                         "Coloured lines are GPS tracks; T = takeoff.</div>")
        P.append("<h3>Recorded flights</h3><table class=toc><tr><th>#</th><th>Takeoff (UTC)</th><th>Air time</th><th>Distance</th><th>Max height</th>"
                 "<th>Place</th><th>Aircraft / FC</th><th>Battery</th><th>Source file</th></tr>")
        for f in real:
            a = analyses[f["id"]]
            st = a.get("stats") or {}
            bat = ", ".join(dict.fromkeys(i["value"] for i in a.get("identifiers") or [] if i["label"] in ("Battery serial number", "Battery barcode / serial"))) or "—"
            fc = f.get("aircraft_sn") or next((i["value"] for i in a.get("identifiers") or [] if i["label"] in ("Flight controller ID (Mc ID)", "Flight controller serial")), "—")
            P.append(f"<tr><td><a href='#f{f['id']}'>#{f['id']}</a></td><td>{_utc((a.get('takeoff') or {}).get('t_utc'))}</td><td>{_dur(st.get('air_time_s'))}</td>"
                     f"<td>{_m(st.get('path_distance_m'))}</td><td>{_m(st.get('max_height_m'))}</td><td>{esc((a.get('location') or {}).get('label') or a.get('app_location') or '—')}</td>"
                     f"<td>{esc(str(f.get('aircraft_model') or ''))}<br><span class=mono>{esc(str(fc))}</span></td><td class=mono>{esc(bat)}</td>"
                     f"<td class=mono>{esc(_clean(str(f.get('artifact_name') or '')))}</td></tr>")
        P.append("</table>")

    # ---- per flight
    arts = {a["id"]: a for a in svc.list_artifacts()}
    for k, f in enumerate(real):
        a = analyses[f["id"]]
        st = a.get("stats") or {}
        P.append(f"<div class=flight id='f{f['id']}'><h2>Flight #{f['id']} &nbsp;<span class='badge ok'>Flight</span></h2>")
        P.append(f"<div class=narr>{esc(a.get('narrative') or '')}</div>")
        pts = svc.track(f["id"], 3000)
        tracks = [{"coords": [[q[0], q[1]] for q in pts], "color": COLORS[k % len(COLORS)], "label": f"Flight #{f['id']}"}] if len(pts) >= 2 else []
        mk = _markers(a)
        r = _map_png(tracks, mk, tile_sources, title=f"Flight #{f['id']}") if (tracks or mk) else None
        if r:
            P.append(f"<img class=map src='{_img_uri(r)}' alt='Map of flight {f['id']}'>"
                     f"<div class=cap>GPS track from the aircraft log ({st.get('gps_points') or 0:,} fixes). T = takeoff, L = landing, H = home point in effect at takeoff, "
                     f"rings = later home-point updates. {'Basemap: ' + esc(r.get('attribution_used') or '') if r.get('basemap') == 'tiles' else 'No basemap tiles available offline; WGS 84 graticule.'}</div>")
        else:
            P.append("<p class=small>No GPS position fixes in this record, so no map can be drawn.</p>")
        to, la, home = a.get("takeoff") or {}, a.get("landing") or {}, a.get("home") or {}
        P.append("<table><tr><th></th><th>Time (UTC)</th><th>Coordinates (WGS 84)</th><th>Place (nearest named place)</th><th>Basis</th></tr>"
                 f"<tr><td class=k>Takeoff</td><td>{_utc(to.get('t_utc'))}</td><td class=mono>{_coord(to)}</td><td>{_place(to)}</td><td>first in-air sample</td></tr>"
                 f"<tr><td class=k>Landing</td><td>{_utc(la.get('t_utc'))}</td><td class=mono>{_coord(la)}</td><td>{_place(la)}</td><td>last in-air sample</td></tr>"
                 f"<tr><td class=k>Home point</td><td>{'+%.0f s into log' % (home['t_offset_ms'] / 1000) if home.get('t_offset_ms') is not None else '—'}</td>"
                 f"<td class=mono>{_coord(home)}</td><td>{_place(home)}</td><td>{esc(home.get('source') or '—')}"
                 f"{'; %.0f m from takeoff' % home['distance_from_takeoff_m'] if home.get('distance_from_takeoff_m') is not None else ''}</td></tr>")
        for rr in a.get("rth") or []:
            P.append(f"<tr><td class=k>Return home / auto land</td><td>{_utc(rr.get('t_start_utc'))}</td><td class=mono>{_coord(rr)}</td><td></td><td>flight mode {esc(str(rr['mode']))}</td></tr>")
        P.append("</table>")
        if home.get("note"):
            P.append(f"<p class=narr style='border-left-color:#D9534F'><b>Home point note:</b> {esc(home['note'])}</p>")
        if a.get("app_location"):
            P.append(f"<p class=small>App-recorded location text: {esc(a['app_location'])}</p>")
        P.append("<div class=grid2><div><h3>Statistics</h3><table>"
                 f"<tr><td class=k>Air time</td><td>{_dur(st.get('air_time_s'))}</td></tr>"
                 f"<tr><td class=k>Log length (power-on to end)</td><td>{_dur(st.get('record_duration_s'))}</td></tr>"
                 f"<tr><td class=k>Takeoffs in this log</td><td>{_v(st.get('takeoffs'))}</td></tr>"
                 f"<tr><td class=k>Distance flown</td><td>{_m(st.get('path_distance_m'))}</td></tr>"
                 f"<tr><td class=k>Max height above takeoff</td><td>{_m(st.get('max_height_m'))}</td></tr>"
                 f"<tr><td class=k>Max ground speed</td><td>{_spd(st.get('max_speed_ms'))}</td></tr>"
                 f"<tr><td class=k>Max vertical speed</td><td>{_spd(st.get('max_vspeed_ms'))}</td></tr>"
                 f"<tr><td class=k>Furthest from home</td><td>{_m(st.get('max_distance_from_home_m'))}</td></tr>"
                 f"<tr><td class=k>Battery</td><td>{_v(st.get('battery_start_pct'), '{:.0f}%')} → {_v(st.get('battery_end_pct'), '{:.0f}%')}</td></tr>"
                 f"<tr><td class=k>Flight modes</td><td>{esc(', '.join(st.get('flight_modes') or [])) or '—'}</td></tr>"
                 "</table></div><div><h3>Classification basis</h3><ul>"
                 + "".join(f"<li>{esc(x)}</li>" for x in a.get("evidence") or [])
                 + f"</ul><div class=small>Rule: {esc((a.get('method') or {}).get('rules') or '')}</div></div></div>")
        idl = [i for i in a.get("identifiers") or [] if i["group"] != "Sensors"]
        if idl:
            P.append("<h3>Identifiers in this record</h3><table><tr><th>Group</th><th>Identifier</th><th>Value</th><th>Where found</th></tr>"
                     + "".join(f"<tr><td>{esc(i['group'])}</td><td>{esc(i['label'])}</td><td class=mono>{esc(i['value'])}</td><td class=small>{esc(_clean(i['source']))}</td></tr>" for i in idl)
                     + "</table>")
        tl = a.get("timeline") or []
        if tl:
            P.append("<h3>Timeline</h3><table><tr><th>Log time</th><th>UTC</th><th>Event</th><th>Position</th></tr>"
                     + "".join(f"<tr><td class=mono>{'+%.1f s' % (e['t_offset_ms'] / 1000) if e.get('t_offset_ms') is not None else '—'}</td><td>{_utc(e.get('t_utc'))}</td>"
                               f"<td>{esc(e['event'])}</td><td class=mono>{_coord(e) if e.get('lat') is not None else ''}</td></tr>" for e in tl)
                     + "</table>")
        key = _key_events(svc, f["id"])
        if key:
            P.append("<h3>Key flight-controller events (plain English, original text kept)</h3><table><tr><th>Log time</th><th>Meaning</th><th>Original log text</th></tr>"
                     + "".join(f"<tr><td class=mono>{'+%.1f s' % (e['t_offset_ms'] / 1000) if e.get('t_offset_ms') is not None else '—'}</td>"
                               f"<td><b>{esc(e['explain']['title'])}</b><br><span class=small>{esc(e['explain'].get('detail') or '')}</span></td>"
                               f"<td class=h>{esc(_clean(str(e.get('message') or ''))[:220])}</td></tr>" for e in key)
                     + "</table>")
        art = arts.get(f["artifact_id"]) or {}
        src = (art.get("sources") or [{}])[0]
        P.append("<h3>Source</h3><table>"
                 f"<tr><td class=k>File</td><td class=mono>{esc(_clean(str(f.get('artifact_name') or '')))}</td></tr>"
                 f"<tr><td class=k>Evidence item / path inside</td><td class=mono>{esc(str(art.get('evidence_label') or ''))} : {esc(str(src.get('inner_path') or art.get('rel_path') or ''))}"
                 f"{' (recovered deleted file)' if art.get('deleted') else ''}</td></tr>"
                 f"<tr><td class=k>File SHA-256</td><td class=h>{esc(str(art.get('sha256') or ''))}</td></tr>"
                 f"<tr><td class=k>Parser / result SHA-256</td><td class=h>{esc(str(f.get('adapter') or ''))} {esc(str(f.get('adapter_version') or ''))} / {esc(str(f.get('result_sha256') or ''))}</td></tr>"
                 "</table></div>")

    # ---- other records
    if other:
        P.append("<h2 class=flight>Records without a recorded flight</h2>"
                 "<p>DJI aircraft create a new log every time they are powered on, including power-ons to download logs. "
                 "Each record is listed with what it records and the basis for its classification.</p>"
                 "<table><tr><th>#</th><th>File</th><th>Status</th><th>Log length</th><th>Classification basis</th><th>Identifiers</th></tr>")
        for f in sorted(other, key=lambda x: (str(x.get("artifact_name")), x["id"])):
            a = analyses[f["id"]]
            idv = "; ".join(f"{i['label']}: {i['value']}" for i in (a.get("identifiers") or [])
                            if i["group"] in ("Aircraft", "Account", "Flight controller", "Battery") and i["label"] != "Aircraft model")[:300]
            P.append(f"<tr><td>{f['id']}</td><td class=mono>{esc(_clean(str(f.get('artifact_name') or '')))}</td>"
                     f"<td><span class='badge {'warn' if a.get('classification') in ('motors_only', 'gps_movement') else 'no'}'>{esc(a.get('label') or '')}</span></td>"
                     f"<td>{_dur((a.get('stats') or {}).get('record_duration_s'))}</td><td class=small>{esc(' '.join(a.get('evidence') or []))}</td>"
                     f"<td class='small mono'>{esc(idv) or '—'}</td></tr>")
        P.append("</table>")

    # ---- evidence, custody, method
    P.append("<h2 class=flight>Evidence</h2><table><tr><th>#</th><th>Item</th><th>Kind</th><th>Bytes</th><th>SHA-256</th><th>Verified</th></tr>")
    for e in svc.list_evidence():
        P.append(f"<tr><td>{e['id']}</td><td class=mono>{esc(str(e['label']))}</td><td>{esc(str(e['kind']))}/{esc(str(e.get('subkind') or '-'))}</td>"
                 f"<td>{(e['size'] or 0):,}</td><td class=h>{esc(str(e['sha256'] or '')) or ('<b>NOT HASHED (skipped at import)</b>' if e.get('hash_scope') == 'not-hashed' else '')}</td>"
                 f"<td>{'yes' if e['verified'] else ('NO' if e['verified'] is False else '-')}</td></tr>")
    P.append("</table>")
    P.append(f"<h3>Custody log</h3><p>{'Hash chain verified' if cu['ok'] else 'HASH CHAIN BROKEN'}: {cu['entries']} entries, head "
             f"<span class=h>{esc(str(cu.get('head_hash') or ''))}</span>{'' if cu['ok'] else ' - ' + esc(str(cu.get('reason')))}</p>")
    lim = "".join(f"<li>Flight #{f['id']} ({esc(_clean(str(f['artifact_name'])))}): {esc(_clean(str(w)))}</li>"
                  for f in flights for w in (f.get("warnings") or []) if "fast path" not in str(w)) or "<li>No parse warnings.</li>"
    P.append("<h3>Method</h3><ul>"
             "<li>Evidence was hashed (SHA-256/SHA-1/MD5) on intake and never modified; disk images are read in place and every extracted log is hashed with its source path and offset.</li>"
             "<li>DJI aircraft DAT files are decoded by DroneDog's own decoder (record CRC-16 checked; validated against DatCon 4 output on public datasets). App TXT logs are decoded with the MIT dji-log-parser library, offline.</li>"
             "<li>A record is classified as a flight when the flight controller reported the in-air state for at least 2 s; when that state is never reported, when motors ran with more than 1 m of height gain for at least 2 s; when no motor-running state is recorded either, when height gain exceeded 2 m for at least 5 s. Height before the first GPS fix is not used. A record with no in-air, motor-running or height values whose GPS position moved at least 50 m is listed as GPS movement recorded, not as a flight. The basis for each classification is listed with the record.</li>"
             "<li>Place names are the nearest named populated place in the offline GeoNames gazetteer (CC BY 4.0), given as distance and direction; they are not postal addresses or boundary determinations.</li>"
             "<li>Event explanations are DroneDog's plain-English reading of the original flight-controller text, which is always shown beside it.</li>"
             "</ul>")
    P.append(f"<h3>Limitations</h3><ul>{lim}<li>Encrypted logs are reported with their plaintext header only; DroneDog does not decrypt protected content.</li></ul>")
    P.append(f"<p class=small><b>Tool status:</b> {esc(TOOL_STATUS)} DroneDog is provided under the PolyForm Noncommercial License 1.0.0 (noncommercial use only), without warranty of any kind.</p>")
    P.append("<p class=small>Contains GeoNames data (CC BY 4.0, geonames.org).</p></body></html>")
    return "\n".join(P)


def _key_events(svc, fid, cap=60):
    rows = svc._need().store.query("SELECT t_offset_ms, kind, severity, message FROM events WHERE flight_id=? ORDER BY t_offset_ms, id", (fid,))
    try:
        from dronedog.analysis.explain import explain_many
        rows = explain_many(rows)
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], set()
    for e in rows:
        ex = e.get("explain")
        if not ex or ex.get("importance") != "high":
            continue
        key = (ex.get("title"), (e.get("message") or "")[:60])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
        if len(out) >= cap:
            break
    return out


def _img_uri(r: dict) -> str:
    """Satellite maps as JPEG (a tenth of the PNG size); graticule maps stay PNG (sharp lines)."""
    if r.get("basemap") != "tiles":
        return png_data_uri(r["png"])
    import base64
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.open(io.BytesIO(r["png"])).convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _clean(s: str) -> str:
    import re
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]", "�", s)
