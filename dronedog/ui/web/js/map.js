// Offline maps: bundled Natural Earth vector basemap drawn on canvas tiles, DOM labels,
// lat/lon graticule with edge labels, scale bar, cursor coordinates, and raster MBTiles
// packs fetched through the bridge.  No network access anywhere.
import { call, hasMethod } from './bridge.js';
import { cssVar, getPrefs, onPrefs, setPref, h, icon, num } from './util.js';
import { onNet } from './network.js';

const L = window.L;
const NE = 'vendor/naturalearth/';

// ------------------------------------------------------------------ data
const mercX = (lon) => (lon + 180) / 360;
const mercY = (lat) => {
  const c = Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI / 180;
  return (1 - Math.log(Math.tan(c) + 1 / Math.cos(c)) / Math.PI) / 2;
};
const GRID = 32;

class Layer {
  constructor() { this.items = []; this.cells = new Map(); }
  add(item) {
    const id = this.items.length; this.items.push(item);
    const [x0, y0, x1, y1] = item.bbox;
    const cx0 = Math.max(0, Math.floor(x0 * GRID)), cx1 = Math.min(GRID - 1, Math.floor(x1 * GRID));
    const cy0 = Math.max(0, Math.floor(y0 * GRID)), cy1 = Math.min(GRID - 1, Math.floor(y1 * GRID));
    for (let cx = cx0; cx <= cx1; cx++) for (let cy = cy0; cy <= cy1; cy++) {
      const k = cy * GRID + cx; let a = this.cells.get(k); if (!a) this.cells.set(k, (a = [])); a.push(id);
    }
  }
  query(x0, y0, x1, y1) {
    const out = new Set();
    const cx0 = Math.max(0, Math.floor(x0 * GRID)), cx1 = Math.min(GRID - 1, Math.floor(x1 * GRID));
    const cy0 = Math.max(0, Math.floor(y0 * GRID)), cy1 = Math.min(GRID - 1, Math.floor(y1 * GRID));
    for (let cx = cx0; cx <= cx1; cx++) for (let cy = cy0; cy <= cy1; cy++) {
      const a = this.cells.get(cy * GRID + cx); if (a) for (const id of a) out.add(id);
    }
    const res = [];
    for (const id of [...out].sort((a, b) => a - b)) {
      const b = this.items[id].bbox;
      if (b[0] <= x1 && b[2] >= x0 && b[1] <= y1 && b[3] >= y0) res.push(this.items[id]);
    }
    return res;
  }
}

function project(coords) {
  const a = new Float64Array(coords.length * 2);
  let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
  for (let i = 0; i < coords.length; i++) {
    const x = mercX(coords[i][0]), y = mercY(coords[i][1]);
    a[2 * i] = x; a[2 * i + 1] = y;
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  return { a, bbox: [x0, y0, x1, y1] };
}

function buildPolys(fc) {
  const L2 = new Layer();
  for (const f of fc.features) {
    const g = f.geometry; if (!g) continue;
    const polys = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
    for (const poly of polys) {
      const rings = poly.map(project);
      L2.add({ rings: rings.map((r) => r.a), bbox: rings[0].bbox, p: f.properties || {} });
    }
  }
  return L2;
}
function buildLines(fc) {
  const L2 = new Layer();
  for (const f of fc.features) {
    const g = f.geometry; if (!g) continue;
    const lines = g.type === 'LineString' ? [g.coordinates] : g.type === 'MultiLineString' ? g.coordinates : [];
    for (const ln of lines) { const r = project(ln); L2.add({ a: r.a, bbox: r.bbox, p: f.properties || {} }); }
  }
  return L2;
}

const data = { low: null, countries: null, borders: null, admin1: null, lakes: null, rivers: null, roads: null, labels: null, full: false };
let loading = null;
const fullListeners = new Set();

async function fetchJson(name) {
  const r = await fetch(NE + name);
  if (!r.ok) throw new Error(`basemap ${name}: HTTP ${r.status}`);
  return r.json();
}

export function loadBasemap() {
  if (loading) return loading;
  loading = (async () => {
    const [low, borders] = await Promise.all([fetchJson('ne_countries_low.geojson'), fetchJson('ne_borders.geojson')]);
    data.low = buildPolys(low); data.borders = buildLines(borders);
    // full detail in the background
    (async () => {
      const names = ['ne_countries', 'ne_admin1_lines', 'ne_lakes', 'ne_rivers', 'ne_roads', 'ne_labels'];
      const res = await Promise.all(names.map((n) => fetchJson(n + '.geojson')));
      data.countries = buildPolys(res[0]); data.admin1 = buildLines(res[1]); data.lakes = buildPolys(res[2]);
      data.rivers = buildLines(res[3]); data.roads = buildLines(res[4]);
      data.labels = res[5].features.map((f) => ({ n: f.properties.n, k: f.properties.k, z: f.properties.z,
        cap: f.properties.cap, lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] }))
        .sort((a, b) => (a.k === b.k ? a.z - b.z : 'csp'.indexOf(a.k) - 'csp'.indexOf(b.k)));
      data.full = true;
      for (const fn of fullListeners) fn();
    })().catch((e) => console.error('basemap detail failed', e));
    return data;
  })();
  return loading;
}

