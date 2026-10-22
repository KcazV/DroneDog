// Flight detail: map (track colored by altitude/speed, home/takeoff/landing/event markers,
// aircraft with heading, linked-flight overlay), playback scrubber, synchronized uPlot
// charts, events, details.  Tolerates real-world logs: null positions before GPS fix and
// in gaps, null timestamps, absent columns, frameless (encrypted / unparsed) flights.
import uPlot from '../../vendor/uplot/1.6.32/uPlot.esm.js';
import { call } from '../bridge.js';
import { store, load } from '../store.js';
import { h, clear, icon, getPrefs, setPref, units, num, fmtUtc, fmtUtcTime, fmtLocal, fmtDur, fmtDist,
  fmtAlt, fmtSpd, fmtVSpd, fmtOffset, parseUtc, hashEl, sourceBadge, badge, rampColor, RAMP, cssVar, esc,
  SOURCE, FLIGHT_COLORS, linkKind, visibleLinks, noteBadges, copyText } from '../util.js';
import { createMap, divMarker, aircraftIcon } from '../map.js';

const L = window.L;
const SPEEDS = [0.5, 1, 2, 5, 10, 30, 60];
const C = { a: '#56B4E9', b: '#CC79A7', c: '#3DD6A3', d: '#F07167', e: '#B79CFF' };
const SEV = { info: 'var(--info)', warn: 'var(--warn)', critical: 'var(--bad)' };
const SEV_RANK = { info: 0, warn: 1, critical: 2 };

const DETAIL_LABELS = [
  ['aircraft_model', 'Aircraft model'], ['aircraft_name', 'Aircraft name'], ['aircraft_sn', 'Aircraft SN', 'mono'],
  ['fc_sn', 'Flight controller SN', 'mono'], ['camera_sn', 'Camera SN', 'mono'], ['rc_sn', 'Remote controller SN', 'mono'],
  ['battery_sn', 'Battery SN', 'mono'], ['firmware_version', 'Firmware', 'mono'], ['app_platform', 'App platform'],
  ['app_version', 'App version', 'mono'], ['log_version', 'Log version', 'mono'], ['start_time_utc', 'Start (UTC)', 'utc'],
  ['duration_s', 'Duration', 'dur'], ['total_distance_m', 'Total distance', 'dist'], ['max_height_m', 'Max height', 'alt'],
  ['max_h_speed_ms', 'Max horizontal speed', 'spd'], ['max_v_speed_ms', 'Max vertical speed', 'vspd'],
  ['home_lat', 'Home latitude', 'coord'], ['home_lon', 'Home longitude', 'coord'], ['city', 'City'], ['area', 'Area'],
  ['sub_street', 'Street'], ['record_line_count', 'Record count', 'int'],
];
const LABELLED = new Set(DETAIL_LABELS.map((d) => d[0]));

function maxPoints() {
  const r = getPrefs().chartRes;
  if (r === 'full') return 0;
  if (r === 'high') return 20000;
  return Math.max(3000, Math.min(8000, Math.round(window.innerWidth * 2.5)));
}
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

// Monotonic x axis (seconds) even when t_offset_ms has nulls: interpolate gaps, extrapolate
// the ends with the median step, then force non-decreasing order.
function buildTime(n, tms, tutc) {
  const T = new Float64Array(n);
  const known = [];
  for (let i = 0; i < n; i++) {
    let v = tms && isNum(tms[i]) ? tms[i] / 1000 : null;
    T[i] = v ?? NaN;
    if (v != null) known.push(i);
  }
  if (!known.length && tutc) {           // fall back to absolute timestamps
    let t0 = null;
    for (let i = 0; i < n; i++) {
      const d = parseUtc(tutc[i]);
      if (!d) continue;
      if (t0 == null) t0 = d.getTime();
      T[i] = (d.getTime() - t0) / 1000; known.push(i);
    }
  }
  if (!known.length) { for (let i = 0; i < n; i++) T[i] = i * 0.1; return T; }
  const steps = [];
  for (let k = 1; k < known.length && steps.length < 2000; k++) {
    const d = (T[known[k]] - T[known[k - 1]]) / (known[k] - known[k - 1]);
    if (d > 0) steps.push(d);
  }
  steps.sort((a, b) => a - b);
  const dt = steps.length ? steps[steps.length >> 1] : 0.1;
  const first = known[0], last = known[known.length - 1];
  for (let i = 0; i < first; i++) T[i] = T[first] - (first - i) * dt;
  for (let i = last + 1; i < n; i++) T[i] = T[last] + (i - last) * dt;
  for (let k = 1; k < known.length; k++) {
    const a = known[k - 1], b = known[k];
    for (let i = a + 1; i < b; i++) T[i] = T[a] + (T[b] - T[a]) * (i - a) / (b - a);
  }
  for (let i = 1; i < n; i++) if (!(T[i] >= T[i - 1])) T[i] = T[i - 1];
  return T;
}

