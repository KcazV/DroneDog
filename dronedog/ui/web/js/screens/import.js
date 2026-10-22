// Import: drop zone + pickers -> staged list -> import job with live stages, counts and artifact rows.
import { call } from '../bridge.js';
import { store, subscribe } from '../store.js';
import { h, clear, append, icon, toast, num, fmtBytes, statusBadge, badge } from '../util.js';
import { DataTable } from '../table.js';

const STAGE_ICONS = { hashing: 'fingerprint', extracting: 'package', detecting: 'scan-search', parsing: 'activity' };

export async function mount(el, _param, { go }) {
  const page = h('div', { class: 'page' });
  el.appendChild(page);
  page.appendChild(h('div', { class: 'page-head' }, h('h1', null, 'Import evidence'),
    h('span', { class: 'sub' }, 'Add the evidence items. DroneDog recognises each one itself (E01 / raw disk image, DAT or TXT flight log, zip, phone backup, folder or SD card) and finds every flight log inside, including deleted ones.')));

  // drop zone
  const dz = h('div', { class: 'dropzone' },
    h('div', { class: 'big' }, icon('upload')),
    h('div', null, h('div', { class: 't' }, 'Drop evidence here'),
      h('div', { class: 's' }, 'Pick one or many items of any type. They are staged first; nothing is read until you start the import.')),
    h('div', { class: 'actions' },
      pickBtn('file-plus', 'Add evidence item(s)', 'files', true),
      pickBtn('folder-open', 'Add folder / SD card', 'folder')));
  ['dragenter', 'dragover'].forEach((t) => dz.addEventListener(t, () => dz.classList.add('over')));
  ['dragleave', 'drop'].forEach((t) => dz.addEventListener(t, () => dz.classList.remove('over')));
  page.appendChild(dz);

  function pickBtn(ic, label, kind, primary) {
    return h('button', { class: primary ? 'btn primary lg' : 'btn', onclick: async () => {
      try {
        const paths = await call('pick_paths', kind);
        const add = (paths || []).filter((p) => !store.staged.includes(p));
        store.staged.push(...add);
        renderStaged();
      } catch (e) { toast(e.message, 'bad'); }
    } }, icon(ic), label);
  }

  // staged
  const stagedCard = h('div', { class: 'card staged' });
  page.appendChild(stagedCard);
  function renderStaged() {
    clear(stagedCard);
    const n = store.staged.length;
    stagedCard.hidden = n === 0;
    if (!n) return;
    const running = store.importJob && store.importJob.state === 'running';
    append(stagedCard, [
      h('div', { class: 'card-h' }, icon('list'), h('h2', null, `Staged for import (${n})`), h('span', { class: 'spacer' }),
        h('button', { class: 'btn sm ghost', onclick: () => { store.staged = []; renderStaged(); } }, 'Clear'),
        h('label', { class: 'check', style: { fontSize: '12.5px', color: 'var(--text-2)', marginRight: '10px' }, title: 'Testing only. Evidence is read in place, not copied, hashed or verified. Recorded in the custody log and shown as Not hashed. Extracted flight logs are still hashed.' },
          h('input', { type: 'checkbox', checked: store.skipHash || null, onchange: (e) => { store.skipHash = e.target.checked; } }), 'Skip evidence hashing (testing)'),
        h('button', { class: 'btn primary', disabled: running || null, onclick: startImport }, icon('play'), `Import ${n} item${n > 1 ? 's' : ''}`)),
      h('ul', { class: 'staged-list' }, store.staged.map((p, i) => h('li', null,
        icon(kindOf(p)[1]), h('span', { class: 'badge b-outline', style: { minWidth: '118px', justifyContent: 'center' } }, kindOf(p)[0]),
        h('span', { class: 'p', title: p }, p),
        h('button', { class: 'btn sm ghost icon-only', title: 'Remove', onclick: () => { store.staged.splice(i, 1); renderStaged(); } }, icon('x')))))]);
  }

  async function startImport() {
    const paths = store.staged.slice();
    try {
      const res = await call('import_paths', paths, { skip_hash: !!store.skipHash });
      store.staged = [];
      renderStaged();
      if (res && res.job_id && (!store.importJob || store.importJob.job_id !== res.job_id)) {
        try { store.importJob = await call('job_status', res.job_id); } catch (e) { /* progress push will arrive */ }
      }
      renderJob();
    } catch (e) { toast(e.message, 'bad', 8000); }
  }

  // job panel
  const jobCard = h('div', { class: 'card', style: { marginTop: '16px' } });
  page.appendChild(jobCard);
  const table = new DataTable([
    { key: 'name', label: 'Artifact', render: (a) => h('span', null, h('span', { style: { fontWeight: 600 } }, a.name), a.duplicate ? h('span', { class: 'sub' }, 'already in case') : null) },
    { key: 'inner_path', label: 'Source / inner path', cls: 'trunc', render: (a) => h('span', { title: a.inner_path || '' },
      h('span', { class: 'sub', style: { display: 'block' } }, a.evidence_label || ''), h('span', { class: 'mono', style: { fontSize: '12px' } }, a.inner_path || a.rel_path || '')) },
    { key: 'type', label: 'Type', sort: (a) => a.detected_subtype || a.detected_kind || '', render: (a) => a.detected_kind
      ? h('span', { class: 'mono', style: { fontSize: '12px' } }, a.detected_subtype || a.detected_kind) : h('span', { class: 'dim' }, 'detecting…') },
    { key: 'size', label: 'Size', num: true, render: (a) => fmtBytes(a.size) },
    { key: 'status', label: 'Status', render: (a) => statusBadge(a.status) },
    { key: 'deleted', label: 'Deleted', sort: (a) => a.deleted_sources || 0, render: (a) => a.deleted
      ? badge('Deleted', 'b-del', 'trash-2') : a.deleted_sources ? badge(`${a.deleted_sources} deleted copy`, 'b-del') : null },
  ], { empty: 'Artifacts appear here as they are found.', rowKey: (a) => a.id ?? a.sha256 ?? a.name });

  let lastJobId = null;
  function renderJob() {
    const job = store.importJob;
    clear(jobCard);
    jobCard.hidden = !job;
    if (!job) return;
    const st = job.state;
    const pct = Math.round((job.progress || 0) * 100);
    const counts = job.counts || {};
    append(jobCard, [
      h('div', { class: 'card-h' },
        st === 'running' ? icon('loader-circle', 'spin') : st === 'done' ? icon('circle-check') : icon('circle-x'),
        h('h2', null, st === 'running' ? 'Import running' : st === 'done' ? 'Import complete' : 'Import failed'),
        h('span', { class: 'dim mono', style: { fontSize: '12px' } }, job.job_id || ''),
        h('span', { class: 'spacer' }),
        st === 'done' ? h('button', { class: 'btn', onclick: () => go('artifacts') }, icon('files'), 'Artifacts') : null,
        st === 'done' ? h('button', { class: 'btn primary', onclick: () => go('flights') }, icon('plane'), 'View flights') : null),
      h('div', { class: 'card-b' },
        h('div', { class: 'stepper' }, (job.stages || []).map((s) => h('div', { class: 'step ' + (s.state || '') },
          h('div', { class: 'dot' }, s.state === 'done' ? icon('check') : s.state === 'running' ? icon(STAGE_ICONS[s.key] || 'activity') : icon(STAGE_ICONS[s.key] || 'activity')),
          h('div', null, h('div', { class: 'n' }, s.label), h('div', { class: 'c' }, stageCount(s)))))),
        h('div', { style: { marginTop: '14px' } }, h('div', { class: 'progress ' + (st === 'done' ? 'done' : st === 'failed' ? 'failed' : '') }, h('div', { style: { width: pct + '%' } }))),
        h('div', { class: 'job-msg' }, h('b', { style: { fontVariantNumeric: 'tabular-nums' } }, pct + '%'),
          h('span', null, job.error || job.message || ''), h('span', { class: 'path' }, job.current_path || '')),
        h('div', { class: 'tiles', style: { marginTop: '14px' } },
          tile('Evidence', counts.evidence), tile('Artifacts', counts.artifacts), tile('Parsed', counts.parsed, 'ok'),
          tile('Encrypted', counts.encrypted, 'enc'), tile('Unsupported', counts.unsupported), tile('Failed', counts.failed, 'bad'),
          tile('Deleted found', counts.deleted, 'del'), tile('Flights', counts.flights, 'acc'))),
      h('div', { style: { padding: '0 16px 16px' } }, table.el),
      (job.log && job.log.length) ? h('details', { style: { borderTop: '1px solid var(--border)' }, open: st !== 'running' || null },
        h('summary', { style: { padding: '10px 16px', cursor: 'pointer', color: 'var(--text-2)', fontWeight: 600, fontSize: '13px' } }, `Job log (${job.log.length})`),
        h('div', { class: 'joblog' }, job.log.map((l) => h('div', { class: l.level }, (l.ts_utc ? l.ts_utc.slice(11, 19) + '  ' : '') + l.message)))) : null]);
    table.setRows(job.artifacts || [], { flashNew: lastJobId === job.job_id });
    lastJobId = job.job_id;
  }

  renderStaged();
  renderJob();
  const unsub = subscribe((what) => { if (what === 'job') renderJob(); });
  return { destroy: unsub, refresh: renderStaged };
}

