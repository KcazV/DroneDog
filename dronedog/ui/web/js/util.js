// DOM helpers, formatting (units + time), icons.
import { ICONS } from '../vendor/lucide/icons.js';

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'html') el.innerHTML = v;
      else if (k === 'text') el.textContent = v;
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

export function icon(name, cls) {
  const span = document.createElement('span');
  span.className = 'icon' + (cls ? ' ' + cls : '');
  const inner = ICONS[name] || ICONS['circle-alert'];
  span.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  return span;
}

export function iconSvg(name, size = 14) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ''}</svg>`;
}

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ------------------------------------------------------------------ prefs
const PREF_KEY = 'dronedog.prefs';
const DEFAULTS = { theme: 'dark', units: 'metric', tz: 'local', trackColor: 'alt', basemap: 'vector',
  chartRes: 'auto', layers: { roads: true, rivers: true, admin1: true, labels: true, grid: true } };
let prefs = (() => {
  try { return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem(PREF_KEY) || '{}')); }
  catch (e) { return Object.assign({}, DEFAULTS); }
})();
const prefListeners = new Set();
export function getPrefs() { return prefs; }
export function setPref(k, v) {
  prefs = Object.assign({}, prefs, { [k]: v });
  try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch (e) { /* private mode */ }
  for (const fn of prefListeners) { try { fn(k, v); } catch (e) { console.error(e); } }
}
export function onPrefs(fn) { prefListeners.add(fn); return () => prefListeners.delete(fn); }

export function applyTheme() {
  let t = prefs.theme;
  if (t === 'system') t = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', t);
}
export function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

// ------------------------------------------------------------------ units (display only; data stays SI)
const FT = 3.28084, MPH = 2.236936;
export const units = {
  imperial: () => prefs.units === 'imperial',
  alt: () => (prefs.units === 'imperial' ? 'ft' : 'm'),
  spd: () => (prefs.units === 'imperial' ? 'mph' : 'm/s'),
  vspd: () => (prefs.units === 'imperial' ? 'ft/s' : 'm/s'),
  altV: (m) => (m == null ? null : prefs.units === 'imperial' ? m * FT : m),
  spdV: (v) => (v == null ? null : prefs.units === 'imperial' ? v * MPH : v),
  vspdV: (v) => (v == null ? null : prefs.units === 'imperial' ? v * FT : v),
};
const nf = (d) => new Intl.NumberFormat('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
export function num(v, d = 0) {
  if (v == null || Number.isNaN(v)) return '—';
  if (Math.abs(v) < 0.5 * 10 ** -d) v = 0;
  return nf(d).format(v);
}
export function fmtAlt(m, d = 1) { return m == null ? '—' : `${num(units.altV(m), d)} ${units.alt()}`; }
export function fmtSpd(v, d = 1) { return v == null ? '—' : `${num(units.spdV(v), d)} ${units.spd()}`; }
export function fmtVSpd(v, d = 1) { return v == null ? '—' : `${num(units.vspdV(v), d)} ${units.vspd()}`; }
export function fmtDist(m) {
  if (m == null) return '—';
  if (prefs.units === 'imperial') {
    const ft = m * FT;
    return ft >= 5280 ? `${num(ft / 5280, 2)} mi` : `${num(ft, 0)} ft`;
  }
  return m >= 1000 ? `${num(m / 1000, 2)} km` : `${num(m, 0)} m`;
}
export function fmtBytes(n) {
  if (n == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, v = n;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${num(v, i ? 1 : 0)} ${u[i]}`;
}
export function fmtHex(n) { return n == null ? '—' : '0x' + Number(n).toString(16).toUpperCase().padStart(8, '0'); }
export function fmtDur(s) {
  if (s == null) return '—';
  s = Math.round(s);
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return hh ? `${hh}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}` : `${mm}:${String(ss).padStart(2, '0')}`;
}
export function fmtOffset(ms, frac = 1) {
  if (ms == null) return '—';
  const neg = ms < 0; ms = Math.abs(ms);
  const m = Math.floor(ms / 60000), s = (ms % 60000) / 1000;
  return (neg ? '-' : '') + `${String(m).padStart(2, '0')}:${s.toFixed(frac).padStart(frac ? 3 + frac : 2, '0')}`;
}

