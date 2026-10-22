// Custody: hash-chained methodology log with chain verification.
import { call } from '../bridge.js';
import { store, refreshCase } from '../store.js';
import { h, clear, icon, toast, fmtUtc, hashEl, badge, num } from '../util.js';
import { DataTable } from '../table.js';

const ACTION_CLS = { 'case.create': 'b-acc', 'case.open': 'b-outline', 'evidence.add': 'b-info', 'evidence.verify': 'b-ok', 'artifact.extract': 'b-outline',
  'artifact.detect': 'b-outline', 'artifact.parse': 'b-outline', 'analysis.run': 'b-enc', 'report.export': 'b-acc', 'job.start': 'b-muted', 'job.end': 'b-muted',
  'network.online_on': 'b-bad', 'network.online_off': 'b-ok' };

export async function mount(el) {
  const page = h('div', { class: 'page' });
  el.appendChild(page);
  page.appendChild(h('div', { class: 'page-head' }, h('h1', null, 'Chain of custody'),
    h('span', { class: 'sub' }, 'Append-only methodology log. Each entry carries the SHA-256 of the previous line, so any edit, deletion or reordering breaks the chain.')));

  const status = h('div', { class: 'card card-b', style: { marginBottom: '16px' } });
  page.appendChild(status);
  const verifyBtn = h('button', { class: 'btn primary lg', onclick: verify }, icon('shield-check'), 'Verify chain');
  function renderStatus(res, busy) {
    clear(status);
    let body;
    if (busy) body = h('div', { class: 'banner', style: { borderColor: 'var(--border-strong)', background: 'transparent' } }, icon('loader-circle', 'spin'), h('div', null, 'Verifying every entry…'));
    else if (!res) body = h('div', { class: 'muted' }, 'Recompute every entry hash and link to confirm the log has not been altered since it was written.');
    else if (res.ok) body = h('div', { class: 'banner ok' }, icon('circle-check'), h('div', null, h('b', null, 'Chain intact. '),
      `${num(res.entries)} entries verified; head seq ${res.head_seq ?? '—'}.`, h('div', { style: { marginTop: '4px' } }, h('span', { class: 'dim' }, 'Head hash '), hashEl(res.head_hash, { full: true }))));
    else body = h('div', { class: 'banner bad' }, icon('circle-x'), h('div', null, h('b', null, 'Chain BROKEN. '),
      `${res.reason || 'verification failed'}`, res.first_bad_line ? ` (first bad line ${res.first_bad_line}${res.first_bad_seq ? `, seq ${res.first_bad_seq}` : ''})` : '',
      h('div', { class: 'dim', style: { marginTop: '4px' } }, `${num(res.entries)} entries verified before the break.`)));
    status.append(h('div', { class: 'custody-status' }, h('div', { style: { flex: 1 } }, body), verifyBtn));
  }
  async function verify() {
    verifyBtn.disabled = true; renderStatus(null, true);
    try {
      const [res] = await Promise.all([call('verify_custody'), new Promise((r) => setTimeout(r, 350))]);
      renderStatus(res);
      toast(res.ok ? 'Custody chain verified' : 'Custody chain verification FAILED', res.ok ? 'ok' : 'bad');
      refreshCase().catch(() => {});
    } catch (e) { renderStatus(null); toast(e.message, 'bad'); } finally { verifyBtn.disabled = false; }
  }
  renderStatus(null);

  const table = new DataTable([
    { key: 'seq', label: 'Seq', num: true, width: '56px' },
    { key: 'ts_utc', label: 'Time (UTC)', render: (e) => h('span', { class: 'mono nowrap', style: { fontSize: '12px' } }, fmtUtc(e.ts_utc, true)) },
    { key: 'actor', label: 'Actor' },
    { key: 'action', label: 'Action', render: (e) => { const a = e.action === 'note' && e.params && e.params.event ? e.params.event : e.action; return badge(a, ACTION_CLS[a] || 'b-outline'); } },
    { key: 'component', label: 'Component', render: (e) => h('span', { class: 'mono', style: { fontSize: '12px' } }, e.component || '—') },
    { key: 'summary', label: 'Inputs / outputs', sort: false, cls: 'trunc', render: (e) => summary(e) },
    { key: 'hash', label: 'Entry hash', render: (e) => hashEl(e.hash, { len: 12 }) },
  ], { sortKey: 'seq', expand: (e) => h('div', { style: { paddingTop: '8px' } },
    h('dl', { class: 'kv', style: { marginBottom: '8px' } }, h('dt', null, 'prev_hash'), h('dd', null, hashEl(e.prev_hash, { full: true })), h('dt', null, 'hash'), h('dd', null, hashEl(e.hash, { full: true }))),
    h('pre', { class: 'json' }, JSON.stringify(e, null, 2))), empty: 'Log is empty.' });
  page.appendChild(table.el);
  try {
    const rows = await call('list_custody');
    table.setRows(rows || []);
  } catch (e) { toast(e.message, 'bad'); }
  return {};
}

function summary(e) {
  const ins = e.inputs || [], outs = e.outputs || [];
  const first = (ins[0] && (ins[0].path || ins[0].inner_path)) || (outs[0] && outs[0].path) || '';
  const p = e.params || {};
  const extra = e.action === 'artifact.parse' && p.status ? ` · ${p.status}` : e.action === 'artifact.detect' && p.subtype ? ` · ${p.subtype}` : '';
  return h('span', { title: first }, h('span', { class: 'dim' }, `${ins.length} in · ${outs.length} out${extra}`),
    first ? h('span', { class: 'sub mono', style: { fontSize: '11.5px' } }, first) : null);
}
