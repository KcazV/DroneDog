// App shell: nav, case header, router, drag-and-drop, keyboard dispatch.
import { connect, call, onDrop, bridgeMode, onUnexpectedError } from './bridge.js';
import { store, subscribe, refreshCase, setCase } from './store.js';
import { h, clear, icon, toast, getPrefs, setPref, applyTheme, onPrefs, num, copyText } from './util.js';
import * as start from './screens/start.js';
import * as importScr from './screens/import.js';
import * as evidence from './screens/evidence.js';
import * as artifacts from './screens/artifacts.js';
import * as flights from './screens/flights.js';
import * as flight from './screens/flight.js';
import * as report from './screens/report.js';
import * as custody from './screens/custody.js';
import * as settings from './screens/settings.js';
import { lightButton, startNetwork, onNet, netStatus } from './network.js';

const ROUTES = {
  start: { mod: start, label: 'Start', icon: 'house' },
  import: { mod: importScr, label: 'Import', icon: 'upload', needsCase: true },
  evidence: { mod: evidence, label: 'Evidence', icon: 'hard-drive', needsCase: true, count: 'evidence' },
  artifacts: { mod: artifacts, label: 'Artifacts', icon: 'files', needsCase: true, count: 'artifacts' },
  flights: { mod: flights, label: 'Flights', icon: 'plane', needsCase: true, count: 'flights' },
  flight: { mod: flight, label: 'Flight', icon: 'activity', needsCase: true, hidden: true },
  report: { mod: report, label: 'Report', icon: 'file-text', needsCase: true },
  custody: { mod: custody, label: 'Custody', icon: 'shield-check', needsCase: true },
  settings: { mod: settings, label: 'Settings', icon: 'settings' },
};

let current = { name: null, inst: null };
let routeCount = 0;
let lastFlightId = null;
const view = document.getElementById('view');

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  return { name: parts[0] || 'start', param: parts[1] ? decodeURIComponent(parts[1]) : null };
}

export function go(path) { if (location.hash !== '#/' + path) location.hash = '#/' + path; else route(); }
window.ddGo = go;

async function route() {
  let { name, param } = parseHash();
  if (!ROUTES[name]) name = 'start';
  if (ROUTES[name].needsCase && !store.caseInfo.open) { name = 'start'; if (location.hash !== '#/start') history.replaceState(null, '', '#/start'); }
  if (name === 'flight') lastFlightId = param;
  try { current.inst && current.inst.destroy && current.inst.destroy(); } catch (e) { console.error(e); }
  clear(view);
  view.scrollTop = 0;
  current = { name, inst: null };
  renderNav();
  let routeOk = true, routeErr = null;
  try {
    current.inst = (await ROUTES[name].mod.mount(view, param, { go })) || {};
  } catch (e) {
    routeOk = false; routeErr = String(e && e.message || e);
    console.error(e);
    clear(view);
    view.appendChild(h('div', { class: 'page' }, h('div', { class: 'banner bad' }, icon('circle-x'),
      h('div', null, h('b', null, 'This screen could not load. '), String(e.message || e)))));
  }
  window.__ddRoute = { name, param, ok: routeOk, error: routeErr, n: ++routeCount };
  document.title = 'DroneDog' + (store.caseInfo.open ? ` - ${store.caseInfo.case?.name || ''}` : '') + (name !== 'start' ? ` - ${ROUTES[name].label}` : '');
}

