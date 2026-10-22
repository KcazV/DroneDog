// Settings: appearance, units, time zone, chart resolution, map layers and offline map packs.
import { call, hasMethod } from '../bridge.js';
import { store } from '../store.js';
import { h, clear, icon, toast, getPrefs, setPref, displayTz, fmtBytes } from '../util.js';
import { listPacks } from '../map.js';
import { netStatus, setOnline, onNet, networkAvailable, refresh as netRefresh } from '../network.js';

export async function mount(el) {
  const page = h('div', { class: 'page', style: { maxWidth: '1100px' } });
  el.appendChild(page);
  page.appendChild(h('div', { class: 'page-head' }, h('h1', null, 'Settings'), h('span', { class: 'sub' }, 'Display preferences are stored for this Windows user. They never change evidence or exports.')));

  const appear = h('div', { class: 'card-b settings-grid' });
  page.appendChild(h('div', { class: 'card', style: { marginBottom: '16px' } }, h('div', { class: 'card-h' }, icon('eye'), h('h2', null, 'Display')), appear));
  function renderAppear() {
    clear(appear);
    const p = getPrefs();
    const seg = (key, opts) => h('div', { class: 'seg' }, opts.map(([v, l]) => h('button', { class: p[key] === v ? 'on' : '', onclick: () => { setPref(key, v); renderAppear(); } }, l)));
    let zones = [];
    try { zones = Intl.supportedValuesOf('timeZone'); } catch (e) { zones = ['UTC']; }
    const tzSel = h('select', { class: 'input', style: { maxWidth: '320px' }, onchange: () => { setPref('tz', tzSel.value); renderAppear(); } },
      h('option', { value: 'local', selected: p.tz === 'local' || null }, `This computer (${Intl.DateTimeFormat().resolvedOptions().timeZone})`),
      zones.map((z) => h('option', { value: z, selected: p.tz === z || null }, z)));
    const L = p.layers || {};
    const layer = (k, label) => h('label', { class: 'check', style: { marginRight: '16px' } }, h('input', { type: 'checkbox', checked: L[k] !== false,
      onchange: (e) => setPref('layers', Object.assign({}, getPrefs().layers, { [k]: e.target.checked })) }), label);
    appear.append(
      h('div', { class: 'k' }, 'Theme'), seg('theme', [['dark', 'Dark'], ['light', 'Light'], ['system', 'System']]),
      h('div', { class: 'k' }, 'Units'), h('div', null, seg('units', [['metric', 'Metric (m, m/s, km)'], ['imperial', 'Imperial (ft, mph, mi)']]), h('div', { class: 'dim', style: { fontSize: '12px', marginTop: '4px' } }, 'Display only. Stored data and exports stay SI.')),
      h('div', { class: 'k' }, 'Secondary time zone'), h('div', null, tzSel, h('div', { class: 'dim', style: { fontSize: '12px', marginTop: '4px' } }, `UTC is always primary. Local times shown in ${displayTz()}.`)),
      h('div', { class: 'k' }, 'Chart resolution'), h('div', null, seg('chartRes', [['auto', 'Auto'], ['high', 'High (20k points)'], ['full', 'Full (every frame)']]),
        h('div', { class: 'dim', style: { fontSize: '12px', marginTop: '4px' } }, 'Auto decimates for display with min/max preserved, so spikes stay visible.')),
      h('div', { class: 'k' }, 'Map layers'), h('div', null, layer('grid', 'Lat/lon grid'), layer('labels', 'Place labels'), layer('admin1', 'State borders'), layer('roads', 'Roads'), layer('rivers', 'Rivers')));
  }
  renderAppear();

  // network (D11)
  const netBody = h('div', { class: 'card-b settings-grid' });
  if (networkAvailable()) page.appendChild(h('div', { class: 'card', style: { marginBottom: '16px' } },
    h('div', { class: 'card-h' }, icon('satellite'), h('h2', null, 'Network')), netBody));
  function renderNet() {
    clear(netBody);
    const s = netStatus() || {};
    netBody.append(
      h('div', { class: 'k' }, 'Online maps'),
      h('div', null, h('div', { class: 'seg' },
        h('button', { class: !s.online ? 'on' : '', onclick: () => setOnline(false) }, 'Off (offline)'),
        h('button', { class: s.online ? 'on' : '', onclick: () => setOnline(true) }, 'On')),
        h('div', { class: 'dim', style: { fontSize: '12px', marginTop: '6px', maxWidth: '640px' } },
          'Off by default. When on, DroneDog (not the page) fetches map tiles from USGS The National Map (satellite, topo) and OpenStreetMap (street), caches them on disk, and logs the change in the open case custody log. ',
          s.disclosure || '')),
      h('div', { class: 'k' }, 'Current state'), h('div', null, s.state === 'offline' ? 'Offline: no remote connections' : s.state === 'alert' ? 'Warning: unexpected remote connection' : `Online: ${(s.remote_hosts || []).length} remote host(s) connected`));
  }
  renderNet();
  const unNet = onNet(() => { renderNet(); renderPacks(true); });
  netRefresh(false).then(renderNet);

  // map packs
  const packs = h('div');
  page.appendChild(h('div', { class: 'card', style: { marginBottom: '16px' } },
    h('div', { class: 'card-h' }, icon('layers'), h('h2', null, 'Offline map packs'), h('span', { class: 'spacer' }),
      h('button', { class: 'btn', disabled: !hasMethod('import_basemap') || null, onclick: importPack }, icon('upload'), 'Import map pack (.mbtiles)')),
    h('div', { class: 'card-b' }, h('div', { class: 'muted', style: { marginBottom: '12px', fontSize: '13px' } },
      'The built-in world basemap (Natural Earth: coastlines, borders, states, major roads, rivers, places) works everywhere. For street or satellite detail, build a raster MBTiles pack on a connected machine and import it here; it is copied to ',
      h('span', { class: 'mono', style: { fontSize: '12px' } }, (store.app && store.app.basemap_dir) || 'the local basemap folder'), '.'), packs)));
  async function renderPacks(force) {
    clear(packs);
    const list = await listPacks(force);
    const cur = getPrefs().basemap || 'vector';
    const opt = (id, title, sub, ok = true) => h('label', { class: 'fmt' + (cur === id ? ' on' : ''), style: { marginBottom: '8px', opacity: ok ? 1 : 0.5 } },
      h('input', { type: 'radio', name: 'bm', checked: cur === id || null, disabled: !ok || null, style: { accentColor: 'var(--gold)', marginTop: '3px' }, onchange: () => { setPref('basemap', id); renderPacks(); } }),
      h('div', null, h('div', { class: 't' }, title), h('div', { class: 'd' }, sub)));
    packs.appendChild(opt('vector', 'World vector (built-in)', 'Natural Earth 1:10m, simplified. Always available offline.'));
    for (const p of list.filter((x) => !x.online)) {
      packs.appendChild(opt(p.id, p.name || p.id, p.ok === false ? `Unusable: ${p.error}` :
        `${(p.format || '').toUpperCase()} · zoom ${p.minzoom}-${p.maxzoom} · ${fmtBytes(p.size)} · ${p.path}`, p.ok !== false));
    }
    for (const p of list.filter((x) => x.online)) {
      packs.appendChild(opt(p.id, `${p.name} (online)`, p.available ? `${p.coverage} · zoom 0-${p.maxzoom} · ${p.attribution}` : 'Turn on online maps to use this source.', !!p.available));
    }
  }
  async function importPack() {
    try {
      const sel = await call('pick_paths', 'mbtiles');
      if (!sel || !sel[0]) return;
      const res = await call('import_basemap', sel[0]);
      toast(`Map pack imported: ${res.imported?.name || sel[0]}`, 'ok');
      setPref('basemap', res.imported?.id || 'vector');
      renderPacks(true);
    } catch (e) { toast(e.message, 'bad', 8000); }
  }
  renderPacks(true);

  // shortcuts + about
  const app = store.app || {};
  page.appendChild(h('div', { class: 'grid2' },
    h('div', { class: 'card' }, h('div', { class: 'card-h' }, icon('keyboard'), h('h2', null, 'Flight playback shortcuts')),
      h('div', { class: 'card-b settings-grid', style: { gridTemplateColumns: '140px 1fr' } },
        ...[['Space', 'Play / pause'], ['Left / Right', 'Step 1 s'], ['Shift + arrows', 'Step 10 s'], [', and .', 'Previous / next frame'], ['Home / End', 'Start / end'], ['[ and ]', 'Slower / faster'], ['F', 'Follow aircraft']]
          .flatMap(([k, v]) => [h('div', null, h('span', { class: 'kbd' }, k)), h('div', { class: 'muted' }, v)]))),
    h('div', { class: 'card' }, h('div', { class: 'card-h' }, icon('info'), h('h2', null, 'About')),
      h('div', { class: 'card-b' }, h('dl', { class: 'kv' },
        h('dt', null, 'Application'), h('dd', null, `DroneDog ${app.version || ''}`),
        h('dt', null, 'Backend'), h('dd', null, app.mode === 'mock' ? 'Mock (synthetic demo data)' : app.mode || '—'),
        h('dt', null, 'Core'), h('dd', { class: 'mono' }, app.core_version || 'not loaded'),
        h('dt', null, 'Network'), h('dd', null, 'Offline by default. Maps, fonts and libraries are bundled; online map tiles only when turned on.'),
        h('dt', null, 'Portable mode'), h('dd', null, app.portable ? 'On: app data and cases travel with the DroneDog folder' : 'Off: app data in the Windows user profile'),
        h('dt', null, 'App data'), h('dd', { class: 'mono', style: { fontSize: '12px' } }, app.data_dir || '—'),
        h('dt', null, 'Default case folder'), h('dd', { class: 'mono', style: { fontSize: '12px' } }, app.default_cases_dir || '—'),
        h('dt', null, 'Log file'), h('dd', { class: 'mono', style: { fontSize: '12px' } }, app.log_file || '—'),
        h('dt', null, 'Libraries'), h('dd', null, 'Leaflet 1.9.4 (BSD-2), uPlot 1.6.32 (MIT), Lucide icons (ISC), Inter and JetBrains Mono (OFL), Natural Earth (public domain)'))))));
  return { destroy: unNet };
}