// ------------------------------------------------------------------ vector tiles
function palette() {
  return {
    ocean: cssVar('--map-ocean'), land: cssVar('--map-land'), coast: cssVar('--map-coast'), border: cssVar('--map-border'),
    admin1: cssVar('--map-admin1'), road: cssVar('--map-road'), roadMajor: cssVar('--map-road-major'),
    river: cssVar('--map-river'), lake: cssVar('--map-lake'), grid: cssVar('--map-grid'),
  };
}

function pathRun(ctx, a, s, ox, oy, close) {
  let lx = -1e9, ly = -1e9, n = 0;
  for (let i = 0; i < a.length; i += 2) {
    const x = a[i] * s - ox, y = a[i + 1] * s - oy;
    if (n && Math.abs(x - lx) < 0.6 && Math.abs(y - ly) < 0.6 && i < a.length - 2) continue;
    if (n === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    lx = x; ly = y; n++;
  }
  if (close) ctx.closePath();
}

const VectorLayer = L.GridLayer.extend({
  options: { tileSize: 256, updateWhenZooming: false, keepBuffer: 2, className: 'dd-vector' },
  createTile(coords, done) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const c = document.createElement('canvas');
    c.width = c.height = 256 * dpr; c.style.width = c.style.height = '256px';
    loadBasemap().then(() => { this._draw(c, coords, dpr); done(null, c); }, (e) => done(e, c));
    return c;
  },
  _draw(canvas, coords, dpr) {
    const ctx = canvas.getContext('2d');
    const z = coords.z, n = 2 ** z, s = 256 * n;
    const xw = ((coords.x % n) + n) % n;
    const ox = xw * 256, oy = coords.y * 256;
    const pal = palette();
    const lay = getPrefs().layers || {};
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = pal.ocean; ctx.fillRect(0, 0, 256, 256);
    const pad = 2 / s;
    const bx0 = xw / n - pad, by0 = coords.y / n - pad, bx1 = (xw + 1) / n + pad, by1 = (coords.y + 1) / n + pad;
    const polys = (z <= 4 || !data.countries ? data.low : data.countries);
    ctx.beginPath();
    for (const it of polys.query(bx0, by0, bx1, by1)) for (const r of it.rings) pathRun(ctx, r, s, ox, oy, true);
    ctx.fillStyle = pal.land; ctx.fill('evenodd');
    ctx.lineWidth = z < 6 ? 0.8 : 1.1; ctx.strokeStyle = pal.coast; ctx.stroke();
    if (data.full && z >= 4) {
      ctx.beginPath();
      for (const it of data.lakes.query(bx0, by0, bx1, by1)) if ((it.p.r ?? 10) <= z + 3) for (const r of it.rings) pathRun(ctx, r, s, ox, oy, true);
      ctx.fillStyle = pal.lake; ctx.fill('evenodd'); ctx.strokeStyle = pal.coast; ctx.lineWidth = 0.6; ctx.stroke();
    }
    if (data.full && lay.rivers !== false && z >= 4) {
      ctx.beginPath();
      for (const it of data.rivers.query(bx0, by0, bx1, by1)) if ((it.p.r ?? 10) <= z + 1) pathRun(ctx, it.a, s, ox, oy, false);
      ctx.strokeStyle = pal.river; ctx.lineWidth = z >= 9 ? 1.6 : 1; ctx.stroke();
    }
    if (data.full && lay.roads !== false && z >= 5) {
      for (const major of [0, 1]) {
        if (!major && z < 7) continue;
        ctx.beginPath();
        for (const it of data.roads.query(bx0, by0, bx1, by1)) if ((it.p.m || 0) === major && (it.p.r ?? 10) <= z + 1) pathRun(ctx, it.a, s, ox, oy, false);
        ctx.strokeStyle = major ? pal.roadMajor : pal.road; ctx.lineWidth = (major ? 1.6 : 1) * (z >= 10 ? 1.5 : 1); ctx.stroke();
      }
    }
    if (data.full && lay.admin1 !== false && z >= 4) {
      ctx.beginPath();
      for (const it of data.admin1.query(bx0, by0, bx1, by1)) pathRun(ctx, it.a, s, ox, oy, false);
      ctx.setLineDash([4, 3]); ctx.strokeStyle = pal.admin1; ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.beginPath();
    for (const it of data.borders.query(bx0, by0, bx1, by1)) pathRun(ctx, it.a, s, ox, oy, false);
    ctx.strokeStyle = pal.border; ctx.lineWidth = z < 5 ? 0.9 : 1.4; ctx.stroke();
  },
});

// ------------------------------------------------------------------ graticule
const STEPS = [30, 20, 10, 5, 2, 1, 0.5, 0.25, 0.1, 0.05, 0.025, 0.01, 0.005, 0.0025, 0.001, 0.0005];
function gridStep(z) { const want = 180 / 2 ** z; let best = STEPS[0]; for (const s of STEPS) if (s >= want) best = s; return best; }
function fmtDeg(v, step, pos, neg) {
  const d = step >= 1 ? 0 : Math.min(4, Math.ceil(-Math.log10(step) + 0.001));
  return `${Math.abs(v).toFixed(d)}°${v === 0 ? '' : v > 0 ? pos : neg}`;
}

const GridLayer = L.GridLayer.extend({
  options: { tileSize: 256, className: 'dd-grid', pane: 'overlayPane', zIndex: 2 },
  createTile(coords) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const c = document.createElement('canvas');
    c.width = c.height = 256 * dpr; c.style.width = c.style.height = '256px';
    const ctx = c.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const z = coords.z, n = 2 ** z, step = gridStep(z);
    const lon0 = coords.x / n * 360 - 180, lon1 = (coords.x + 1) / n * 360 - 180;
    const yToLat = (y) => Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) * 180 / Math.PI;
    const lat0 = yToLat(coords.y + 1), lat1 = yToLat(coords.y);
    ctx.strokeStyle = palette().grid; ctx.lineWidth = 1; ctx.beginPath();
    for (let lo = Math.ceil(lon0 / step) * step; lo <= lon1; lo += step) {
      const x = Math.round((mercX(lo) * n - coords.x) * 256) + 0.5; ctx.moveTo(x, 0); ctx.lineTo(x, 256);
    }
    for (let la = Math.ceil(lat0 / step) * step; la <= lat1; la += step) {
      const y = Math.round((mercY(la) * n - coords.y) * 256) + 0.5; ctx.moveTo(0, y); ctx.lineTo(256, y);
    }
    ctx.stroke();
    return c;
  },
});

