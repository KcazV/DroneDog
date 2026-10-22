// Flights: sortable/filterable table, multi-select, overlay map of selected tracks.
import { call } from '../bridge.js';
import { store, load } from '../store.js';
import { h, clear, icon, toast, fmtUtc, fmtLocal, fmtDur, fmtDist, fmtAlt, fmtSpd, num, sourceBadge, badge, FLIGHT_COLORS, esc, linkKind, visibleLinks, noteBadges } from '../util.js';
import { DataTable, emptyState } from '../table.js';
import { createMap, divMarker } from '../map.js';
import { classBadge } from './flight.js';

const L = window.L;

export async function mount(el, _p, { go }) {
  const page = h('div', { class: 'page full' });
  el.appendChild(page);
  let rows;
  try { rows = await load('flights', true); } catch (e) { toast(e.message, 'bad'); rows = []; }
  let artById = new Map();
  try { artById = new Map((await load('artifacts')).map((a) => [a.id, a])); } catch (e) { /* notes without the deleted flag */ }
  const hasGps = (r) => (r.gps_points || 0) > 0 || !!r.bbox;
  const nGps = rows.filter(hasGps).length;
  const nFlight = rows.filter((r) => r.is_real_flight).length;
  page.appendChild(h('div', { class: 'page-head' }, h('h1', null, 'Flights'),
    h('span', { class: 'sub' }, `${rows.length} record${rows.length === 1 ? '' : 's'}: ${nGps} with a GPS track, ${nFlight} with a recorded flight. `
      + 'DJI aircraft write a log at every power-on. Select rows to overlay tracks; click one to open it.')));
  if (!rows.length) { page.appendChild(emptyState('plane', 'No flights yet', 'Import evidence; parsed DAT and TXT logs appear here.', h('button', { class: 'btn primary', onclick: () => go('import') }, icon('upload'), 'Import evidence'))); return {}; }

  const colorOf = new Map(rows.map((r, i) => [r.id, FLIGHT_COLORS[i % FLIGHT_COLORS.length]]));
  const sel = store.selectedFlights;
  const filt = { q: '', source: 'all', model: 'all', gps: nGps > 0 && nGps < rows.length ? 'gps' : 'all' };
  const gpsMatch = (r) => filt.gps === 'all' || (filt.gps === 'gps') === hasGps(r);

  const q = h('input', { class: 'input sm', placeholder: 'Search model, serial, battery, place, file', oninput: () => { filt.q = q.value.toLowerCase(); apply(); } });
  const models = [...new Set(rows.map((r) => r.aircraft_model).filter(Boolean))].sort();
  const modelSel = h('select', { class: 'input sm', onchange: () => { filt.model = modelSel.value; apply(); } },
    h('option', { value: 'all' }, 'All models'), models.map((m) => h('option', { value: m }, m)));
  const srcSel = h('select', { class: 'input sm', onchange: () => { filt.source = srcSel.value; apply(); } },
    h('option', { value: 'all' }, 'All sources'), h('option', { value: 'aircraft-dat' }, 'Aircraft DAT'),
    h('option', { value: 'controller-dat' }, 'Controller DAT'), h('option', { value: 'app-txt' }, 'App TXT'));
  const gpsSel = h('select', { class: 'input sm', title: 'Records with or without GPS position fixes',
    onchange: () => { filt.gps = gpsSel.value; apply(); drawTracks(); } },
    h('option', { value: 'gps' }, `With GPS track (${nGps})`), h('option', { value: 'nogps' }, `Without GPS track (${rows.length - nGps})`),
    h('option', { value: 'all' }, `All records (${rows.length})`));
  gpsSel.value = filt.gps;
  const selInfo = h('span', { class: 'muted', style: { fontSize: '13px' } });
  const reportBtn = h('button', { class: 'btn', onclick: () => go('report') }, icon('file-text'), 'Report on selected');
  page.appendChild(h('div', { class: 'page-head', style: { marginTop: '-6px' } },
    h('div', { class: 'search' }, icon('search'), q), gpsSel, modelSel, srcSel, h('span', { class: 'spacer' }), selInfo, reportBtn));

  const table = new DataTable([
    { key: 'color', label: '', sort: false, width: '10px', render: (r) => h('span', { style: { display: 'inline-block', width: '10px', height: '10px', borderRadius: '3px', background: colorOf.get(r.id) } }) },
    { key: 'id', label: '#', num: true, width: '40px' },
    { key: 'classification', label: 'Status', sort: (r) => (r.is_real_flight ? 0 : 1) + (r.classification || ''), render: (r) => classBadge({ classification: r.classification, is_real_flight: r.is_real_flight, label: r.class_label }) },
    { key: 'start_utc', label: 'Start / takeoff (UTC)', render: (r) => h('span', { class: 'nowrap' }, h('span', { class: 'mono', style: { fontSize: '12.5px' } }, fmtUtc(r.takeoff_utc || r.start_utc)),
      h('span', { class: 'sub mono', style: { fontSize: '11.5px' } }, r.takeoff_utc ? 'takeoff · ' + fmtLocal(r.takeoff_utc) : fmtLocal(r.start_utc))) },
    { key: 'location_label', label: 'Place', render: (r) => r.location_label || r.app_location ? h('span', { title: r.location_summary || r.app_location },
      h('span', null, r.location_label || r.app_location), r.location_summary ? h('span', { class: 'sub' }, r.location_summary.split(' of ')[0] + ' of') : null) : h('span', { class: 'dim' }, '—') },
    { key: 'aircraft_model', label: 'Aircraft', render: (r) => h('span', null, h('span', { style: { fontWeight: 600 } }, r.aircraft_model || 'Unknown'),
      h('span', { class: 'sub mono', style: { fontSize: '11.5px' } }, r.aircraft_sn || (r.fc_ids && r.fc_ids[0] ? `FC ${r.fc_ids[0]}` : 'SN not recorded')),
      (r.battery_serials || []).length ? h('span', { class: 'sub mono', style: { fontSize: '11px' } }, 'Batt ' + r.battery_serials.slice(0, 2).join(', ')) : null) },
    { key: 'source', label: 'Source', render: (r) => h('span', null, sourceBadge(r.source), h('span', { class: 'sub', title: r.artifact_name }, r.artifact_name || '')) },
    { key: 'duration_s', label: 'Duration', num: true, render: (r) => fmtDur(r.duration_s) },
    { key: 'distance_m', label: 'Distance', num: true, render: (r) => fmtDist(r.distance_m) },
    { key: 'max_alt_m', label: 'Max alt', num: true, render: (r) => fmtAlt(r.max_alt_m) },
    { key: 'max_speed_ms', label: 'Max speed', num: true, render: (r) => fmtSpd(r.max_speed_ms) },
    { key: 'warnings', label: 'Note', sort: (r) => (r.warnings || []).length + visibleLinks(r.links).length * 10 + noteBadges(r, artById.get(r.artifact_id)).length * 100, render: (r) => notes(r, artById.get(r.artifact_id)) },
  ], { selectable: true, selected: sel, sortKey: 'start_utc', rowKey: (r) => r.id,
    onRowClick: (r) => go(`flight/${r.id}`), onSelect: () => { renderSel(); drawTracks(); } });

  const layout = h('div', { class: 'flights-layout' });
  const mapBox = h('div', { class: 'map-card' });
  const legend = h('div', { class: 'legend tr' });
  layout.append(table.el, mapBox);
  page.appendChild(layout);

  function apply() {
    table.setRows(rows.filter((r) => gpsMatch(r) && (filt.source === 'all' || r.source === filt.source) && (filt.model === 'all' || r.aircraft_model === filt.model)
      && (!filt.q || [r.aircraft_model, r.aircraft_sn, r.artifact_name, r.evidence_label, r.location_label, ...(r.battery_serials || []), ...(r.fc_ids || []), ...(r.uuids || [])].join(' ').toLowerCase().includes(filt.q))));
  }
  function renderSel() {
    selInfo.textContent = sel.size ? `${sel.size} selected` : 'None selected: map shows all';
    reportBtn.disabled = !sel.size || null;
  }

  // map
  const mapEl = h('div', { style: { position: 'absolute', inset: 0 } });
  mapBox.append(mapEl, legend);
  const map = createMap(mapEl);
  const group = L.featureGroup().addTo(map);
  let geo = null;
  try { geo = await call('get_track_geojson', rows.map((r) => r.id)); } catch (e) { toast(e.message, 'bad'); }

  function drawTracks(fit = true) {
    group.clearLayers(); clear(legend);
    const show = sel.size ? rows.filter((r) => sel.has(r.id)) : rows.filter(gpsMatch);
    const ids = new Set(show.map((r) => r.id));
    legend.appendChild(h('div', { style: { fontWeight: 650, marginBottom: '4px' } }, sel.size ? 'Selected flights' : 'All flights'));
    for (const f of (geo && geo.features) || []) {
      const fid = f.properties.flight_id;
      if (!ids.has(fid)) continue;
      const color = colorOf.get(fid);
      if (f.geometry.type === 'LineString') {
        const ll = f.geometry.coordinates.map((c) => [c[1], c[0]]);
        L.polyline(ll, { color: '#0B0D12', weight: 5.5, opacity: 0.55, interactive: false }).addTo(group);
        L.polyline(ll, { color, weight: 3, opacity: 0.95 })
          .bindTooltip(`#${fid} ${esc(f.properties.aircraft_model || '')} · ${esc(f.properties.source || '')}`, { sticky: true })
          .on('click', () => go(`flight/${fid}`)).addTo(group);
      } else if (f.properties.role === 'home') {
        divMarker([f.geometry.coordinates[1], f.geometry.coordinates[0]], 'mk-home', 'house', 22, `Home point #${fid}`).addTo(group);
      }
    }
    for (const r of show) {
      legend.appendChild(h('div', { class: 'row', onclick: () => go(`flight/${r.id}`) }, h('span', { class: 'sw', style: { background: colorOf.get(r.id) } }),
        `#${r.id} ${r.aircraft_model || ''}`, r.encrypted_unreadable ? h('span', { class: 'dim' }, ' (no track)') : null));
    }
    if (fit && group.getLayers().length) { const bb = group.getBounds(); map.fitBounds(bb, { padding: [40, 40], maxZoom: 16 }); setTimeout(() => { map.invalidateSize(); map.fitBounds(bb, { padding: [40, 40], maxZoom: 16, animate: false }); }, 60); }
  }
  renderSel(); apply(); drawTracks();
  return { destroy: () => map.ddDestroy() };
}

function notes(r, art) {
  const w = r.warnings || [];
  const out = noteBadges(r, art);
  const links = visibleLinks(r.links);
  const byKind = new Map();
  for (const l of links) { const k = linkKind(l.kind); if (!byKind.has(l.kind)) byKind.set(l.kind, { k, ids: [] }); byKind.get(l.kind).ids.push(l.flight_id); }
  for (const [kind, { k, ids }] of byKind) {
    const title = links.filter((l) => l.kind === kind).map((l) => `${k.label(l.flight_id)} (score ${l.score ?? '?'})\n${(l.reasons || []).join('\n')}`).join('\n\n');
    out.push(h('span', { title }, badge(k.label(ids[0]) + (ids.length > 1 ? ` +${ids.length - 1}` : ''), k.cls, 'link')));
  }
  if (w.length) out.push(h('span', { title: w.join('\n') }, badge(`${w.length} warning${w.length > 1 ? 's' : ''}`, w.some((x) => /forced|critical|crash/i.test(x)) ? 'b-bad' : 'b-warn', 'triangle-alert')));
  return out.length ? h('span', { style: { display: 'inline-flex', gap: '4px', flexWrap: 'wrap' } }, out) : null;
}
