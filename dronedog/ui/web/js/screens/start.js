// Start: brand hero, new case, recent cases, open case folder.
import { call, hasMethod } from '../bridge.js';
import { store, setCase } from '../store.js';
import { h, icon, toast, relTime, num } from '../util.js';

export async function mount(el, _param, { go }) {
  const ci = store.caseInfo;
  const page = h('div', { class: 'start' });
  el.appendChild(page);

  page.appendChild(h('div', { class: 'hero' },
    h('img', { src: 'assets/logo_256.png', alt: 'DroneDog logo' }),
    h('div', null,
      h('h1', null, 'Drone', h('b', null, 'Dog')),
      h('div', { class: 'tag' }, 'Drone flight-log forensics'),
      h('div', { class: 'offline' }, icon('shield-check'), 'Runs fully offline. Evidence is hashed on intake and never modified.'))));
  page.appendChild(h('div', { class: 'banner warn', style: { width: '100%', maxWidth: '1040px' } }, icon('triangle-alert'),
    h('div', null, h('b', null, 'Training and research tool, not independently validated for casework. '),
      'Verify every finding with an independent, validated tool before relying on it. What has and has not been tested: docs/VALIDATION_STATUS.md in the DroneDog repository.')));

  if (ci.open) {
    const c = ci.case || {}, n = ci.counts || {};
    page.appendChild(h('div', { class: 'card', style: { width: '100%', maxWidth: '1040px' } },
      h('div', { class: 'card-h' }, icon('folder-open'), h('h2', null, 'Current case'), h('span', { class: 'spacer' }),
        h('button', { class: 'btn primary', onclick: () => go(n.flights ? 'flights' : 'import') }, n.flights ? 'Continue to flights' : 'Import evidence', icon('chevron-right'))),
      h('div', { class: 'card-b', style: { display: 'flex', gap: '24px', alignItems: 'center', flexWrap: 'wrap' } },
        h('div', null, h('div', { style: { fontWeight: 650, fontSize: '16px' } }, c.name), h('div', { class: 'mono dim', style: { fontSize: '12px' } }, ci.root || '')),
        h('div', { class: 'tiles', style: { flex: 1, minWidth: '380px' } },
          tile('Evidence', n.evidence), tile('Artifacts', n.artifacts), tile('Flights', n.flights, 'acc'),
          tile('Deleted recovered', n.deleted_sources, 'del')))));
  }

  // new case form: plain labelled fields, nothing pre-filled, and no default location
  const f = {
    name: input('Case name', true),
    case_number: input('Case number', false),
    examiner: input('Examiner', true),
    agency: input('Agency', false),
  };
  const loc = h('input', { class: 'input mono', required: true, style: { flex: 1, fontSize: '12px' } });
  const create = h('button', { class: 'btn primary lg', type: 'submit' }, icon('folder-plus'), 'Create case');
  const form = h('form', { class: 'card', onsubmit: async (e) => {
    e.preventDefault();
    const meta = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.input.value.trim()]));
    if (!meta.name || !meta.examiner) { toast('Case name and examiner are required.', 'warn'); return; }
    if (!loc.value.trim()) { toast('Choose where the case folder will be created.', 'warn'); return; }
    meta.location = loc.value.trim();
    create.disabled = true;
    try {
      const info = await call('case_new', meta);
      setCase(info);
      toast(`Case created: ${info.root || meta.name}`, 'ok');
      go('import');
    } catch (err) { toast(err.message, 'bad', 8000); } finally { create.disabled = false; }
  } },
  h('div', { class: 'card-h' }, icon('folder-plus'), h('h2', null, 'New case')),
  h('div', { class: 'card-b' },
    h('div', { class: 'form-grid' }, f.name.el, f.case_number.el, f.examiner.el, f.agency.el,
      h('div', { class: 'field wide' }, h('label', null, 'Case folder location', h('span', { class: 'req' }, ' *')),
        h('div', { style: { display: 'flex', gap: '8px' } }, loc,
          h('button', { class: 'btn', type: 'button', onclick: async () => {
            try { const p = await call('pick_paths', 'folder'); if (p && p[0]) loc.value = p[0]; } catch (err) { toast(err.message, 'bad'); }
          } }, icon('folder'), 'Browse')),
        h('div', { class: 'dim', style: { fontSize: '12px' } }, 'A new folder named after the case number (or case name) is created inside this location.')),
      h('div', { class: 'wide', style: { display: 'flex', justifyContent: 'flex-end', marginTop: '4px' } }, create))));

  // recent
  const list = h('ul', { class: 'recent' }, h('li', { class: 'dim' }, 'Loading…'));
  const recentCard = h('div', { class: 'card', style: { display: 'flex', flexDirection: 'column' } },
    h('div', { class: 'card-h' }, icon('history'), h('h2', null, 'Recent cases'), h('span', { class: 'spacer' }),
      h('button', { class: 'btn sm', onclick: async () => {
        try {
          const p = await call('pick_paths', 'folder');
          if (p && p[0]) await openCase(p[0]);
        } catch (err) { toast(err.message, 'bad', 8000); }
      } }, icon('folder-open'), 'Open case folder…')),
    list);
  page.appendChild(h('div', { class: 'start-grid' }, form, recentCard));

  async function openCase(path) {
    try {
      const info = await call('case_open', path);
      setCase(info);
      toast(`Opened ${info.case?.name || path}`, 'ok');
      go(info.counts && info.counts.flights ? 'flights' : 'import');
    } catch (err) { toast(err.message, 'bad', 8000); }
  }

  try {
    const rec = hasMethod('recent_cases') ? await call('recent_cases') : [];
    list.textContent = '';
    if (!rec || !rec.length) list.appendChild(h('li', { class: 'dim', style: { cursor: 'default' } }, 'No recent cases on this machine.'));
    for (const r of rec || []) {
      const missing = r.exists === false;
      list.appendChild(h('li', { class: missing ? 'missing' : '', title: missing ? 'Folder not found' : r.path,
        onclick: () => !missing && openCase(r.path) },
        h('div', { class: 'ico' }, icon(missing ? 'circle-alert' : 'folder')),
        h('div', { style: { minWidth: 0, flex: 1 } },
          h('div', { class: 't' }, r.name || r.path, r.case_number ? h('span', { class: 'mono dim', style: { marginLeft: '8px', fontWeight: 400, fontSize: '12px' } }, r.case_number) : null),
          h('div', { class: 'p' }, r.path)),
        h('div', { class: 'when' }, missing ? 'not found' : relTime(r.opened_utc))));
    }
  } catch (err) { list.textContent = ''; list.appendChild(h('li', { class: 'dim' }, err.message)); }

  setTimeout(() => { if (!ci.open) f.name.input.focus(); }, 50);
  return {};
}

function input(label, req) {
  const i = h('input', { class: 'input', required: req || null });
  return { input: i, el: h('div', { class: 'field' }, h('label', null, label, req ? h('span', { class: 'req' }, ' *') : null), i) };
}
function tile(k, v, cls) { return h('div', { class: 'tile ' + (cls || '') }, h('div', { class: 'v' }, num(v ?? 0)), h('div', { class: 'k' }, k)); }