// ------------------------------------------------------------------ nav + topbar
function renderNav() {
  const nav = document.getElementById('nav');
  clear(nav);
  const open = store.caseInfo.open;
  const counts = store.caseInfo.counts || {};
  const items = [];
  for (const [name, r] of Object.entries(ROUTES)) {
    if (name === 'settings') continue;
    if (r.hidden) {
      if (name === 'flight' && lastFlightId && open) {
        items.push(h('a', { class: 'nav-item sub' + (current.name === 'flight' ? ' active' : ''), href: `#/flight/${lastFlightId}`, title: `Flight #${lastFlightId}` },
          icon(r.icon), h('span', { class: 'label' }, `Flight #${lastFlightId}`)));
      }
      continue;
    }
    const disabled = r.needsCase && !open;
    const cnt = r.count && open && counts[r.count] != null ? h('span', { class: 'count' }, num(counts[r.count])) : null;
    items.push(h('a', { class: 'nav-item' + (current.name === name ? ' active' : '') + (disabled ? ' disabled' : ''),
      href: `#/${name}`, title: r.label }, icon(r.icon), h('span', { class: 'label' }, r.label), cnt));
    if (name === 'start') items.push(h('div', { class: 'nav-sep' }));
  }
  const app = store.app || {};
  nav.append(
    h('div', { class: 'brand' }, h('img', { src: 'assets/logo_64.png', alt: '' }),
      h('span', { class: 'brand-name' }, 'Drone', h('b', null, 'Dog'))),
    h('nav', { class: 'nav-list' }, items, h('div', { style: { flex: 1 } }),
      h('a', { class: 'nav-item' + (current.name === 'settings' ? ' active' : ''), href: '#/settings', title: 'Settings' },
        icon('settings'), h('span', { class: 'label' }, 'Settings'))),
    h('div', { class: 'nav-foot' }, `v${app.version || '?'}`, app.core_version ? ` · core ${app.core_version}` : '', h('br'), netStatus() && netStatus().online ? 'Online maps on (tiles only)' : 'Offline · no network access'));
}

function renderTop() {
  const top = document.getElementById('topbar');
  clear(top);
  const ci = store.caseInfo;
  const c = ci.case || {};
  const head = ci.open
    ? h('div', { class: 'case-head' },
        h('span', { class: 'case-title', title: ci.root || '' }, c.name || 'Untitled case'),
        h('span', { class: 'case-meta' },
          c.case_number ? h('span', null, h('span', { class: 'k' }, 'Case'), h('span', { class: 'mono' }, c.case_number)) : null,
          c.examiner ? h('span', null, h('span', { class: 'k' }, 'Examiner'), c.examiner) : null,
          c.agency ? h('span', { class: 'opt' }, h('span', { class: 'k' }, 'Agency'), c.agency) : null))
    : h('div', { class: 'case-head' }, h('span', { class: 'case-title muted' }, 'No case open'));
  const job = store.importJob;
  const jobPill = job && job.state === 'running'
    ? h('button', { class: 'job-pill', onclick: () => go('import'), title: job.message || '' },
        icon('loader-circle', 'spin'), `Importing ${Math.round((job.progress || 0) * 100)}%`)
    : null;
  const p = getPrefs();
  const unitsSeg = h('div', { class: 'seg', title: 'Display units (data stays SI)' },
    h('button', { class: p.units !== 'imperial' ? 'on' : '', onclick: () => setPref('units', 'metric') }, 'Metric'),
    h('button', { class: p.units === 'imperial' ? 'on' : '', onclick: () => setPref('units', 'imperial') }, 'Imperial'));
  const dark = document.documentElement.getAttribute('data-theme') !== 'light';
  const mock = store.app && store.app.mode === 'mock' || bridgeMode() === 'dev-mock';
  top.append(head, h('div', { class: 'top-actions' },
    jobPill,
    lightButton(),
    mock ? h('span', { class: 'mock-badge', title: 'Synthetic demo data: not evidence' }, 'Mock data') : null,
    unitsSeg,
    h('button', { class: 'btn ghost icon-only', title: dark ? 'Light theme' : 'Dark theme',
      onclick: () => setPref('theme', dark ? 'light' : 'dark') }, icon(dark ? 'sun' : 'moon'))));
}

subscribe((what) => {
  if (what === 'case' || what === 'job') { renderTop(); renderNav(); }
  if (what === 'case' && !store.caseInfo.open && ROUTES[current.name]?.needsCase) go('start');
});
onNet(() => renderNav());
onPrefs((k) => {
  if (k === 'theme') applyTheme();
  if (k === 'units' || k === 'theme') renderTop();
  if (current.inst && current.inst.onPrefs) current.inst.onPrefs(k);
});