function stageCount(s) {
  const bytes = s.key === 'hashing' && s.total > 100000;
  const f = (v) => (bytes ? fmtBytes(v) : num(v));
  if (s.total && s.total >= (s.done || 0)) return `${f(s.done || 0)} / ${f(s.total)}`;
  if (s.done) return f(s.done);
  return s.state === 'done' ? 'done' : '—';
}
function tile(k, v, cls) { return h('div', { class: 'tile ' + (cls || '') }, h('div', { class: 'v' }, num(v ?? 0)), h('div', { class: 'k' }, k)); }

// what a staged path looks like from its name; the backend re-checks every item by content
function kindOf(p) {
  if (/\.(e01|ex01)$/i.test(p)) return ['Disk image (E01)', 'disc'];
  if (/\.(001|dd|img|raw|bin|dmg)$/i.test(p)) return ['Disk image (raw)', 'disc'];
  if (/\.dat$/i.test(p)) return ['Flight log (DAT)', 'file'];
  if (/\.txt$/i.test(p)) return ['App log (TXT)', 'file'];
  if (/\.zip$/i.test(p)) return ['Zip / extraction', 'package'];
  if (/\.[a-z0-9]{2,5}$/i.test(p)) return ['File', 'file'];
  return ['Folder', 'folder'];
}