// ------------------------------------------------------------------ time: UTC primary, local secondary
export function parseUtc(s) {
  if (!s) return null;
  const d = new Date(/Z|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
  return Number.isNaN(d.getTime()) ? null : d;
}
export function fmtUtc(s, withMs = false) {
  const d = typeof s === 'string' ? parseUtc(s) : s;
  if (!d) return '—';
  const iso = d.toISOString();
  return iso.slice(0, 10) + ' ' + iso.slice(11, withMs ? 23 : 19) + 'Z';
}
export function fmtUtcTime(d, withMs = false) {
  if (!d) return '—';
  return d.toISOString().slice(11, withMs ? 23 : 19) + 'Z';
}
export function displayTz() {
  return prefs.tz === 'local' || !prefs.tz ? Intl.DateTimeFormat().resolvedOptions().timeZone : prefs.tz;
}
export function fmtLocal(s, withDate = true) {
  const d = typeof s === 'string' ? parseUtc(s) : s;
  if (!d) return '—';
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: displayTz(), year: withDate ? 'numeric' : undefined, month: withDate ? '2-digit' : undefined,
      day: withDate ? '2-digit' : undefined, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      timeZoneName: 'short',
    }).formatToParts(d);
    const g = (t) => (parts.find((p) => p.type === t) || {}).value || '';
    const date = withDate ? `${g('year')}-${g('month')}-${g('day')} ` : '';
    return `${date}${g('hour') === '24' ? '00' : g('hour')}:${g('minute')}:${g('second')} ${g('timeZoneName')}`;
  } catch (e) { return d.toLocaleString(); }
}
export function relTime(s) {
  const d = parseUtc(s); if (!d) return '';
  const sec = (Date.now() - d.getTime()) / 1000;
  if (sec < 90) return 'just now';
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400 * 2) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} days ago`;
}

// ------------------------------------------------------------------ clipboard + toasts
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const ta = h('textarea', { style: { position: 'fixed', opacity: '0' } }, text);
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } finally { ta.remove(); }
  }
  toast('Copied to clipboard', 'ok', 1400);
}

export function toast(msg, kind = 'info', ms = 4200) {
  const host = document.getElementById('toasts');
  const ic = { ok: 'circle-check', bad: 'circle-x', warn: 'triangle-alert', info: 'info' }[kind] || 'info';
  const t = h('div', { class: `toast ${kind}`, role: 'status' }, icon(ic), h('div', null, msg));
  host.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

export function hashEl(value, { full = false, len = 16 } = {}) {
  if (!value) return h('span', { class: 'dim' }, '—');
  const shown = full ? value : (value.length > len ? value.slice(0, len) + '…' : value);
  return h('span', { class: 'hash' + (full ? ' full' : ''), title: value }, shown,
    h('button', { class: 'copy', title: 'Copy full value', onclick: (e) => { e.stopPropagation(); copyText(value); } }, icon('copy')));
}

export function badge(text, cls, ic) { return h('span', { class: `badge ${cls}` }, ic ? icon(ic) : null, text); }

export const STATUS = {
  parsed: ['Parsed', 'b-ok', 'circle-check'],
  encrypted: ['Encrypted', 'b-enc', 'lock'],
  unsupported: ['Unsupported', 'b-muted', 'circle-alert'],
  failed: ['Failed', 'b-bad', 'circle-x'],
  pending: ['Pending', 'b-outline', 'clock'],
};
export function statusBadge(s) { const [t, c, i] = STATUS[s] || [s || '—', 'b-outline']; return badge(t, c, i); }

export const SOURCE = {
  'aircraft-dat': ['Aircraft DAT', 'b-info'],
  'controller-dat': ['Controller DAT', 'b-info'],
  'app-txt': ['App TXT', 'b-acc'],
};
export function sourceBadge(s) { const [t, c] = SOURCE[s] || [s || '—', 'b-outline']; return badge(t, c); }

// Correlation link kinds (dronedog/analysis/correlate.py). Only same-flight and app-tips assert
// that two records describe one flight; the others describe a relationship between files.
export const LINK_KINDS = {
  'same-flight': { label: (n) => `Same flight as #${n}`, cls: 'b-info', overlay: true },
  'app-tips': { label: (n) => `App tips: #${n}`, cls: 'b-info', overlay: true },
  'same-site': { label: (n) => `Same site as #${n}`, cls: 'b-outline', overlay: false },
  'duplicate-record': { label: (n) => `Duplicate of #${n}`, cls: 'b-warn', overlay: false },
  'truncated-copy': { label: (n) => `Truncated copy of #${n}`, cls: 'b-warn', overlay: false },
  'conflict': { label: (n) => `Conflicts with #${n}`, cls: 'b-bad', overlay: false },
  'rejected': { label: (n) => `Rejected candidate #${n}`, cls: 'b-muted', overlay: false, hidden: true },
};
export function linkKind(kind) {
  return LINK_KINDS[kind] || { label: (n) => `Linked to #${n} (${kind || 'link'})`, cls: 'b-outline', overlay: false };
}
export const visibleLinks = (links) => (links || []).filter((l) => !linkKind(l.kind).hidden);

