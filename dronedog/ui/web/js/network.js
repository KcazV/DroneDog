// Network light (top bar) + detail panel (D11).  Green = offline, red = online or any remote
// socket; an unexpected remote socket is flagged as a warning.  All data comes from Python
// (netmon.py): requests made through the single choke point, plus the OS socket table of the
// app process and its WebView2 children.
import { call, hasMethod } from './bridge.js';
import { h, clear, append, icon, toast, num, fmtBytes, getPrefs, setPref } from './util.js';

let status = null;
let timer = 0;
let panel = null;
let lightEl = null;
let showLocal = false;
const listeners = new Set();

export function netStatus() { return status; }
export function onNet(fn) { listeners.add(fn); return () => listeners.delete(fn); }
const ONLINE_IDS = [['online:esri_imagery', 'Satellite HD'], ['online:usgs_imagery', 'Satellite'], ['online:usgs_topo', 'Topo'], ['online:osm', 'Street']];

export function networkAvailable() { return hasMethod('net_status'); }

export async function refresh(includeOs = !!panel) {
  if (!networkAvailable()) return null;
  try {
    const prev = status;
    status = await call('net_status', includeOs);
    renderLight();
    if (panel) renderPanel();
    if (!prev || prev.online !== status.online) for (const fn of listeners) fn(status);
  } catch (e) { /* bridge busy or closing */ }
  return status;
}

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(async () => { await refresh(); schedule(); }, panel ? 2000 : (status && status.online ? 2500 : 5000));
}

export function startNetwork() {
  if (!networkAvailable()) return;
  refresh(false).then((s) => {
    if (s && !s.online && String(getPrefs().basemap || '').startsWith('online:')) setPref('basemap', 'vector');
    schedule();
  });
}

export async function setOnline(on) {
  try {
    status = await call('set_online', !!on);
    if (!on && String(getPrefs().basemap || '').startsWith('online:')) setPref('basemap', 'vector');
    toast(on ? 'Online maps on: map tiles are fetched from the selected provider.' : 'Offline: no network requests.', on ? 'warn' : 'ok');
  } catch (e) { toast(e.message, 'bad'); }
  for (const fn of listeners) fn(status);
  await refresh();
}

export function lightButton() {
  lightEl = h('button', { class: 'net-light', title: 'Network status', onclick: (e) => { e.stopPropagation(); togglePanel(); } });
  renderLight();
  return lightEl;
}

function renderLight() {
  if (!lightEl) return;
  const s = status;
  if (!s) { lightEl.className = 'net-light'; lightEl.replaceChildren(h('span', { class: 'dot' }), 'Network'); lightEl.hidden = !networkAvailable(); return; }
  const n = Math.max(s.remote_hosts.length, (s.hosts || []).filter((x) => x.requests).length);
  if (s.state === 'alert') {
    lightEl.className = 'net-light red alert';
    lightEl.replaceChildren(h('span', { class: 'dot' }), `Unexpected connection`);
  } else if (s.state === 'online') {
    lightEl.className = 'net-light red';
    lightEl.replaceChildren(h('span', { class: 'dot' }), `Online · ${n} host${n === 1 ? '' : 's'}`);
  } else {
    lightEl.className = 'net-light green';
    lightEl.replaceChildren(h('span', { class: 'dot' }), 'Offline');
  }
}

function togglePanel() {
  if (panel) { closePanel(); return; }
  panel = h('div', { class: 'net-panel', onclick: (e) => e.stopPropagation() });
  document.body.appendChild(panel);
  document.addEventListener('click', closePanel, { once: true });
  renderPanel();
  refresh(true).then(schedule);
}
function closePanel() { if (panel) { panel.remove(); panel = null; schedule(); } }