// DOM overlay: graticule edge labels + place labels.
const LabelLayer = L.Layer.extend({
  onAdd(map) {
    this._map = map;
    this._el = L.DomUtil.create('div', 'dd-labels', map.getPane('ddLabels'));
    this._onEnd = () => this._render();
    this._onStart = () => { this._el.style.display = 'none'; };
    map.on('moveend zoomend viewreset resize', this._onEnd);
    map.on('zoomstart', this._onStart);
    this._unfull = () => this._render();
    fullListeners.add(this._unfull);
    this._render();
  },
  onRemove(map) {
    map.off('moveend zoomend viewreset resize', this._onEnd); map.off('zoomstart', this._onStart);
    fullListeners.delete(this._unfull); this._el.remove();
  },
  _render() {
    const map = this._map, el = this._el; if (!map) return;
    el.style.display = ''; el.textContent = '';
    const lay = getPrefs().layers || {};
    const z = map.getZoom(), size = map.getSize(), b = map.getBounds();
    const frag = document.createDocumentFragment();
    // graticule edge labels
    if (lay.grid !== false) {
      const step = gridStep(Math.round(z));
      const w = b.getWest(), e = b.getEast(), s = b.getSouth(), nn = b.getNorth();
      for (let lo = Math.ceil(w / step) * step; lo <= e; lo += step) {
        const cp = map.latLngToContainerPoint([nn, lo]); if (cp.x < 30 || cp.x > size.x - 30) continue;
        const lp = map.containerPointToLayerPoint([cp.x + 3, 4]);
        const d = h('div', { class: 'grid-label' }, fmtDeg(((lo + 540) % 360) - 180, step, 'E', 'W'));
        d.style.left = lp.x + 'px'; d.style.top = lp.y + 'px'; frag.appendChild(d);
      }
      for (let la = Math.ceil(s / step) * step; la <= nn; la += step) {
        const cp = map.latLngToContainerPoint([la, w]); if (cp.y < 20 || cp.y > size.y - 30) continue;
        const lp = map.containerPointToLayerPoint([4, cp.y - 14]);
        const d = h('div', { class: 'grid-label' }, fmtDeg(la, step, 'N', 'S'));
        d.style.left = lp.x + 'px'; d.style.top = lp.y + 'px'; frag.appendChild(d);
      }
    }
    // place labels with simple collision avoidance
    if (lay.labels !== false && data.labels && !this.options.hidePlaces) {
      const boxes = [];
      let count = 0;
      for (const lb of data.labels) {
        if (count > 140) break;
        const minz = lb.k === 'c' ? lb.z : lb.k === 's' ? Math.max(4, lb.z - 0.5) : Math.max(5, lb.z - 1);
        if (minz > z + 0.01) continue;
        if (lb.k === 'c' && z > 8) continue;
        if (lb.k === 's' && z > 10) continue;
        if (lb.lat < b.getSouth() || lb.lat > b.getNorth()) continue;
        const cp = map.latLngToContainerPoint([lb.lat, lb.lon]);
        if (cp.x < 0 || cp.y < 0 || cp.x > size.x || cp.y > size.y) continue;
        const wpx = lb.n.length * (lb.k === 'c' ? 9.8 : lb.k === 's' ? 8.4 : 6.4) + 12, hpx = 16;
        const bx = lb.k === 'p' ? [cp.x - 6, cp.y - hpx / 2, cp.x + wpx, cp.y + hpx / 2] : [cp.x - wpx / 2, cp.y - hpx / 2, cp.x + wpx / 2, cp.y + hpx / 2];
        if (boxes.some((o) => bx[0] < o[2] && bx[2] > o[0] && bx[1] < o[3] && bx[3] > o[1])) continue;
        boxes.push(bx); count++;
        const lp = map.latLngToLayerPoint([lb.lat, lb.lon]);
        const d = h('div', { class: 'map-label ' + lb.k }, lb.n);
        d.style.left = lp.x + 'px'; d.style.top = lp.y + 'px'; frag.appendChild(d);
      }
    }
    el.appendChild(frag);
  },
});