// A flight's note badges: backend flight.note (string or list) wins; otherwise derived.
export function flightNotes(flight, artifact) {
  const f = flight || {};
  let notes = f.note == null ? null : Array.isArray(f.note) ? f.note : [f.note];
  if (!notes) {
    notes = [];
    if (f.encrypted_unreadable) notes.push('ENCRYPTED');
    if ((f.warnings || []).some((w) => /INCOMPLETE/i.test(w))) notes.push('INCOMPLETE');
    if (!f.encrypted_unreadable && f.frame_count === 0) notes.push('NO TRACK');
    if (artifact && artifact.deleted) notes.push('RECOVERED (DELETED)');
  }
  const STYLE = { ENCRYPTED: ['b-enc', 'lock'], INCOMPLETE: ['b-warn', 'triangle-alert'], 'NO TRACK': ['b-muted', 'circle-alert'],
    'RECOVERED (DELETED)': ['b-del', 'trash-2'] };
  return notes.filter(Boolean).map((n) => { const s = String(n).toUpperCase(); const [cls, ic] = STYLE[s] || ['b-outline', null]; return { text: s, cls, icon: ic }; });
}
export function noteBadges(flight, artifact) {
  return flightNotes(flight, artifact).map((n) => badge(n.text, n.cls, n.icon));
}

export function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// Okabe-Ito derived, readable on dark and light, none close to the gold accent.
export const FLIGHT_COLORS = ['#56B4E9', '#CC79A7', '#3DD6A3', '#F07167', '#B79CFF', '#9FD356', '#4F8BE8', '#E58FC9'];
// Sequential ramp for altitude/speed (violet -> blue -> teal -> mint), also clear of gold.
export const RAMP = ['#5B3F9E', '#4459C4', '#3B82D6', '#2FA8C9', '#33C2A6', '#7FDDA8', '#C9F2C7'];
export function rampColor(t) {
  t = Math.max(0, Math.min(1, t));
  const n = RAMP.length - 1, i = Math.min(n - 1, Math.floor(t * n)), f = t * n - i;
  const a = hex(RAMP[i]), b = hex(RAMP[i + 1]);
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)},${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)})`;
}
function hex(c) { return [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)); }
