// Evidence: every intake item with hashes, storage mode and verification.
import { store, load } from '../store.js';
import { h, icon, toast, fmtBytes, fmtUtc, hashEl, badge, num } from '../util.js';
import { DataTable, emptyState } from '../table.js';

const KIND = { file: ['File', 'b-outline'], folder: ['Folder', 'b-outline'], zip: ['Zip', 'b-info'], image: ['Disk image', 'b-acc'], sd: ['SD card', 'b-acc'] };

export async function mount(el, _p, { go }) {
  const page = h('div', { class: 'page' });
  el.appendChild(page);
  page.appendChild(h('div', { class: 'page-head' }, h('h1', null, 'Evidence'),
    h('span', { class: 'sub' }, 'Intake items as received. Hashes are computed on intake; originals are never modified.'),
    h('span', { class: 'spacer' }), h('button', { class: 'btn', onclick: () => go('import') }, icon('upload'), 'Add evidence')));
  const table = new DataTable([
    { key: 'id', label: '#', num: true, width: '44px' },
    { key: 'label', label: 'Item', render: (e) => h('span', null, h('span', { style: { fontWeight: 600 } }, e.label || '—'),
      h('span', { class: 'sub mono', style: { fontSize: '11.5px' }, title: e.src_path }, e.src_path)) },
    { key: 'kind', label: 'Kind', render: (e) => { const [t, c] = KIND[e.kind] || [e.kind, 'b-outline']; return h('span', null, badge(t, c), e.subkind ? h('span', { class: 'sub mono' }, e.subkind) : null); } },
    { key: 'size', label: 'Size', num: true, render: (e) => fmtBytes(e.size) },
    { key: 'sha256', label: 'SHA-256', render: (e) => e.hash_scope === 'not-hashed' ? badge('Not hashed', 'b-warn', 'triangle-alert') : hashEl(e.sha256) },
    { key: 'storage', label: 'Storage', render: (e) => h('span', null, e.storage || '—', e.hash_scope ? h('span', { class: 'sub' }, `hash: ${e.hash_scope}`) : null) },
    { key: 'verified', label: 'Verified', render: (e) => e.hash_scope === 'not-hashed' ? h('span', { class: 'dim' }, 'Not verified') : e.verified === true ? badge('Match', 'b-ok', 'circle-check') : e.verified === false ? badge('Mismatch', 'b-bad', 'circle-x') : h('span', { class: 'dim' }, 'n/a') },
    { key: 'artifact_count', label: 'Artifacts', num: true, render: (e) => num(e.artifact_count) },
    { key: 'added_utc', label: 'Added (UTC)', render: (e) => h('span', { class: 'mono', style: { fontSize: '12px' } }, fmtUtc(e.added_utc)) },
  ], { sortKey: 'id', expand: (e) => detail(e), empty: 'No evidence yet.' });
  try {
    const rows = await load('evidence', true);
    if (!rows.length) { page.appendChild(emptyState('hard-drive', 'No evidence in this case yet', 'Add files, folders or disk images to begin.', h('button', { class: 'btn primary', onclick: () => go('import') }, icon('upload'), 'Import evidence'))); return {}; }
    table.setRows(rows);
    page.appendChild(table.el);
  } catch (e) { toast(e.message, 'bad'); }
  return {};
}

function detail(e) {
  return h('div', { class: 'detail-grid' },
    h('div', null, h('h3', null, 'Hashes'), h('dl', { class: 'kv' },
      h('dt', null, 'SHA-256'), h('dd', null, hashEl(e.sha256, { full: true })),
      h('dt', null, 'SHA-1'), h('dd', null, hashEl(e.sha1, { full: true })),
      h('dt', null, 'MD5'), h('dd', null, hashEl(e.md5, { full: true })),
      h('dt', null, 'Scope'), h('dd', null, e.hash_scope || '—'))),
    h('div', null, h('h3', null, 'Intake'), h('dl', { class: 'kv' },
      h('dt', null, 'Source path'), h('dd', { class: 'mono' }, e.src_path),
      e.stored_path ? [h('dt', null, 'Stored'), h('dd', { class: 'mono' }, e.stored_path)] : null,
      h('dt', null, 'Examiner'), h('dd', null, e.examiner || '—'),
      h('dt', null, 'Added'), h('dd', { class: 'mono' }, fmtUtc(e.added_utc)),
      (e.verifications || []).length ? [h('dt', null, 'Verification'), h('dd', null, e.verifications.map((v) => h('div', { class: 'mono', style: { fontSize: '12px' } }, `${v.kind || v.type || 'check'}: ${v.result}`)))] : null)));
}