// ------------------------------------------------------------------ MBTiles raster packs
const BLANK = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
const PackLayer = L.GridLayer.extend({
  createTile(coords, done) {
    const img = document.createElement('img');
    img.alt = '';
    const z = coords.z, n = 2 ** z, x = ((coords.x % n) + n) % n;
    call('tile', this.options.packId, z, x, coords.y).then((r) => {
      img.onload = () => done(null, img); img.onerror = () => done(null, img);
      img.src = r && r.data ? `data:${r.mime};base64,${r.data}` : BLANK;
    }, (e) => { img.src = BLANK; done(e, img); });
    return img;
  },
});

let packCache = null;
export async function listPacks(force = false) {
  if (!hasMethod('basemaps')) return [];
  if (!packCache || force) {
    try { packCache = (await call('basemaps')) || []; } catch (e) { packCache = []; }
  }
  return packCache;
}

// ------------------------------------------------------------------ map factory
export function createMap(el, opts = {}) {
  const map = L.map(el, { zoomControl: true, attributionControl: true, preferCanvas: true, worldCopyJump: true,
    minZoom: 2, maxZoom: 20, zoomSnap: 0.25, zoomDelta: 0.5, wheelPxPerZoomLevel: 90, center: [30, -40], zoom: 2.5,
    fadeAnimation: false });
  map.attributionControl.setPrefix(false);
  map.createPane('ddLabels'); map.getPane('ddLabels').style.zIndex = 450; map.getPane('ddLabels').style.pointerEvents = 'none';
  const vector = new VectorLayer({ attribution: 'Basemap: Natural Earth (public domain)', zIndex: 1 });
  const grid = new GridLayer();
  const labels = new LabelLayer({ hidePlaces: !!opts.hidePlaces });
  let pack = null;
  let scale = null;
  const st = { vector, grid, labels };

  function applyLayers() {
    const lay = getPrefs().layers || {};
    if (lay.grid !== false) { if (!map.hasLayer(grid)) grid.addTo(map); } else if (map.hasLayer(grid)) map.removeLayer(grid);
  }
  function applyScale() {
    if (scale) scale.remove();
    const imp = getPrefs().units === 'imperial';
    scale = L.control.scale({ metric: !imp, imperial: imp, position: 'bottomleft', maxWidth: 140 }).addTo(map);
  }
  let bmSeq = 0;
  async function applyBasemap(force) {
    const seq = ++bmSeq;
    const id = getPrefs().basemap || 'vector';
    const packs = await listPacks(!!force);
    if (seq !== bmSeq) return;
    if (pack) { map.removeLayer(pack); pack = null; }
    if (!map.hasLayer(vector)) vector.addTo(map);
    renderBmControl(packs);
    if (id === 'vector') return;
    const p = packs.find((x) => x.id === id && x.ok !== false && x.available !== false);
    if (!p) return;                         // online source while offline, or a removed pack: vector only
    pack = new PackLayer({ packId: p.id, maxNativeZoom: p.maxzoom, minZoom: Math.max(0, p.minzoom || 0), maxZoom: 20,
      zIndex: 1.5, attribution: p.attribution || p.name, updateWhenIdle: !!p.online, keepBuffer: p.online ? 0 : 2,
      bounds: p.bounds ? L.latLngBounds([p.bounds[1], p.bounds[0]], [p.bounds[3], p.bounds[2]]) : undefined });
    pack.addTo(map);
    // raster sources blur when stretched far past their native zoom: back off to one level over
    if (map.getZoom() > p.maxzoom + 1) map.setZoom(p.maxzoom + 1);
  }
  // basemap switcher: offline vector / imported packs / online sources (disabled while offline)
  const BmControl = L.Control.extend({ options: { position: 'topleft' },
    onAdd() { this._d = L.DomUtil.create('div', 'bm-control leaflet-control'); L.DomEvent.disableClickPropagation(this._d); return this._d; } });
  const bmControl = opts.basemapControl === false ? null : new BmControl().addTo(map);
  function renderBmControl(packs) {
    if (!bmControl) return;
    let cur = getPrefs().basemap || 'vector';
    const eff = packs.find((p) => p.id === cur);
    if (cur !== 'vector' && (!eff || eff.ok === false || eff.available === false)) cur = 'vector';
    const sel = h('select', { title: 'Basemap', onchange: () => setPref('basemap', sel.value) },
      h('option', { value: 'vector', selected: cur === 'vector' || null }, 'Offline: world (built-in)'));
    const local = packs.filter((p) => !p.online);
    const online = packs.filter((p) => p.online);
    if (local.length) sel.appendChild(h('optgroup', { label: 'Map packs (offline)' }, local.map((p) =>
      h('option', { value: p.id, selected: cur === p.id || null, disabled: p.ok === false || null }, p.name || p.id))));
    if (online.length) sel.appendChild(h('optgroup', { label: online[0].available ? 'Online' : 'Online (turn on online maps)' }, online.map((p) =>
      h('option', { value: p.id, selected: cur === p.id || null, disabled: !p.available || null }, p.name))));
    bmControl._d.replaceChildren(sel);
  }
  vector.addTo(map);
  labels.addTo(map);
  applyLayers(); applyScale(); applyBasemap(true);

  // cursor coordinates
  const Coord = L.Control.extend({ options: { position: 'bottomright' },
    onAdd() { this._d = L.DomUtil.create('div', 'coord-readout'); this._d.textContent = ''; return this._d; } });
  const coord = new Coord().addTo(map);
  map.on('mousemove', (e) => { coord._d.textContent = `${e.latlng.lat.toFixed(6)}, ${L.Util.wrapNum(e.latlng.lng, [-180, 180], true).toFixed(6)}`; });
  map.on('mouseout', () => { coord._d.textContent = ''; });

  const unsub = onPrefs((k) => {
    if (k === 'theme') { vector.redraw(); grid.redraw(); }
    if (k === 'layers') { vector.redraw(); applyLayers(); labels._render(); }
    if (k === 'units') applyScale();
    if (k === 'basemap') applyBasemap();
  });
  const unNet = onNet(() => applyBasemap(true));
  const onFull = () => vector.redraw();
  fullListeners.add(onFull);
  const ro = new ResizeObserver(() => map.invalidateSize({ pan: false }));
  ro.observe(el);
  map.ddDestroy = () => { unsub(); unNet(); fullListeners.delete(onFull); ro.disconnect(); map.remove(); };
  map.ddState = st;
  map.ddNativeMax = () => (pack ? pack.options.maxNativeZoom : 19);
  return map;
}

// ------------------------------------------------------------------ markers
export function divMarker(latlng, cls, ic, size = 26, title) {
  const html = `<div class="mk ${cls}" style="width:${size}px;height:${size}px">${ic ? icon(ic).outerHTML : ''}</div>`;
  return L.marker(latlng, { icon: L.divIcon({ html, className: '', iconSize: [size, size], iconAnchor: [size / 2, size / 2] }),
    title: title || '', keyboard: false, riseOnHover: true });
}

export function aircraftIcon(yaw, color) {
  const svg = `<svg class="aircraft" width="34" height="34" viewBox="-17 -17 34 34" style="transform:rotate(${yaw || 0}deg)">
    <path d="M0,-14 L9,11 L0,6 L-9,11 Z" fill="${color}" stroke="#0B0D12" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
  return L.divIcon({ html: svg, className: '', iconSize: [34, 34], iconAnchor: [17, 17] });
}

export function fmtLatLon(lat, lon) { return `${num(lat, 6)}, ${num(lon, 6)}`; }