function renderPanel() {
  if (!panel) return;
  const s = status || { state: 'offline', hosts: [], remote_hosts: [], unexpected: [] };
  clear(panel);
  const head = s.state === 'alert'
    ? h('div', { class: 'banner bad' }, icon('shield-alert'), h('div', null, h('b', null, 'Unexpected remote connection. '),
        s.online ? 'A socket is open to a host DroneDog did not request.' : 'Online maps are off, yet a remote socket exists. Record this and investigate.'))
    : s.state === 'online'
      ? h('div', { class: 'banner warn' }, icon('triangle-alert'), h('div', null, h('b', null, 'Online maps are on. '), 'DroneDog is fetching map tiles over the network.'))
      : h('div', { class: 'banner ok' }, icon('shield-check'), h('div', null, h('b', null, 'Offline. '), 'No remote connections. Maps use the built-in basemap and imported packs.'));
  const bm = getPrefs().basemap || 'vector';
  append(panel, [
    h('div', { class: 'net-head' }, h('b', null, 'Network'), h('span', { class: 'spacer' }),
      h('button', { class: 'btn sm ghost icon-only', title: 'Close', onclick: closePanel }, icon('x'))),
    head,
    h('div', { class: 'net-row' },
      h('span', { class: 'k' }, 'Online maps'),
      h('div', { class: 'seg' },
        h('button', { class: !s.online ? 'on' : '', onclick: () => setOnline(false) }, 'Off'),
        h('button', { class: s.online ? 'on' : '', onclick: () => setOnline(true) }, 'On')),
      s.online ? h('div', { class: 'seg', title: 'Basemap' },
        h('button', { class: bm === 'vector' ? 'on' : '', onclick: () => { setPref('basemap', 'vector'); renderPanel(); } }, 'Offline map'),
        ONLINE_IDS.map(([id, label]) => h('button', { class: bm === id ? 'on' : '', onclick: () => { setPref('basemap', id); renderPanel(); } }, label))) : null),
    h('p', { class: 'net-disclosure' }, s.disclosure || 'Only map tiles are requested. Tile requests reveal the map area being viewed to the provider. No evidence, hashes or case data are sent.'),
    h('h4', null, 'Requests made by DroneDog'),
    hostsTable(s.hosts || []),
    h('h4', null, 'Operating-system sockets (DroneDog and its WebView2 processes)'),
    socketsTable(s),
    s.online ? h('div', { style: { display: 'flex', justifyContent: 'flex-end', marginTop: '10px' } },
      h('button', { class: 'btn primary', onclick: () => setOnline(false) }, icon('shield-check'), 'Go offline')) : null]);
}

function hostsTable(hosts) {
  if (!hosts.length) return h('div', { class: 'dim net-empty' }, 'None this session.');
  return h('table', { class: 'dt net-table' },
    h('thead', null, h('tr', null, ['Host', 'Purpose', 'Req', 'Out', 'In', 'Last (UTC)', 'No tile', 'Err'].map((t, i) => h('th', { class: (i >= 2 && i <= 4) || i >= 6 ? 'num' : '', title: t === 'No tile' ? 'HTTP 404: the provider has no tile at that place/zoom (normal at the edge of coverage)' : null }, t)))),
    h('tbody', null, hosts.map((x) => h('tr', null,
      h('td', { class: 'mono' }, x.host),
      h('td', null, x.purpose),
      h('td', { class: 'num' }, num(x.requests)),
      h('td', { class: 'num' }, fmtBytes(x.bytes_out)),
      h('td', { class: 'num' }, fmtBytes(x.bytes_in)),
      h('td', { class: 'mono' }, x.last_utc ? x.last_utc.slice(11, 19) : '—'),
      h('td', { class: 'num dim' }, num(x.not_found || 0)),
      h('td', { class: 'num', title: x.last_error || '' }, x.errors ? h('span', { style: { color: 'var(--bad)' } }, num(x.errors)) : '0')))));
}

function socketsTable(s) {
  const osv = s.os;
  if (!s.os_available) return h('div', { class: 'dim net-empty' }, s.os_error || 'OS socket view unavailable.');
  if (!osv) return h('div', { class: 'dim net-empty' }, 'Loading…');
  const remote = osv.sockets.filter((x) => x.scope !== 'local');
  const local = osv.sockets.filter((x) => x.scope === 'local');
  const procs = osv.processes || [];
  return h('div', null,
    h('div', { class: 'dim', style: { fontSize: '12px', margin: '0 0 6px' } },
      `${procs.length} process${procs.length === 1 ? '' : 'es'} watched (${[...new Set(procs.map((p) => p.name))].join(', ')}). `,
      `${num(local.length)} loopback socket${local.length === 1 ? '' : 's'} (the 127.0.0.1 UI server). `,
      h('b', { style: { color: remote.length ? 'var(--bad)' : 'var(--ok)' } }, `${num(remote.length)} remote.`)),
    remote.length ? h('table', { class: 'dt net-table' },
      h('thead', null, h('tr', null, ['Process', 'Local', 'Remote', 'Host', 'State', ''].map((t) => h('th', null, t)))),
      h('tbody', null, remote.map((x) => h('tr', null,
        h('td', null, `${x.process} (${x.pid})`), h('td', { class: 'mono' }, x.laddr), h('td', { class: 'mono' }, x.raddr || '—'),
        h('td', { class: 'mono' }, x.host || '—'), h('td', null, x.status),
        h('td', null, x.expected ? h('span', { class: 'badge b-warn' }, 'tiles') : h('span', { class: 'badge b-bad' }, 'unexpected')))))) : null,
    local.length ? h('details', { open: showLocal || null, ontoggle: (e) => { showLocal = e.target.open; } }, h('summary', { class: 'dim', style: { cursor: 'pointer', fontSize: '12px' } }, 'Show loopback sockets'),
      h('table', { class: 'dt net-table' }, h('tbody', null, local.slice(0, 60).map((x) => h('tr', null,
        h('td', null, `${x.process} (${x.pid})`), h('td', { class: 'mono' }, x.laddr), h('td', { class: 'mono' }, x.raddr || 'listening'), h('td', null, x.status)))))) : null);
}
