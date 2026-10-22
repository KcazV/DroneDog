// Report: pick flights + formats, export, show output folder and file hashes.
import { call, hasMethod } from '../bridge.js';
import { store, load, subscribe } from '../store.js';
import { h, clear, icon, toast, fmtUtc, fmtBytes, fmtDur, hashEl, sourceBadge, badge, num, copyText, noteBadges } from '../util.js';
import { emptyState } from '../table.js';

const FORMATS = [
  ['html', 'Exhibit report (HTML, printable)', 'Case header, evidence hashes and checks, artifacts, flights with limitations, links, custody verification.'],
  ['kml', 'KML tracks', 'For Google Earth and GIS tools. One placemark per flight.'],
  ['geojson', 'GeoJSON tracks', 'Tracks and home points for GIS tools.'],
  ['json', 'JSON bundle', 'Machine-readable: case, evidence, artifacts, flights with all frames and events.'],
];

export async function mount(el, _p, { go }) {
  const page = h('div', { class: 'page' });
  el.appendChild(page);
  page.appendChild(h('div', { class: 'page-head' }, h('h1', null, 'Report'),
    h('span', { class: 'sub' }, 'Exports are written to the case exports folder with a SHA-256 manifest and logged in the custody log.')));
  let flights = [];
  try { flights = await load('flights', true); } catch (e) { toast(e.message, 'bad'); }
  let artById = new Map();
  try { artById = new Map((await load('artifacts')).map((a) => [a.id, a])); } catch (e) { /* notes without the deleted flag */ }
  if (!flights.length) { page.appendChild(emptyState('file-text', 'Nothing to report yet', 'Import evidence first.', h('button', { class: 'btn primary', onclick: () => go('import') }, icon('upload'), 'Import evidence'))); return {}; }

  const sel = store.selectedFlights.size ? new Set(store.selectedFlights) : new Set(flights.map((f) => f.id));
  const fmts = new Set(['html', 'kml', 'geojson', 'json']);

  const flightList = h('div');
  function renderFlights() {
    clear(flightList);
    const all = flights.every((f) => sel.has(f.id));
    flightList.appendChild(h('label', { class: 'check', style: { padding: '8px 16px', borderBottom: '1px solid var(--border)', display: 'flex', fontWeight: 600 } },
      h('input', { type: 'checkbox', checked: all, onchange: (e) => { flights.forEach((f) => (e.target.checked ? sel.add(f.id) : sel.delete(f.id))); renderFlights(); renderAction(); } }), `All flights (${flights.length})`));
    for (const f of flights) {
      flightList.appendChild(h('label', { class: 'check', style: { padding: '8px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center' } },
        h('input', { type: 'checkbox', checked: sel.has(f.id), onchange: (e) => { e.target.checked ? sel.add(f.id) : sel.delete(f.id); renderFlights(); renderAction(); } }),
        h('span', { style: { flex: 1, minWidth: 0 } }, h('b', null, `#${f.id} ${f.aircraft_model || 'Unknown'}`), ' ', h('span', { class: 'mono dim', style: { fontSize: '12px' } }, f.aircraft_sn || ''),
          h('div', { class: 'dim', style: { fontSize: '12px' } }, `${fmtUtc(f.start_utc)} · ${fmtDur(f.duration_s)} · ${f.artifact_name || ''}`)),
        sourceBadge(f.source), noteBadges(f, artById.get(f.artifact_id))));
    }
  }
  const fmtList = h('div', { class: 'fmt-list' });
  function renderFmts() {
    clear(fmtList);
    for (const [k, t, d] of FORMATS) {
      fmtList.appendChild(h('label', { class: 'fmt' + (fmts.has(k) ? ' on' : '') },
        h('input', { type: 'checkbox', checked: fmts.has(k), style: { accentColor: 'var(--gold)', marginTop: '3px' }, onchange: (e) => { e.target.checked ? fmts.add(k) : fmts.delete(k); renderFmts(); renderAction(); } }),
        h('div', null, h('div', { class: 't' }, t), h('div', { class: 'd' }, d))));
    }
  }
  const exportBtn = h('button', { class: 'btn primary lg', onclick: doExport }, icon('download'), 'Export');
  const actionNote = h('span', { class: 'muted' });
  function renderAction() {
    exportBtn.disabled = !sel.size || !fmts.size || (store.exportJob && store.exportJob.state === 'running') || null;
    actionNote.textContent = `${sel.size} flight(s) · ${fmts.size} format(s)`;
  }
  const result = h('div', { style: { marginTop: '16px' } });

  page.appendChild(h('div', { class: 'grid2' },
    h('div', { class: 'card' }, h('div', { class: 'card-h' }, icon('plane'), h('h2', null, 'Flights')), flightList),
    h('div', null,
      h('div', { class: 'card' }, h('div', { class: 'card-h' }, icon('file-text'), h('h2', null, 'Formats')), h('div', { class: 'card-b' }, fmtList,
        h('div', { style: { display: 'flex', alignItems: 'center', gap: '12px', marginTop: '16px', justifyContent: 'flex-end' } }, actionNote, exportBtn))),
      result)));

  async function doExport() {
    try {
      store.exportJob = null;
      const res = await call('export_report', [...sel], [...fmts]);
      if (res && res.job_id) {
        store.exportJob = { job_id: res.job_id, state: 'running', progress: 0, message: 'Starting export', stages: [] };
        try { const j = await call('job_status', res.job_id); if (j) store.exportJob = j; } catch (e) { /* push will come */ }
      } else if (res && res.files) {
        store.exportJob = { state: 'done', progress: 1, result: res, message: 'Export complete' };
      }
      renderResult(); renderAction();
    } catch (e) { toast(e.message, 'bad', 8000); }
  }

  function renderResult() {
    clear(result);
    const j = store.exportJob;
    if (!j) return;
    if (j.state === 'running') {
      result.appendChild(h('div', { class: 'card card-b' }, h('div', { style: { display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '10px' } }, icon('loader-circle', 'spin'), h('b', null, j.message || 'Exporting…')),
        h('div', { class: 'progress' }, h('div', { style: { width: Math.round((j.progress || 0) * 100) + '%' } }))));
      return;
    }
    if (j.state === 'failed') { result.appendChild(h('div', { class: 'banner bad' }, icon('circle-x'), h('div', null, h('b', null, 'Export failed. '), j.error || ''))); return; }
    const r = j.result || {};
    result.appendChild(h('div', { class: 'card' },
      h('div', { class: 'card-h' }, icon('circle-check'), h('h2', null, 'Export complete'), h('span', { class: 'spacer' }),
        hasMethod('reveal_path') ? h('button', { class: 'btn sm', onclick: async () => { const x = await call('reveal_path', r.export_dir); if (x && x.ok === false) toast(x.error || 'Cannot open folder', 'warn'); } }, icon('folder-open'), 'Show in folder') : null),
      h('div', { class: 'card-b' },
        h('div', { class: 'muted', style: { fontSize: '12px', marginBottom: '4px' } }, 'Output folder'),
        h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '12px' } }, h('span', { class: 'mono', style: { fontSize: '12.5px', wordBreak: 'break-all' } }, r.export_dir || '—'),
          r.export_dir ? h('button', { class: 'btn sm ghost icon-only', title: 'Copy path', onclick: () => copyText(r.export_dir) }, icon('copy')) : null),
        r.mock ? h('div', { class: 'banner warn', style: { marginBottom: '12px' } }, icon('info'), h('div', null, 'Mock mode: no files were written. Paths and hashes are illustrative.')) : null,
        h('table', { class: 'dt' }, h('thead', null, h('tr', null, h('th', null, 'Format'), h('th', null, 'File'), h('th', { class: 'num' }, 'Size'), h('th', null, 'SHA-256'))),
          h('tbody', null, (r.files || []).map((f) => h('tr', null, h('td', null, badge(f.format === 'pdf' ? 'html' : f.format, 'b-outline')),
            h('td', { class: 'mono', style: { fontSize: '12px' } }, (f.path || '').split(/[\\/]/).pop()),
            h('td', { class: 'num' }, fmtBytes(f.size)), h('td', null, hashEl(f.sha256)))))))));
  }

  renderFlights(); renderFmts(); renderAction(); renderResult();
  const unsub = subscribe((w) => { if (w === 'job') { renderResult(); renderAction(); } });
  return { destroy: unsub };
}