// ------------------------------------------------------------------ drag and drop
const overlay = document.getElementById('drop-overlay');
let dragDepth = 0;
const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
document.addEventListener('dragenter', (e) => { if (!hasFiles(e)) return; dragDepth++; if (store.caseInfo.open) overlay.hidden = false; });
document.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) overlay.hidden = true; });
document.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
document.addEventListener('drop', (e) => {
  dragDepth = 0; overlay.hidden = true;
  if (!hasFiles(e)) return;
  e.preventDefault();
  if (bridgeMode() !== 'pywebview') {
    // Plain browser: no native paths available. Stage names so the flow can be shown.
    const names = Array.from(e.dataTransfer.files).map((f) => f.name);
    window.dronedog.onDropPaths({ paths: names.map((n) => `(browser drop) ${n}`), unresolved: [] });
  }
});
onDrop(({ paths = [], unresolved = [] }) => {
  if (!store.caseInfo.open) { toast('Create or open a case before adding evidence.', 'warn'); return; }
  const add = paths.filter((p) => !store.staged.includes(p));
  store.staged.push(...add);
  if (add.length) toast(`${add.length} item(s) staged for import`, 'ok');
  if (unresolved.length) toast(`Could not resolve ${unresolved.length} dropped item(s). Use "Add files" or "Add folder" instead.`, 'warn', 7000);
  if (current.name !== 'import') go('import'); else current.inst && current.inst.refresh && current.inst.refresh();
});

// ------------------------------------------------------------------ unexpected backend errors
let lastDialog = 0;
onUnexpectedError((err) => {
  if (Date.now() - lastDialog < 4000 || document.querySelector('.dd-modal')) return;
  lastDialog = Date.now();
  const logFile = (store.app && store.app.log_file) || 'logs/dronedog.log';
  const close = () => overlay.remove();
  const overlay = h('div', { class: 'dd-modal', onclick: (e) => { if (e.target === overlay) close(); } },
    h('div', { class: 'dd-modal-card', role: 'alertdialog', 'aria-labelledby': 'dd-err-title' },
      h('div', { class: 'dd-modal-h' }, icon('circle-alert'), h('b', { id: 'dd-err-title' }, 'Something went wrong')),
      h('p', null, `DroneDog could not finish "${err.method || 'request'}". The rest of the app keeps working.`),
      h('p', { class: 'mono dim', style: { fontSize: '12px' } }, `${err.name}: ${err.message}`.slice(0, 400)),
      h('p', null, 'The full details were written to the log file:'),
      h('div', { class: 'dd-modal-path' }, h('span', { class: 'mono' }, logFile),
        h('button', { class: 'btn sm', onclick: () => copyText(logFile) }, icon('copy'), 'Copy path')),
      h('div', { style: { display: 'flex', justifyContent: 'flex-end', marginTop: '14px' } },
        h('button', { class: 'btn primary', onclick: close }, 'Close'))));
  document.body.appendChild(overlay);
});

// ------------------------------------------------------------------ keyboard
document.addEventListener('keydown', (e) => {
  const t = e.target;
  if (t && (t.tagName === 'INPUT' && !['range', 'checkbox', 'radio'].includes(t.type) || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
  if (current.inst && current.inst.onKey && current.inst.onKey(e) === true) e.preventDefault();
});

// ------------------------------------------------------------------ boot
(async function boot() {
  applyTheme();
  renderNav(); renderTop();
  const mode = await connect();
  if (mode === 'none') {
    view.appendChild(h('div', { class: 'page' }, h('div', { class: 'banner bad' }, icon('circle-x'),
      h('div', null, h('b', null, 'No backend. '), 'Start DroneDog with ', h('span', { class: 'mono' }, 'python -m dronedog.ui'),
        ' (or ', h('span', { class: 'mono' }, '--mock'), ' for demo data). For a browser preview add ', h('span', { class: 'mono' }, '?mock=1'), ' to the URL.'))));
    window.__ddReady = true; document.documentElement.setAttribute("data-dd-ready", "1");
    return;
  }
  try { store.app = await call('app_info'); } catch (e) { store.app = { mode: 'unknown' }; }
  startNetwork();
  try { await refreshCase(); } catch (e) { store.caseInfo = { open: false }; }
  const q = new URLSearchParams(location.search);
  if (q.get('demo') === '1' && !store.caseInfo.open) {
    try {
      const rec = await call('recent_cases');
      if (rec && rec[0]) setCase(await call('case_open', rec[0].path));
    } catch (e) { console.warn(e); }
  }
  if (q.get('route') && (!location.hash || location.hash === '#')) history.replaceState(null, '', '#/' + q.get('route').replace(/~/g, '/'));
  if (q.get('openerr')) setTimeout(() => toast(q.get('openerr'), 'bad', 10000), 300);
  renderTop(); renderNav();
  window.addEventListener('hashchange', route);
  await route();
  window.__ddReady = true; document.documentElement.setAttribute("data-dd-ready", "1");
})();
