// Artifacts: every flight-log candidate found, with detection reasons, status and provenance.
import { store, load } from '../store.js';
import { h, clear, icon, toast, fmtBytes, fmtHex, fmtUtc, hashEl, badge, statusBadge, num } from '../util.js';
import { DataTable, emptyState } from '../table.js';

export async function mount(el, _p, { go }) {
  const page = h('div', { class: 'page' });
  el.appendChild(page);
  page.appendChild(h('div', { class: 'page-head' }, h('h1', null, 'Artifacts'),
    h('span', { class: 'sub' }, 'Flight-log files found in the evidence. Click a row for detection reasons and every place the file was found.')));
  let rows;
  try { rows = await load('artifacts', true); } catch (e) { toast(e.message, 'bad'); rows = []; }
  if (!rows.length) { page.appendChild(emptyState('files', 'No artifacts yet', 'Import evidence to find DAT and TXT flight logs.', h('button', { class: 'btn primary', onclick: () => go('import') }, icon('upload'), 'Import evidence'))); return {}; }

  const filt = { status: 'all', deleted: false, q: '' };
  const counts = rows.reduce((m, a) => (m[a.status] = (m[a.status] || 0) + 1, m), {});
  const chips = h('div', { class: 'chips' });
  function renderChips() {
    clear(chips);
    for (const [k, label] of [['all', 'All'], ['parsed', 'Parsed'], ['encrypted', 'Encrypted'], ['unsupported', 'Unsupported'], ['failed', 'Failed']]) {
      const n = k === 'all' ? rows.length : counts[k] || 0;
      if (k !== 'all' && !n) continue;
      chips.appendChild(h('button', { class: 'chip' + (filt.status === k ? ' on' : ''), onclick: () => { filt.status = k; renderChips(); apply(); } }, label, h('span', { class: 'n' }, num(n))));
    }
    const nd = rows.filter((a) => a.deleted_sources).length;
    if (nd) chips.appendChild(h('button', { class: 'chip' + (filt.deleted ? ' on' : ''), onclick: () => { filt.deleted = !filt.deleted; renderChips(); apply(); } }, icon('trash-2'), 'Deleted', h('span', { class: 'n' }, num(nd))));
  }
  const q = h('input', { class: 'input sm', placeholder: 'Search name, path, hash', oninput: () => { filt.q = q.value.toLowerCase(); apply(); } });
  page.appendChild(h('div', { class: 'page-head', style: { marginTop: '-4px' } }, chips, h('span', { class: 'spacer' }), h('div', { class: 'search' }, icon('search'), q)));

  const table = new DataTable([
    { key: 'id', label: '#', num: true, width: '44px' },
    { key: 'name', label: 'Name', render: (a) => h('span', { style: { fontWeight: 600 } }, a.name) },
    { key: 'evidence_label', label: 'Evidence / inner path', cls: 'trunc', render: (a) => h('span', { title: a.inner_path || '' }, a.evidence_label || '—',
      h('span', { class: 'sub mono', style: { fontSize: '11.5px' } }, a.inner_path || '')) },
    { key: 'offset', label: 'Offset', num: true, render: (a) => a.offset != null ? h('span', { class: 'mono', style: { fontSize: '12px' } }, fmtHex(a.offset)) : null },
    { key: 'size', label: 'Size', num: true, render: (a) => fmtBytes(a.size) },
    { key: 'type', label: 'Detected', sort: (a) => a.detected_subtype || a.detected_kind, render: (a) => h('span', null,
      h('span', { class: 'mono', style: { fontSize: '12px' } }, a.detected_subtype || a.detected_kind || '—'),
      a.confidence != null ? h('span', { class: 'sub' }, `confidence ${Math.round(a.confidence * 100)}%`) : null) },
    { key: 'status', label: 'Status', render: (a) => statusBadge(a.status) },
    { key: 'deleted', label: 'Deleted', sort: (a) => a.deleted_sources || 0, render: (a) => a.deleted ? badge('Deleted', 'b-del', 'trash-2') : a.deleted_sources ? badge(`${a.deleted_sources} deleted copy`, 'b-del') : null },
    { key: 'sha256', label: 'SHA-256', render: (a) => hashEl(a.sha256) },
    { key: 'flights', label: 'Flights', sort: (a) => (a.flight_ids || []).length, render: (a) => (a.flight_ids || []).length
      ? h('span', null, a.flight_ids.map((id) => h('a', { href: `#/flight/${id}`, style: { marginRight: '6px' } }, `#${id}`))) : null },
  ], { sortKey: 'id', expand: detail, empty: 'No artifacts match the filter.' });
  page.appendChild(table.el);

  function apply() {
    table.setRows(rows.filter((a) => (filt.status === 'all' || a.status === filt.status) && (!filt.deleted || a.deleted_sources)
      && (!filt.q || [a.name, a.inner_path, a.sha256, a.evidence_label, a.detected_subtype].join(' ').toLowerCase().includes(filt.q))));
  }
  renderChips(); apply();
  return {};
}

function detail(a) {
  return h('div', { class: 'detail-grid' },
    h('div', null,
      h('h3', null, 'Detection'),
      h('ul', { class: 'reasons' }, (a.reasons || []).map((r) => h('li', null, r))),
      a.error ? h('div', { class: 'banner ' + (a.status === 'encrypted' ? '' : a.status === 'failed' ? 'bad' : 'warn'), style: { marginTop: '10px' } },
        icon(a.status === 'encrypted' ? 'lock' : 'triangle-alert'), h('div', null, a.error)) : null,
      h('h3', { style: { marginTop: '14px' } }, 'Hashes'),
      h('dl', { class: 'kv' }, h('dt', null, 'SHA-256'), h('dd', null, hashEl(a.sha256, { full: true })),
        h('dt', null, 'SHA-1'), h('dd', null, hashEl(a.sha1, { full: true })),
        h('dt', null, 'Working copy'), h('dd', { class: 'mono', style: { fontSize: '12px' } }, a.rel_path || '—'))),
    h('div', null,
      h('h3', null, `Found in ${(a.sources || []).length} location(s)`),
      h('table', { class: 'dt', style: { border: '1px solid var(--border)', borderRadius: '6px' } },
        h('thead', null, h('tr', null, ['Evidence', 'Inner path', 'Container', 'Offset', 'FS', 'State', 'Modified (UTC)'].map((t) => h('th', null, t)))),
        h('tbody', null, (a.sources || []).map((s) => h('tr', null,
          h('td', null, s.evidence_label || s.evidence_id),
          h('td', { class: 'mono', style: { fontSize: '11.5px', wordBreak: 'break-all' } }, s.inner_path || '—'),
          h('td', null, s.container || '—'),
          h('td', { class: 'mono', style: { fontSize: '12px' } }, s.offset != null ? fmtHex(s.offset) : '—'),
          h('td', null, s.fs_type || '—'),
          h('td', null, s.deleted ? badge('Deleted', 'b-del', 'trash-2') : badge('Allocated', 'b-outline'),
            s.overwrite_status && s.deleted ? h('span', { class: 'sub' }, `overwrite: ${s.overwrite_status}`) : null),
          h('td', { class: 'mono', style: { fontSize: '12px' } }, s.mtime_utc ? fmtUtc(s.mtime_utc) : '—')))))));
}