export async function mount(el, param, { go }) {
  const fid = parseInt(param, 10);
  const root = h('div', { class: 'fd' });
  el.appendChild(root);
  el.style.overflow = 'hidden';
  const loadingEl = h('div', { class: 'page' }, h('span', { class: 'muted' }, icon('loader-circle', 'spin'), ' Loading flight…'));
  root.appendChild(loadingEl);

  let data;
  try { data = await call('get_flight', fid, maxPoints()); }
  catch (e) { loadingEl.replaceChildren(h('div', { class: 'banner bad' }, icon('circle-x'), h('div', null, e.message))); el.style.overflow = ''; return { destroy() {} }; }
  loadingEl.remove();

  const flight = data.flight || {};
  const an = data.analysis || {};
  const details = data.details || {};
  const fr = data.frames || {};
  const allLinks = data.links || flight.links || [];
  const links = visibleLinks(allLinks);                         // 'rejected' only in the Source tab
  const overlayLinks = links.filter((l) => linkKind(l.kind).overlay);   // same-flight / app-tips tracks
  if (!store.artifacts) { try { await load('artifacts'); } catch (e) { /* notes without the deleted flag */ } }
  const artifact = (store.artifacts || []).find((a) => a.id === flight.artifact_id);
  const n = Math.max(0, ...Object.values(fr).map((v) => (Array.isArray(v) ? v.length : 0)));
  const T = buildTime(n, fr.t_offset_ms, fr.t_utc);
  const tMin = n ? T[0] : 0, tMax = n ? T[n - 1] : 0;
  const LAT = fr.lat || [], LON = fr.lon || [];
  const fixIdx = [];
  for (let i = 0; i < n; i++) if (isNum(LAT[i]) && isNum(LON[i]) && !(LAT[i] === 0 && LON[i] === 0)) fixIdx.push(i);
  const hasFix = (i) => isNum(LAT[i]) && isNum(LON[i]) && !(LAT[i] === 0 && LON[i] === 0);
  // Height is referenced to the takeoff point; before the first GPS fix it is not (unless the
  // aircraft already reports being in the air), so it is not plotted or shown (summary.py does the same).
  const firstAir = (fr.is_flying || []).findIndex((v) => v === true);
  const altFrom = fixIdx.length ? Math.min(fixIdx[0], firstAir >= 0 ? firstAir : Infinity) : 0;
  if (altFrom > 0) for (const k of ['alt_rel_m', 'height_m', 'alt_msl_m']) if (Array.isArray(fr[k])) { fr[k] = fr[k].slice(); fr[k].fill(null, 0, altFrom); }
  const hasTrack = fixIdx.length >= 2;
  const hasFrames = n > 1;
  // absolute time of x: anchor on the first frame with both t_utc and x
  let t0utc = null;
  if (fr.t_utc) for (let i = 0; i < n; i++) { const d = parseUtc(fr.t_utc[i]); if (d) { t0utc = new Date(d.getTime() - T[i] * 1000); break; } }
  if (!t0utc) { const d = parseUtc(flight.start_utc || details.start_time_utc); if (d) t0utc = new Date(d.getTime() - tMin * 1000); }
  const absAt = (ms) => (t0utc ? new Date(t0utc.getTime() + ms) : null);
  const events = (data.events || []).filter((e) => e).slice().sort((a, b) => (a.t_offset_ms ?? Infinity) - (b.t_offset_ms ?? Infinity));
  const evTimes = events.map((e) => (isNum(e.t_offset_ms) ? e.t_offset_ms : Infinity));
  const st = { t: tMin * 1000, playing: false, speed: getPrefs().playSpeed || 10, follow: false, colorBy: getPrefs().trackColor || 'alt', idx: 0 };
  const cleanup = [];

  // ---------------------------------------------------------------- header
  const linkChips = links.map((l) => h('button', { class: 'chip chip-' + (linkKind(l.kind).cls || ''), title: (l.reasons || []).join('\n'), onclick: () => go(`flight/${l.flight_id}`) },
    icon('link'), linkKind(l.kind).label(l.flight_id),
    l.source ? h('span', { class: 'n' }, (SOURCE[l.source] || [l.source])[0]) : null,
    l.score != null ? h('span', { class: 'n' }, `score ${num(l.score, 2)}`) : null));
  const warnings = data.warnings || flight.warnings || [];
  root.appendChild(h('div', { class: 'fd-head' },
    h('button', { class: 'btn sm ghost', onclick: () => go('flights'), title: 'Back to flights' }, icon('chevron-left'), 'Flights'),
    h('div', { class: 'fd-title' }, `#${fid} ${flight.aircraft_model || details.aircraft_model || 'Unknown aircraft'}`,
      h('span', { class: 'sn' }, flight.aircraft_sn || details.aircraft_sn || details.fc_sn || '')),
    classBadge(an), sourceBadge(flight.source), noteBadges(flight, artifact),
    h('div', { class: 'fd-stats' },
      h('span', null, h('b', null, fmtUtc(flight.start_utc)), flight.start_utc ? h('span', { class: 'dim' }, ` · ${fmtLocal(flight.start_utc)}`) : h('span', { class: 'dim' }, ' (no UTC in log)')),
      (an.location && an.location.label) ? h('span', { title: an.location.note || '' }, icon('map-pin'), ' ', h('b', null, an.location.label)) : null,
      h('span', null, 'Duration ', h('b', null, fmtDur(flight.duration_s ?? (hasFrames ? tMax - tMin : null)))),
      flight.distance_m != null ? h('span', null, 'Distance ', h('b', null, fmtDist(flight.distance_m))) : null,
      flight.max_alt_m != null ? h('span', null, 'Max alt ', h('b', null, fmtAlt(flight.max_alt_m))) : null,
      flight.max_speed_ms != null ? h('span', null, 'Max speed ', h('b', null, fmtSpd(flight.max_speed_ms))) : null),
    h('span', { style: { flex: 1 } }), linkChips,
    warnings.length ? h('span', { title: warnings.join('\n') }, badge(`${warnings.length} warning${warnings.length > 1 ? 's' : ''}`, warnings.some((w) => /forced|critical/i.test(w)) ? 'b-bad' : 'b-warn', 'triangle-alert')) : null));

  // ---------------------------------------------------------------- layout
  const main = h('div', { class: 'fd-main' });
  const side = h('div', { class: 'fd-side' });
  root.append(main, side);

  // home: header, then frame column, then first fix
  // home: the one in effect at takeoff (analysis), else header, then frame column, then first fix
  let homeLat = an.home?.lat ?? details.home_lat ?? flight.home_lat ?? (fr.home_lat || []).find(isNum) ?? null;
  let homeLon = an.home?.lon ?? details.home_lon ?? flight.home_lon ?? (fr.home_lon || []).find(isNum) ?? null;
  let homeDerived = false;
  if ((!isNum(homeLat) || !isNum(homeLon)) && hasTrack) { homeLat = LAT[fixIdx[0]]; homeLon = LON[fixIdx[0]]; homeDerived = true; }
  if (!isNum(homeLat) || !isNum(homeLon) || (homeLat === 0 && homeLon === 0)) { homeLat = homeLon = null; }

  const showMap = hasTrack || homeLat != null || overlayLinks.length > 0;
  const mapWrap = h('div', { class: 'fd-map' });
  let map = null, trackGroup = null, markerGroup = null, linkGroup = null, trail = null, aircraft = null, legendEl = null;
  if (showMap) {
    const mapEl = h('div', { style: { position: 'absolute', inset: 0 } });
    mapWrap.appendChild(mapEl);
    main.appendChild(mapWrap);
    map = createMap(mapEl);
    cleanup.push(() => map.ddDestroy());
    linkGroup = L.featureGroup().addTo(map);
    trackGroup = L.featureGroup().addTo(map);
    markerGroup = L.layerGroup().addTo(map);
  }
  const why = flight.encrypted_unreadable
    ? 'Records are encrypted (no keychain available). DroneDog reports the plaintext header only; no track or telemetry is shown.'
    : !hasFrames ? 'No telemetry frames were decoded from this log. Header details and any events are still listed.'
    : 'This log has no GPS position fixes, so there is no track to draw. Telemetry charts are below.';
  if (!hasTrack) {
    const note = h('div', { class: 'banner' + (flight.encrypted_unreadable ? '' : ' warn'), style: showMap ? { border: 0, background: 'transparent', padding: 0 } : { margin: '16px' } },
      icon(flight.encrypted_unreadable ? 'lock' : 'info'), h('div', null, why));
    if (showMap) mapWrap.appendChild(h('div', { class: 'legend tl', style: { maxWidth: '460px', left: '56px' } }, note));
    else main.appendChild(note);
    if (homeLat != null && map) {
      divMarker([homeLat, homeLon], 'mk-home', 'house', 28, 'Home point').bindTooltip(homeDerived ? 'Home point (first GPS fix)' : 'Home point (log header)', { permanent: true, direction: 'top', offset: [0, -14] }).addTo(markerGroup);
      map.setView([homeLat, homeLon], 15);
    }
  } else {
    drawTrack(true);
    addMarkers();
    trail = L.polyline([], { color: cssVar('--gold') || '#E0A43A', weight: 2.5, opacity: 0.95, interactive: false }).addTo(map);
    aircraft = L.marker([LAT[fixIdx[0]], LON[fixIdx[0]]], { icon: aircraftIcon(0, '#F6E7C1'), interactive: false, zIndexOffset: 1000, keyboard: false }).addTo(map);
  }
  if (map && overlayLinks.length) drawLinks();

  function segmentsOf(predicate) {         // runs of consecutive fixes (split at null gaps)
    const runs = []; let cur = [];
    for (let i = 0; i < n; i++) {
      if (hasFix(i)) { cur.push(i); } else if (cur.length) { runs.push(cur); cur = []; }
    }
    if (cur.length) runs.push(cur);
    return predicate ? runs.filter(predicate) : runs;
  }
  function drawTrack(fit) {
    trackGroup.clearLayers();
    const col = st.colorBy === 'speed' ? fr.speed_h_ms : (fr.alt_rel_m || fr.height_m || fr.alt_msl_m);
    let lo = Infinity, hi = -Infinity;
    if (col) for (const i of fixIdx) { const x = col[i]; if (isNum(x)) { if (x < lo) lo = x; if (x > hi) hi = x; } }
    if (!(hi > lo)) { lo = isNum(lo) ? lo : 0; hi = lo + 1; }
    const BINS = 24;
    const binOf = (x) => (!isNum(x) ? 0 : Math.min(BINS - 1, Math.max(0, Math.floor(((x - lo) / (hi - lo)) * BINS))));
    const runs = segmentsOf();
    const lines = runs.map((r) => r.map((i) => [LAT[i], LON[i]]));
    L.polyline(lines, { color: '#0B0D12', weight: 7, opacity: 0.5, interactive: false }).addTo(trackGroup);
    for (const r of runs) {
      let seg = [], bin = null;
      const flush = () => { if (seg.length > 1) L.polyline(seg, { color: col ? rampColor((bin + 0.5) / BINS) : C.a, weight: 4, opacity: 1, interactive: false }).addTo(trackGroup); };
      for (const i of r) {
        const b = col ? binOf(col[i]) : 0;
        const p = [LAT[i], LON[i]];
        if (bin === null) { bin = b; seg = [p]; continue; }
        seg.push(p);
        if (b !== bin) { flush(); seg = [p]; bin = b; }
      }
      flush();
    }
    const hit = L.polyline(lines, { color: '#000', weight: 14, opacity: 0, interactive: true }).addTo(trackGroup);
    hit.on('click', (e) => { seek(T[nearestIdx(e.latlng)] * 1000); });
    hit.bindTooltip(() => 'Click to jump here', { sticky: true });
    if (legendEl) legendEl.remove();
    const conv = st.colorBy === 'speed' ? units.spdV : units.altV;
    const u = st.colorBy === 'speed' ? units.spd() : units.alt();
    legendEl = h('div', { class: 'legend br' },
      h('div', { style: { fontWeight: 650 } }, st.colorBy === 'speed' ? `Ground speed (${u})` : `Altitude (${u})`),
      col ? [h('div', { class: 'ramp', style: { background: `linear-gradient(90deg, ${RAMP.join(',')})` } }),
        h('div', { class: 'ramp-l' }, h('span', null, num(conv(lo), 0)), h('span', null, num(conv(hi), 0)))]
        : h('div', { class: 'dim' }, 'not recorded'));
    mapWrap.appendChild(legendEl);
    if (fit) fitAll();
  }
  function fitAll() {
    const bb = L.latLngBounds([]);
    if (trackGroup.getLayers().length) bb.extend(trackGroup.getBounds());
    if (linkGroup && linkGroup.getLayers().length) bb.extend(linkGroup.getBounds());
    if (!bb.isValid()) return;
    const mz = () => Math.min(19, (map.ddNativeMax ? map.ddNativeMax() : 18) + 1);   // avoid heavy overzoom of raster tiles
    map.fitBounds(bb, { padding: [50, 50], maxZoom: mz() });
    setTimeout(() => { map.invalidateSize(); map.fitBounds(bb, { padding: [50, 50], maxZoom: mz(), animate: false }); }, 250);
  }
  async function drawLinks() {
    try {
      const gj = await call('get_track_geojson', overlayLinks.map((l) => l.flight_id));
      if (!map) return;
      const leg = h('div', { class: 'legend tl', style: { left: '56px', top: hasTrack ? '10px' : '96px' } }, h('div', { style: { fontWeight: 650, marginBottom: '2px' } }, 'Linked records'));
      overlayLinks.forEach((l, k) => {
        const color = FLIGHT_COLORS[(k + 1) % FLIGHT_COLORS.length];
        const layers = [];
        for (const f of (gj && gj.features) || []) {
          if (f.properties.flight_id !== l.flight_id || f.geometry.type !== 'LineString') continue;
          const ll = f.geometry.coordinates.filter((c) => isNum(c[0]) && isNum(c[1])).map((c) => [c[1], c[0]]);
          if (ll.length < 2) continue;
          layers.push(L.polyline(ll, { color, weight: 3, opacity: 0.9, dashArray: '6 6' })
            .bindTooltip(`#${l.flight_id} ${esc(l.artifact_name || '')} (${esc((SOURCE[l.source] || [l.source || ''])[0])})`, { sticky: true })
            .on('click', () => go(`flight/${l.flight_id}`)).addTo(linkGroup));
        }
        const cb = h('input', { type: 'checkbox', checked: true, onchange: (e) => layers.forEach((x) => (e.target.checked ? x.addTo(linkGroup) : linkGroup.removeLayer(x))) });
        leg.appendChild(h('label', { class: 'row check' }, cb, h('span', { class: 'sw', style: { background: `repeating-linear-gradient(90deg, ${color} 0 6px, transparent 6px 10px)` } }),
          `#${l.flight_id} ${(SOURCE[l.source] || [l.source || 'record'])[0]}`, layers.length ? null : h('span', { class: 'dim' }, ' (no track)')));
      });
      mapWrap.appendChild(leg);
      if (!hasTrack && linkGroup.getLayers().length) fitAll(); else if (hasTrack) fitAll();
    } catch (e) { console.warn('linked tracks unavailable', e); }
  }
  function nearestIdx(ll) {
    let best = fixIdx[0], bd = Infinity;
    for (const i of fixIdx) {
      const d = (LAT[i] - ll.lat) ** 2 + (LON[i] - ll.lng) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }
  function posAt(ms) {                     // frame index at time ms (last frame with T <= t)
    if (!n) return 0;
    const ti = ms / 1000;
    if (ti <= T[0]) return 0;
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (T[m] <= ti) lo = m; else hi = m; }
    return T[hi] <= ti ? hi : lo;
  }
  function fixAt(i) {                      // position in fixIdx of the last fix at or before frame i
    let lo = 0, hi = fixIdx.length - 1;
    if (i <= fixIdx[0]) return 0;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (fixIdx[m] <= i) lo = m; else hi = m; }
    return fixIdx[hi] <= i ? hi : lo;
  }
  function addMarkers() {
    const flying = fr.is_flying || [];
    let iTake = -1, iLand = -1;
    for (const i of fixIdx) if (flying[i] === true) { iTake = i; break; }
    for (let k = fixIdx.length - 1; k >= 0; k--) if (flying[fixIdx[k]] === true) { iLand = fixIdx[k]; break; }
    if (an.takeoff?.t_offset_ms != null) { const j = fixIdx[fixAt(posAt(an.takeoff.t_offset_ms))]; if (j != null) iTake = j; }
    if (an.landing?.t_offset_ms != null) { const j = fixIdx[fixAt(posAt(an.landing.t_offset_ms))]; if (j != null) iLand = j; }
    if (iTake < 0) iTake = fixIdx[0];
    if (iLand < 0) iLand = fixIdx[fixIdx.length - 1];
    for (const hp of (an.home_points || [])) {
      if (hp.rtk || homeLat == null || (Math.abs(hp.lat - homeLat) < 0.00002 && Math.abs(hp.lon - homeLon) < 0.00002)) continue;
      L.circleMarker([hp.lat, hp.lon], { radius: 5, color: '#E0A43A', weight: 2, fillOpacity: 0.15 })
        .bindTooltip(`Home point ${hp.kind} · ${hp.t_offset_ms != null ? fmtOffset(hp.t_offset_ms, 0) : ''} · ${hp.source}`).addTo(markerGroup);
    }
    const forced = events.some((e) => e.severity === 'critical' && /forced/i.test(e.message || ''));
    const at = (i) => [LAT[i], LON[i]];
    const tAt = (i) => T[i] * 1000;
    const nearHome = homeLat != null && Math.abs(LAT[iTake] - homeLat) < 0.00006 && Math.abs(LON[iTake] - homeLon) < 0.00008;
    if (homeLat != null) divMarker([homeLat, homeLon], 'mk-home', 'house', 26, 'Home point')
      .bindTooltip((homeDerived ? 'Home point (first GPS fix)' : 'Home point') + (nearHome ? ` · takeoff ${fmtOffset(tAt(iTake), 0)}` : ''))
      .on('click', () => nearHome && seek(tAt(iTake))).addTo(markerGroup);
    if (!nearHome) divMarker(at(iTake), 'mk-takeoff', 'plane-takeoff', 24, 'Takeoff').bindTooltip(`Takeoff / first fix · ${fmtOffset(tAt(iTake), 0)}`)
      .on('click', () => seek(tAt(iTake))).addTo(markerGroup);
    if (iLand !== iTake) divMarker(at(iLand), forced ? 'mk-landx' : 'mk-land', 'plane-landing', 24, 'Landing').bindTooltip(`${forced ? 'Forced landing' : 'Landing / last fix'} · ${fmtOffset(tAt(iLand), 0)}`)
      .on('click', () => seek(tAt(iLand))).addTo(markerGroup);
    // warn/critical events: first occurrence of each distinct message, capped
    const seen = new Set(); let count = 0;
    for (const e of events) {
      if (count >= 150) break;
      if ((SEV_RANK[e.severity] || 0) < 1 || !isNum(e.t_offset_ms)) continue;
      const key = `${e.severity}|${e.kind}|${e.message}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const i = fixIdx[fixAt(posAt(e.t_offset_ms))];
      if (i == null) continue;
      const color = e.severity === 'critical' ? cssVar('--bad') : cssVar('--warn');
      L.marker(at(i), { icon: L.divIcon({ html: `<div class="mk-ev" style="background:${color}"></div>`, className: '', iconSize: [12, 12], iconAnchor: [6, 6] }), keyboard: false })
        .bindTooltip(`${fmtOffset(e.t_offset_ms, 0)} · ${esc(e.message)}`).on('click', () => seek(e.t_offset_ms)).addTo(markerGroup);
      count++;
    }
  }

  // map tools (color by, follow)
  if (hasTrack) {
    const colorSeg = h('div', { class: 'seg' },
      h('button', { class: st.colorBy === 'alt' ? 'on' : '', onclick: () => setColor('alt') }, 'Altitude'),
      h('button', { class: st.colorBy === 'speed' ? 'on' : '', onclick: () => setColor('speed') }, 'Speed'));
    const followBtn = h('button', { class: 'btn sm', title: 'Keep the aircraft in view (F)', onclick: () => toggleFollow() }, icon('crosshair'), 'Follow');
    const fitBtn = h('button', { class: 'btn sm', title: 'Fit track', onclick: () => fitAll() }, icon('maximize-2'));
    mapWrap.appendChild(h('div', { class: 'map-tools' }, h('span', { class: 'legend', style: { position: 'static', padding: '0 8px', display: 'flex', alignItems: 'center', gap: '6px', boxShadow: 'none' } }, 'Color by', colorSeg), followBtn, fitBtn));
    function setColor(by) { st.colorBy = by; setPref('trackColor', by); [...colorSeg.children].forEach((b, i) => b.classList.toggle('on', (i === 0) === (by === 'alt'))); drawTrack(false); }
    function toggleFollow() { st.follow = !st.follow; followBtn.classList.toggle('primary', st.follow); }
    st.toggleFollow = toggleFollow;
  }

  const readout = h('div', { class: 'readout' });

  // ---------------------------------------------------------------- player
  const playBtn = h('button', { class: 'btn primary icon-only', title: 'Play / pause (Space)', onclick: () => togglePlay() }, icon('play'));
  const range = h('input', { type: 'range', min: Math.round(tMin * 1000), max: Math.round(tMax * 1000), step: 50, value: Math.round(tMin * 1000), 'aria-label': 'Playback position',
    oninput: () => seek(+range.value, true) });
  const ticks = h('div', { class: 'ticks' });
  const timeEl = h('div', { class: 'time' });
  const speedSel = h('select', { class: 'input sm', title: 'Playback speed ([ and ])', onchange: () => { st.speed = +speedSel.value; setPref('playSpeed', st.speed); } },
    SPEEDS.map((s) => h('option', { value: s, selected: s === st.speed || null }, `${s}×`)));
  const player = h('div', { class: 'player' },
    h('button', { class: 'btn sm ghost icon-only', title: 'Back 10 s (Shift+Left)', onclick: () => seek(st.t - 10000) }, icon('rewind')),
    playBtn,
    h('button', { class: 'btn sm ghost icon-only', title: 'Forward 10 s (Shift+Right)', onclick: () => seek(st.t + 10000) }, icon('fast-forward')),
    timeEl, h('div', { class: 'scrub' }, ticks, range), speedSel,
    h('span', { class: 'kbd', title: 'Space play/pause · Left/Right 1 s · Shift 10 s · Home/End · [ ] speed · F follow' }, 'Space'));
  const span = Math.max(1e-6, tMax - tMin);
  if (hasFrames) {
    main.appendChild(player);
    if (hasTrack) main.appendChild(readout);
    const used = new Set();
    for (const e of events) {
      if (!isNum(e.t_offset_ms)) continue;
      if (e.severity === 'info' && !['takeoff', 'landing', 'rth'].includes(e.kind)) continue;
      const pct = Math.max(0, Math.min(100, ((e.t_offset_ms / 1000 - tMin) / span) * 100));
      const key = `${e.severity}|${Math.round(pct * 4)}`;
      if (used.has(key)) continue;
      used.add(key);
      ticks.appendChild(h('i', { style: { left: `calc(${pct}% - 1px)`, background: SEV[e.severity] || 'var(--info)' }, title: e.message }));
    }
  }

  // ---------------------------------------------------------------- charts
  const chartsEl = h('div', { class: 'fd-charts' });
  if (hasFrames) main.appendChild(chartsEl);
  if (showMap && !hasFrames) mapWrap.style.flex = '1 1 100%';
  if (showMap && hasFrames && !hasTrack) mapWrap.style.flex = '0 0 34%';
  let plots = [];
  let syncing = false;
  function col(name, conv) {
    const a = fr[name]; if (!a) return null;
    let any = false;
    const out = new Array(n);
    for (let i = 0; i < n; i++) { const v = a[i]; if (isNum(v)) { any = true; out[i] = conv ? conv(v) : v; } else out[i] = null; }
    return any ? out : null;
  }
  function buildCharts() {
    for (const p of plots) p.u.destroy();
    plots = []; clear(chartsEl);
    if (!hasFrames) return;
    const vs = col('vel_d', (v) => units.vspdV(-v));
    const altCol = fr.alt_rel_m ? 'alt_rel_m' : fr.height_m ? 'height_m' : 'alt_msl_m';
    const defs = [
      { title: altFrom > 0 ? 'Altitude (from first GPS fix)' : 'Altitude', unit: units.alt(), series: [
        { label: `${altCol === 'alt_msl_m' ? 'Altitude MSL' : 'Altitude above takeoff'} (${units.alt()})`, data: col(altCol, units.altV), stroke: C.a, scale: 'y' },
        { label: `Distance from home (${units.alt()})`, data: col('distance_m', units.altV), stroke: C.e, scale: 'r', dash: [5, 4] }] },
      { title: 'Speed', unit: `${units.spd()} / ${units.vspd()}`, series: [
        { label: `Ground speed (${units.spd()})`, data: col('speed_h_ms', units.spdV), stroke: C.a, scale: 'y' },
        { label: `Vertical speed, up + (${units.vspd()})`, data: vs, stroke: C.b, scale: 'r' }] },
      { title: 'Battery', unit: '% / V', series: [
        { label: 'Battery (%)', data: col('battery_pct'), stroke: C.c, scale: 'y', range: [0, 100] },
        { label: 'Pack voltage (V)', data: col('battery_v'), stroke: C.e, scale: 'r' }] },
      { title: 'RC sticks', unit: '-1 .. 1', series: [
        { label: 'Aileron', data: col('rc_aileron'), stroke: C.a, scale: 'y', range: [-1.05, 1.05] },
        { label: 'Elevator', data: col('rc_elevator'), stroke: C.b, scale: 'y' },
        { label: 'Throttle', data: col('rc_throttle'), stroke: C.c, scale: 'y' },
        { label: 'Rudder', data: col('rc_rudder'), stroke: C.d, scale: 'y' }] },
      { title: 'GPS', unit: 'satellites / health 0-5', series: [
        { label: 'Satellites', data: col('gps_sats'), stroke: C.a, scale: 'y' },
        { label: 'GPS health (0-5)', data: col('gps_health'), stroke: C.e, scale: 'r', range: [0, 5.2], step: true }] },
      { title: 'Attitude', unit: 'deg', series: [
        { label: 'Pitch (deg)', data: col('pitch'), stroke: C.a, scale: 'y' },
        { label: 'Roll (deg)', data: col('roll'), stroke: C.b, scale: 'y' }] },
    ];
    const missing = [];
    const xs = Array.from(T);
    const grid = cssVar('--border'), axis = cssVar('--text-3');
    for (const d of defs) {
      const ser = d.series.filter((s) => s.data);
      for (const s of d.series) if (!s.data) missing.push(s.label.replace(/ \(.*\)$/, ''));
      if (!ser.length) continue;
      const box = h('div', { class: 'chart' }, h('div', { class: 'chart-h' }, d.title, h('span', { class: 'u' }, d.unit)));
      chartsEl.appendChild(box);
      const hasR = ser.some((s) => s.scale === 'r');
      const hasY = ser.some((s) => s.scale === 'y');
      const scales = { x: { time: false } };
      const yR = ser.find((s) => s.scale === 'y' && s.range), rR = ser.find((s) => s.scale === 'r' && s.range);
      scales.y = yR ? { range: yR.range } : { auto: true };
      if (hasR) scales.r = rR ? { range: rR.range } : { auto: true };
      const axes = [
        { stroke: axis, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, values: (u, vals) => vals.map((v) => fmtOffset(v * 1000, 0)), size: 30, font: '11px Inter, sans-serif' },
        { stroke: axis, grid: { stroke: grid, width: 1, show: hasY }, ticks: { stroke: grid }, size: 46, font: '11px Inter, sans-serif', scale: 'y', show: hasY },
      ];
      if (hasR) axes.push({ side: 1, scale: 'r', stroke: axis, grid: { show: !hasY, stroke: grid, width: 1 }, ticks: { stroke: grid }, size: 46, font: '11px Inter, sans-serif' });
      const opts = {
        width: Math.max(300, box.clientWidth - 18), height: 150,
        scales, axes, legend: { live: true },
        cursor: { sync: { key: 'dd-flight' }, drag: { x: true, y: false, setScale: true }, points: { size: 6 } },
        series: [{ label: 'Time', value: (u, v) => (v == null ? '—' : `${fmtOffset(v * 1000, 1)}  ${t0utc ? fmtUtcTime(absAt(v * 1000)) : ''}`) },
          ...ser.map((s) => ({ label: s.label, stroke: s.stroke, width: 1.5, scale: s.scale, dash: s.dash, spanGaps: false,
            paths: s.step ? uPlot.paths.stepped({ align: 1 }) : undefined, points: { show: false },
            value: (u, v) => (v == null ? '—' : num(v, Math.abs(v) < 10 ? 2 : 1)) }))],
        hooks: {
          draw: [(u) => drawEvents(u)],
          setScale: [(u, key) => {
            if (key !== 'x' || syncing) return;
            syncing = true;
            for (const p of plots) if (p.u !== u) p.u.setScale('x', { min: u.scales.x.min, max: u.scales.x.max });
            syncing = false;
            placeHeads();
          }],
        },
      };
      let u;
      try { u = new uPlot(opts, [xs, ...ser.map((s) => s.data)], box); }
      catch (e) { box.appendChild(h('div', { class: 'dim', style: { padding: '8px' } }, `Chart unavailable: ${e.message}`)); continue; }
      const head = h('div', { class: 'playhead' });
      u.over.appendChild(head);
      let down = null;
      u.over.addEventListener('mousedown', (e) => { down = [e.clientX, e.clientY]; });
      u.over.addEventListener('mouseup', (e) => {
        if (down && Math.abs(e.clientX - down[0]) < 4 && Math.abs(e.clientY - down[1]) < 4 && u.cursor.left != null && u.cursor.left >= 0) seek(u.posToVal(u.cursor.left, 'x') * 1000);
        down = null;
      });
      u.over.title = 'Click to seek · drag to zoom · double-click to reset zoom';
      plots.push({ u, head });
    }
    if (missing.length) chartsEl.appendChild(h('div', { class: 'dim', style: { fontSize: '12px', padding: '2px 4px 8px' } }, `Not recorded in this log: ${missing.join(', ')}.`));
    placeHeads();
  }
  const evMarks = events.filter((e) => e.severity !== 'info' && isNum(e.t_offset_ms)).slice(0, 400);
  function drawEvents(u) {
    const ctx = u.ctx;
    ctx.save();
    let lastX = -1e9;
    for (const e of evMarks) {
      const x = u.valToPos(e.t_offset_ms / 1000, 'x', true);
      if (x < u.bbox.left || x > u.bbox.left + u.bbox.width || x - lastX < 3) continue;
      lastX = x;
      ctx.strokeStyle = e.severity === 'critical' ? cssVar('--bad') : cssVar('--warn');
      ctx.globalAlpha = 0.55; ctx.lineWidth = 1 * devicePixelRatio; ctx.setLineDash([3 * devicePixelRatio, 3 * devicePixelRatio]);
      ctx.beginPath(); ctx.moveTo(x, u.bbox.top); ctx.lineTo(x, u.bbox.top + u.bbox.height); ctx.stroke();
    }
    ctx.restore();
  }
  function placeHeads() {
    for (const p of plots) {
      const x = p.u.valToPos(st.t / 1000, 'x');
      const w = p.u.over.clientWidth || p.u.bbox.width / devicePixelRatio;
      p.head.style.display = !Number.isFinite(x) || x < 0 || x > w ? 'none' : '';
      p.head.style.left = x + 'px';
    }
  }
  buildCharts();
  const ro = new ResizeObserver(() => { for (const p of plots) p.u.setSize({ width: Math.max(300, p.u.root.parentElement.clientWidth - 18), height: 150 }); placeHeads(); });
  ro.observe(chartsEl);
  cleanup.push(() => ro.disconnect(), () => plots.forEach((p) => p.u.destroy()));

  // ---------------------------------------------------------------- side panel
  const tabs = h('div', { class: 'tabs' });
  const body = h('div', { class: 'side-body' });
  side.append(tabs, body);
  let tab = 'summary';
  const nWarn = events.filter((e) => e.severity !== 'info').length;
  const nKey = events.filter((e) => e.explain && e.explain.importance === 'high').length;
  let evFilter = events.length > 300 && nKey ? 'key' : events.length > 300 && nWarn ? 'warn' : 'all';
  let evRows = [], evRowTimes = [], curRow = null;
  const EV_CAP = 2500;
  function renderTabs() {
    clear(tabs);
    for (const [k, label, cnt] of [['summary', 'Summary'], ['ids', 'Identifiers', (an.identifiers || []).length], ['events', 'Events', events.length], ['details', 'Details'], ['source', 'Source']]) {
      tabs.appendChild(h('button', { class: tab === k ? 'on' : '', onclick: () => { tab = k; renderTabs(); renderBody(); } }, label, cnt != null ? h('span', { class: 'n' }, num(cnt)) : null));
    }
  }
  function detailValue(fmt, v) {
    if (fmt === 'mono') return hashEl(String(v), { full: true });
    if (fmt === 'utc') return h('span', null, h('span', { class: 'mono' }, fmtUtc(v)), h('div', { class: 'dim', style: { fontSize: '12px' } }, fmtLocal(v)));
    if (fmt === 'dur') return fmtDur(v);
    if (fmt === 'dist') return fmtDist(v);
    if (fmt === 'alt') return fmtAlt(v);
    if (fmt === 'spd') return fmtSpd(v);
    if (fmt === 'vspd') return fmtVSpd(v);
    if (fmt === 'coord') return h('span', { class: 'mono' }, num(v, 6));
    if (fmt === 'int') return num(v);
    if (typeof v === 'object') return h('pre', { class: 'json', style: { maxHeight: '160px', margin: 0 } }, JSON.stringify(v, null, 1));
    return String(v);
  }
  function renderBody() {
    clear(body); evRows = []; evRowTimes = []; curRow = null;
    if (tab === 'summary') { renderSummary(); return; }
    if (tab === 'ids') { renderIds(); return; }
    if (tab === 'events') {
      body.appendChild(h('div', { class: 'ev-filter' }, [['key', `Key events (${nKey})`], ['all', 'All'], ['warn', 'Warnings'], ['critical', 'Critical']].map(([k, l]) =>
        h('button', { class: 'chip' + (evFilter === k ? ' on' : ''), onclick: () => { evFilter = k; renderBody(); } }, l))));
      const list = events.filter((e) => evFilter === 'all' || (evFilter === 'key' ? (e.explain && e.explain.importance === 'high') || e.severity === 'critical'
        : evFilter === 'warn' ? e.severity !== 'info' : e.severity === 'critical'));
      if (!list.length) body.appendChild(h('div', { class: 'side-sec dim' }, events.length ? 'No events at this level.' : 'This log has no events.'));
      const frag = document.createDocumentFragment();
      for (const e of list.slice(0, EV_CAP)) {
        const timed = isNum(e.t_offset_ms);
        const abs = timed ? absAt(e.t_offset_ms) : null;
        const row = h('div', { class: 'ev', onclick: () => {
          if (!timed || !hasFrames) return;
          seek(e.t_offset_ms);
          if (hasTrack) { const i = fixIdx[fixAt(posAt(e.t_offset_ms))]; if (i != null) map.panTo([LAT[i], LON[i]]); }
        } },
          h('div', { class: 'bar sev-' + (e.severity || 'info') }),
          h('div', { class: 't' }, timed ? fmtOffset(e.t_offset_ms, 1) : '—', h('span', { class: 'u' }, abs ? fmtUtcTime(abs) : '')),
          h('div', { class: 'm' }, h('div', { class: 'k' }, e.explain ? `${(e.explain.category || '').replace(/_/g, ' ')} · ` : '', (e.kind || '').replace(/_/g, ' ')),
            e.explain ? h('div', { class: 'ex' }, e.explain.title) : null,
            e.explain && e.explain.detail ? h('div', { class: 'exd' }, e.explain.detail) : null,
            h('div', { class: e.explain ? 'orig' : '' }, e.message || ''), e.raw ? h('div', { class: 'raw' }, String(e.raw).slice(0, 300)) : null));
        if (timed) { evRows.push(row); evRowTimes.push(e.t_offset_ms); }
        frag.appendChild(row);
      }
      body.appendChild(frag);
      if (list.length > EV_CAP) body.appendChild(h('div', { class: 'side-sec dim' }, `Showing the first ${num(EV_CAP)} of ${num(list.length)} events. Filter to warnings to narrow the list.`));
      markCurrentEvent();
    } else if (tab === 'details') {
      const dec = data.decimation || {};
      const extra = Object.entries(details).filter(([k, v]) => !LABELLED.has(k) && v != null && v !== '' && !(typeof v === 'object' && !Object.keys(v).length));
      body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Log header'),
        h('dl', { class: 'kv' }, DETAIL_LABELS.map(([k, label, fmt]) => {
          const v = details[k];
          if (v == null || v === '') return null;
          return [h('dt', null, label), h('dd', null, detailValue(fmt, v))];
        }))));
      if (extra.length) body.appendChild(h('details', { class: 'side-sec' }, h('summary', { style: { cursor: 'pointer', color: 'var(--text-2)', fontSize: '12px', fontWeight: 650, textTransform: 'uppercase', letterSpacing: '.6px' } }, `Other header fields (${extra.length})`),
        h('dl', { class: 'kv', style: { marginTop: '8px' } }, extra.map(([k, v]) => [h('dt', { class: 'mono', style: { fontSize: '11.5px' } }, k), h('dd', null, detailValue(typeof v === 'object' ? 'obj' : 'plain', v))]))));
      body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Telemetry'), h('dl', { class: 'kv' },
        h('dt', null, 'Frames'), h('dd', null, num(dec.total ?? flight.frame_count ?? n)),
        h('dt', null, 'Shown'), h('dd', null, dec.method && dec.method !== 'none' ? `${num(dec.returned)} (${dec.method}, display only)` : n ? 'all frames' : 'none'),
        h('dt', null, 'GPS fixes'), h('dd', null, n ? `${num(fixIdx.length)} of ${num(n)} shown frames` : '—'),
        flight.records_total != null ? [h('dt', null, 'Records'), h('dd', null, `${num(flight.records_decoded)} decoded of ${num(flight.records_total)}${flight.records_failed ? `, ${num(flight.records_failed)} failed` : ''}`)] : null,
        flight.hz ? [h('dt', null, 'Rate'), h('dd', null, `${num(flight.hz)} Hz`)] : null,
        h('dt', null, 'Adapter'), h('dd', { class: 'mono' }, `${flight.adapter || '—'}`),
        h('dt', null, 'Version'), h('dd', { class: 'mono', style: { fontSize: '12px' } }, flight.adapter_version || '—'))));
    } else {
      const art = (store.artifacts || []).find((a) => a.id === flight.artifact_id);
      body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Source artifact'), h('dl', { class: 'kv' },
        h('dt', null, 'File'), h('dd', { style: { fontWeight: 600 } }, flight.artifact_name || art?.name || '—'),
        h('dt', null, 'Evidence'), h('dd', null, flight.evidence_label || art?.evidence_label || '—'),
        art ? [h('dt', null, 'Inner path'), h('dd', { class: 'mono', style: { fontSize: '11.5px' } }, art.inner_path || '—'),
          h('dt', null, 'SHA-256'), h('dd', null, hashEl(art.sha256, { full: true })),
          h('dt', null, 'Detected'), h('dd', { class: 'mono' }, art.detected_subtype || art.detected_kind || '—'),
          h('dt', null, 'Deleted'), h('dd', null, art.deleted ? badge('Recovered deleted file', 'b-del', 'trash-2') : art.deleted_sources ? `${art.deleted_sources} deleted copy` : 'No')] : null,
        flight.result_sha256 ? [h('dt', null, 'Parse result'), h('dd', null, hashEl(flight.result_sha256, { full: true }))] : null)));
      const rejected = allLinks.filter((l) => linkKind(l.kind).hidden);
      if (allLinks.length) {
        const linkRow = (l) => h('div', { style: { marginBottom: '10px' } },
          h('a', { href: `#/flight/${l.flight_id}`, style: { fontWeight: 600 } }, linkKind(l.kind).label(l.flight_id)),
          h('span', { class: 'dim' }, ` · ${(SOURCE[l.source] || [l.source || ''])[0]}${l.score != null ? ` · score ${num(l.score, 2)}` : ''}`),
          l.artifact_name ? h('div', { class: 'mono dim', style: { fontSize: '11.5px' } }, l.artifact_name) : null,
          h('ul', { class: 'reasons' }, (l.reasons || []).map((r) => h('li', null, r))));
        const rejBox = h('div', { hidden: true }, rejected.map(linkRow));
        body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Correlation'),
          links.length ? links.map(linkRow) : h('div', { class: 'dim', style: { marginBottom: '8px' } }, 'No accepted links.'),
          rejected.length ? h('label', { class: 'check', style: { fontSize: '12px', color: 'var(--text-2)' } },
            h('input', { type: 'checkbox', onchange: (e) => { rejBox.hidden = !e.target.checked; } }), `Show rejected candidates (${rejected.length})`) : null,
          rejBox));
      }
      if (warnings.length) body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Warnings'), h('ul', { class: 'reasons' }, warnings.map((w) => h('li', null, w)))));
    }
  }
  function placeKV(label, p) {
    if (!p) return null;
    return [h('dt', null, label), h('dd', null,
      p.t_utc ? h('div', { class: 'mono' }, fmtUtc(p.t_utc), h('span', { class: 'dim' }, ` · ${fmtLocal(p.t_utc)}`)) : (p.t_offset_ms != null ? h('div', { class: 'mono' }, fmtOffset(p.t_offset_ms, 0)) : null),
      p.lat != null ? h('div', { class: 'mono', style: { fontSize: '12px' } }, `${num(p.lat, 6)}, ${num(p.lon, 6)} `,
        h('button', { class: 'btn sm ghost icon-only', title: 'Copy coordinates', onclick: () => copyText(`${p.lat.toFixed(6)}, ${p.lon.toFixed(6)}`) }, icon('copy'))) : null,
      p.location ? h('div', null, p.location.summary) : null,
      p.source ? h('div', { class: 'dim', style: { fontSize: '12px' } }, p.source) : null,
      p.distance_from_takeoff_m != null ? h('div', { class: 'dim', style: { fontSize: '12px' } }, `${num(p.distance_from_takeoff_m, 0)} m from the takeoff point`) : null,
      p.note ? h('div', { class: 'banner warn', style: { marginTop: '6px', fontSize: '12px' } }, icon('triangle-alert'), h('div', null, p.note)) : null)];
  }
  function renderSummary() {
    const s = an.stats || {};
    body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Record summary'),
      h('div', { style: { marginBottom: '8px' } }, classBadge(an, true)),
      h('p', { style: { margin: '0 0 8px', lineHeight: 1.5 } }, an.narrative || 'No summary available.'),
      (an.location && an.location.label) ? h('div', { class: 'dim', style: { fontSize: '12px' } }, `Location: ${an.location.label} (nearest named place, GeoNames; not a postal address)`) : null,
      an.app_location ? h('div', { class: 'dim', style: { fontSize: '12px' } }, `App-recorded location: ${an.app_location}`) : null));
    body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Classification basis'),
      h('ul', { class: 'reasons' }, (an.evidence || []).map((x) => h('li', null, x))),
      an.method ? h('div', { class: 'dim', style: { fontSize: '11.5px' } }, `Rule: ${an.method.rules}`) : null));
    body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Takeoff, landing and home'), h('dl', { class: 'kv' },
      placeKV('Takeoff', an.takeoff), placeKV('Landing', an.landing), placeKV('Home point', an.home),
      (an.rth || []).length ? [h('dt', null, 'Return home'), h('dd', null, an.rth.map((r) => h('div', { class: 'mono', style: { fontSize: '12px' } }, `${r.mode} at ${r.t_start_utc ? fmtUtc(r.t_start_utc) : fmtOffset(r.t_start_ms, 0)}`)))] : null)));
    const stat = (k, v) => (v == null || v === '' ? null : [h('dt', null, k), h('dd', null, v)]);
    body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Flight statistics'), h('dl', { class: 'kv' },
      stat('Air time', s.air_time_s != null ? fmtDur(s.air_time_s) : null), stat('Log length', s.record_duration_s != null ? fmtDur(s.record_duration_s) : null),
      stat('Takeoffs', s.takeoffs || null), stat('Distance flown', s.path_distance_m != null ? fmtDist(s.path_distance_m) : null),
      stat('Max height', s.max_height_m != null ? fmtAlt(s.max_height_m) : null), stat('Max speed', s.max_speed_ms != null ? fmtSpd(s.max_speed_ms) : null),
      stat('Max vertical speed', s.max_vspeed_ms != null ? fmtVSpd(s.max_vspeed_ms) : null),
      stat('Furthest from home', s.max_distance_from_home_m != null ? fmtDist(s.max_distance_from_home_m) : null),
      stat('Battery', s.battery_start_pct != null ? `${num(s.battery_start_pct, 0)}% → ${num(s.battery_end_pct, 0)}%` : null),
      stat('Lowest pack voltage', s.battery_min_v != null ? `${num(s.battery_min_v, 2)} V` : null),
      stat('GPS fixes', s.gps_points ? num(s.gps_points) : null), stat('Max satellites', s.gps_sats_max),
      stat('Flight modes', (s.flight_modes || []).join(', ') || null))));
    const ids = (an.identifiers || []).filter((i) => i.group !== 'File' && i.group !== 'Sensors').slice(0, 10);
    if (ids.length) body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Key identifiers'),
      h('dl', { class: 'kv' }, ids.map((i) => [h('dt', null, i.label), h('dd', { class: 'mono' }, i.value)])),
      h('button', { class: 'btn sm', style: { marginTop: '8px' }, onclick: () => { tab = 'ids'; renderTabs(); renderBody(); } }, `All identifiers (${(an.identifiers || []).length})`)));
    if ((an.timeline || []).length) body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, 'Timeline'),
      an.timeline.map((e) => h('div', { class: 'ev', onclick: () => { if (e.t_offset_ms != null && hasFrames) seek(e.t_offset_ms); } },
        h('div', { class: 'bar sev-info' }), h('div', { class: 't' }, e.t_offset_ms != null ? fmtOffset(e.t_offset_ms, 0) : '—',
          h('span', { class: 'u' }, e.t_utc ? e.t_utc.slice(11, 19) : '')), h('div', { class: 'm' }, e.event)))));
  }
  function renderIds() {
    const ids = an.identifiers || [];
    if (!ids.length) { body.appendChild(h('div', { class: 'side-sec dim' }, 'No identifiers were found in this record.')); return; }
    body.appendChild(h('div', { class: 'side-sec' }, h('button', { class: 'btn sm', onclick: () => copyText(ids.map((i) => [i.group, i.label, i.value, i.source].join('\t')).join('\n')) }, icon('copy'), 'Copy all as table')));
    for (const g of [...new Set(ids.map((i) => i.group))]) {
      body.appendChild(h('div', { class: 'side-sec' }, h('h3', null, g), ids.filter((i) => i.group === g).map((i) => h('div', { style: { marginBottom: '8px' } },
        h('div', { class: 'dim', style: { fontSize: '12px' } }, i.label, i.seen > 1 ? ` (seen ${i.seen}x)` : ''),
        h('div', { class: 'mono', style: { fontWeight: 600, display: 'flex', gap: '6px', alignItems: 'center' } }, i.value,
          h('button', { class: 'btn sm ghost icon-only', title: 'Copy', onclick: () => copyText(i.value) }, icon('copy'))),
        h('div', { class: 'dim mono', style: { fontSize: '10.5px', overflowWrap: 'anywhere' } }, i.source + (i.t_offset_ms != null ? ` @ ${fmtOffset(i.t_offset_ms, 1)}` : ''))))));
    }
  }
  renderTabs(); renderBody();
  if (!store.artifacts) load('artifacts').then(() => { if (tab === 'source') renderBody(); }).catch(() => {});

  function markCurrentEvent() {
    if (!evRows.length) return null;
    let lo = -1, hi = evRowTimes.length - 1;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (evRowTimes[m] <= st.t + 1) lo = m; else hi = m - 1; }
    const row = lo >= 0 ? evRows[lo] : null;
    if (row !== curRow) { if (curRow) curRow.classList.remove('current'); if (row) row.classList.add('current'); curRow = row; }
    return row;
  }

  // ---------------------------------------------------------------- playback
  let lastTrail = 0, raf = 0, lastFrameTs = 0;
  function update(scrollEvents) {
    if (!hasFrames) return;
    const i = posAt(st.t);
    st.idx = i;
    range.value = Math.round(st.t);
    const abs = absAt(st.t);
    timeEl.replaceChildren(h('span', { class: 'utc' }, abs ? fmtUtcTime(abs, true) : 'UTC not recorded'), h('br'),
      h('span', { class: 'rel' }, `${fmtOffset(st.t, 1)} / ${fmtOffset(tMax * 1000, 0)}`));
    if (hasTrack) {
      const k = fixAt(i), a = fixIdx[k], b = fixIdx[Math.min(fixIdx.length - 1, k + 1)];
      const ti = st.t / 1000;
      let lat = LAT[a], lon = LON[a];
      if (b !== a && ti > T[a] && T[b] > T[a] && b - a < 50) {
        const f = Math.min(1, (ti - T[a]) / (T[b] - T[a]));
        lat += (LAT[b] - LAT[a]) * f; lon += (LON[b] - LON[a]) * f;
      }
      aircraft.setLatLng([lat, lon]);
      const svg = aircraft.getElement() && aircraft.getElement().querySelector('svg');
      const yaw = fr.yaw && isNum(fr.yaw[i]) ? fr.yaw[i] : null;
      if (svg && yaw != null) svg.style.transform = `rotate(${yaw}deg)`;
      const now = performance.now();
      if (!st.playing || now - lastTrail > 80) {
        lastTrail = now;
        const runs = []; let cur = [];
        for (let q = 0; q <= k; q++) {
          const idx = fixIdx[q];
          if (q > 0 && idx - fixIdx[q - 1] > 1 && !hasFix(idx - 1)) { if (cur.length) runs.push(cur); cur = []; }
          cur.push([LAT[idx], LON[idx]]);
        }
        cur.push([lat, lon]); runs.push(cur);
        trail.setLatLngs(runs);
      }
      if (st.follow) {
        const p = map.latLngToContainerPoint([lat, lon]), s = map.getSize();
        if (p.x < s.x * 0.2 || p.x > s.x * 0.8 || p.y < s.y * 0.2 || p.y > s.y * 0.8) map.panTo([lat, lon], { animate: true, duration: 0.4 });
      }
      const g = (c) => (fr[c] && fr[c][i] != null ? fr[c][i] : null);
      const rp = (k2, v) => h('span', { class: 'rp' }, h('span', { class: 'k' }, k2), h('span', { class: 'v' }, v));
      readout.replaceChildren(
        h('span', { class: 'mode' }, g('flight_mode') || '—', g('is_flying') === false ? h('span', { class: 'dim', style: { fontWeight: 500 } }, ' · on ground') : null),
        rp('Alt', fmtAlt(g('alt_rel_m') ?? g('height_m'))),
        rp('Speed', fmtSpd(g('speed_h_ms'))),
        rp('V/S', g('vel_d') == null ? '—' : fmtVSpd(-g('vel_d'))),
        rp('Home', fmtDist(g('distance_m'))),
        rp('Hdg', yaw == null ? '—' : `${num(((yaw % 360) + 360) % 360, 0)}°`),
        rp('Batt', g('battery_pct') == null ? (g('battery_v') == null ? '—' : `${num(g('battery_v'), 2)} V`) : `${num(g('battery_pct'), 0)}%${g('battery_v') == null ? '' : ` ${num(g('battery_v'), 2)} V`}`),
        rp('GPS', g('gps_sats') == null ? '—' : `${g('gps_sats')} sats`),
        rp('Pos', hasFix(i) ? `${num(lat, 5)}, ${num(lon, 5)}` : `no fix (last ${num(lat, 5)}, ${num(lon, 5)})`));
    }
    placeHeads();
    const cur = markCurrentEvent();
    if (cur && scrollEvents && tab === 'events') cur.scrollIntoView({ block: 'nearest' });
  }
  function seek(ms, fromRange) {
    if (!hasFrames || !isNum(ms)) return;
    st.t = Math.max(tMin * 1000, Math.min(tMax * 1000, ms));
    update(!fromRange);
  }
  function loop(ts) {
    if (!st.playing) return;
    const dt = lastFrameTs ? Math.min(0.25, (ts - lastFrameTs) / 1000) : 0;
    lastFrameTs = ts;
    st.t += dt * 1000 * st.speed;
    if (st.t >= tMax * 1000) { st.t = tMax * 1000; togglePlay(false); }
    update(true);
    raf = requestAnimationFrame(loop);
  }
  function togglePlay(force) {
    if (!hasFrames) return;
    st.playing = force === undefined ? !st.playing : force;
    if (st.playing && st.t >= tMax * 1000) st.t = tMin * 1000;
    playBtn.replaceChildren(icon(st.playing ? 'pause' : 'play'));
    lastFrameTs = 0;
    if (st.playing) raf = requestAnimationFrame(loop); else cancelAnimationFrame(raf);
  }
  cleanup.push(() => cancelAnimationFrame(raf));
  update(false);

  function frameStep(dir) { const i = Math.max(0, Math.min(n - 1, st.idx + dir)); seek(T[i] * 1000); }

  return {
    destroy() { st.playing = false; for (const f of cleanup) { try { f(); } catch (e) { /* ignore */ } } el.style.overflow = ''; },
    onKey(e) {
      if (!hasFrames) return false;
      const big = e.shiftKey ? 10000 : 1000;
      switch (e.key) {
        case ' ': togglePlay(); return true;
        case 'ArrowRight': seek(st.t + big); return true;
        case 'ArrowLeft': seek(st.t - big); return true;
        case '.': frameStep(1); return true;
        case ',': frameStep(-1); return true;
        case 'Home': seek(tMin * 1000); return true;
        case 'End': seek(tMax * 1000); return true;
        case ']': { const i = SPEEDS.indexOf(st.speed); st.speed = SPEEDS[Math.min(SPEEDS.length - 1, i + 1)]; speedSel.value = st.speed; return true; }
        case '[': { const i = SPEEDS.indexOf(st.speed); st.speed = SPEEDS[Math.max(0, i - 1)]; speedSel.value = st.speed; return true; }
        case 'f': case 'F': if (st.toggleFollow) st.toggleFollow(); return true;
        default: return false;
      }
    },
    onPrefs(k) {
      if (k === 'units') { buildCharts(); if (hasTrack) drawTrack(false); update(false); if (tab === 'details') renderBody(); }
      if (k === 'theme') { buildCharts(); if (trail) trail.setStyle({ color: cssVar('--gold') || '#E0A43A' }); }
      if (k === 'tz') renderBody();
    },
  };
}

const CLASS_STYLE = { flight: ['b-ok', 'plane'], gps_movement: ['b-warn', 'map-pin'], motors_only: ['b-warn', 'gauge'], powered_on: ['b-outline', null],
  events_only: ['b-outline', 'file-text'], encrypted: ['b-info', 'lock'], empty: ['b-outline', null] };
export function classBadge(an, big) {
  if (!an || !an.classification) return null;
  const [cls, ic] = CLASS_STYLE[an.classification] || ['b-outline', null];
  return badge(an.is_real_flight ? 'Flight' : an.label, cls, ic);
}
