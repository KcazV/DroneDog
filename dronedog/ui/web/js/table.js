// Sortable table with optional row selection and expandable detail rows.
import { h, clear, icon } from './util.js';

export class DataTable {
  /**
   * columns: [{key, label, num?, width?, cls?, sort?: row=>value|false, render?: row=>Node|string, title?}]
   * opts: {rowKey, onRowClick, expand: row=>Node, selectable, selected: Set, onSelect, sortKey, sortDir, empty,
   *        rowClass: row=>string}
   */
  constructor(columns, opts = {}) {
    this.columns = columns;
    this.o = Object.assign({ rowKey: (r) => r.id, sortDir: 1, empty: 'Nothing to show' }, opts);
    this.rows = [];
    this.sortKey = opts.sortKey || null;
    this.sortDir = opts.sortDir || 1;
    this.expanded = new Set();
    this.selected = opts.selected || new Set();
    this.seen = null;
    this.thead = h('thead');
    this.tbody = h('tbody');
    this.table = h('table', { class: 'dt' }, this.thead, this.tbody);
    this.el = h('div', { class: 'table-wrap' }, this.table);
    this.renderHead();
  }

  renderHead() {
    clear(this.thead);
    const tr = h('tr');
    if (this.o.selectable) {
      const all = h('input', { type: 'checkbox', title: 'Select all', onchange: (e) => {
        const keys = this.rows.map(this.o.rowKey);
        if (e.target.checked) keys.forEach((k) => this.selected.add(k)); else keys.forEach((k) => this.selected.delete(k));
        this.renderBody(); this.o.onSelect && this.o.onSelect(this.selected);
      } });
      this.allBox = all;
      tr.appendChild(h('th', { class: 'chk' }, h('label', { class: 'check' }, all)));
    }
    for (const c of this.columns) {
      const sortable = c.sort !== false;
      const th = h('th', { class: [c.num ? 'num' : '', sortable ? 'sortable' : '', c.cls || ''].join(' '), title: c.title || null,
        style: c.width ? { width: c.width } : null,
        onclick: sortable ? () => { if (this.sortKey === c.key) this.sortDir *= -1; else { this.sortKey = c.key; this.sortDir = 1; } this.renderHead(); this.renderBody(); } : null },
        c.label, this.sortKey === c.key ? h('span', { class: 'sort' }, this.sortDir > 0 ? '▲' : '▼') : null);
      tr.appendChild(th);
    }
    this.thead.appendChild(tr);
  }

  setRows(rows, { flashNew = false } = {}) {
    const prev = this.seen;
    this.rows = rows || [];
    this.flash = flashNew && prev ? new Set(this.rows.map(this.o.rowKey).filter((k) => !prev.has(k))) : new Set();
    this.seen = new Set(this.rows.map(this.o.rowKey));
    this.renderBody();
  }

  sorted() {
    if (!this.sortKey) return this.rows;
    const col = this.columns.find((c) => c.key === this.sortKey);
    const get = col && typeof col.sort === 'function' ? col.sort : (r) => r[this.sortKey];
    const d = this.sortDir;
    return [...this.rows].sort((a, b) => {
      const x = get(a), y = get(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (typeof x === 'string' ? x.localeCompare(y, undefined, { numeric: true }) : x - y) * d;
    });
  }

  renderBody() {
    clear(this.tbody);
    const rows = this.sorted();
    const ncol = this.columns.length + (this.o.selectable ? 1 : 0);
    if (!rows.length) {
      this.tbody.appendChild(h('tr', null, h('td', { class: 'empty', colspan: ncol }, this.o.empty)));
    }
    for (const r of rows) {
      const key = this.o.rowKey(r);
      const isExp = this.expanded.has(key);
      const cls = [this.o.onRowClick || this.o.expand ? 'clickable' : '', this.selected.has(key) ? 'selected' : '',
        isExp ? 'expanded' : '', this.flash.has(key) ? 'row-new' : '', this.o.rowClass ? this.o.rowClass(r) : ''].join(' ');
      const tr = h('tr', { class: cls, onclick: (e) => {
        if (e.target.closest('button, a, input, label')) return;
        if (this.o.expand) { if (this.expanded.has(key)) this.expanded.delete(key); else this.expanded.add(key); this.renderBody(); }
        if (this.o.onRowClick) this.o.onRowClick(r, e);
      } });
      if (this.o.selectable) {
        const cb = h('input', { type: 'checkbox', checked: this.selected.has(key), onchange: (e) => {
          if (e.target.checked) this.selected.add(key); else this.selected.delete(key);
          tr.classList.toggle('selected', e.target.checked);
          this.syncAll(); this.o.onSelect && this.o.onSelect(this.selected);
        } });
        tr.appendChild(h('td', { class: 'chk' }, h('label', { class: 'check' }, cb)));
      }
      for (const c of this.columns) {
        let v = c.render ? c.render(r) : r[c.key];
        if (v === undefined || v === null || v === '') v = h('span', { class: 'dim' }, '—');
        tr.appendChild(h('td', { class: [c.num ? 'num' : '', c.cls || ''].join(' ') }, v));
      }
      this.tbody.appendChild(tr);
      if (isExp && this.o.expand) {
        this.tbody.appendChild(h('tr', { class: 'detail' }, h('td', { colspan: ncol }, this.o.expand(r))));
      }
    }
    this.syncAll();
  }

  syncAll() {
    if (!this.allBox) return;
    const keys = this.rows.map(this.o.rowKey);
    const n = keys.filter((k) => this.selected.has(k)).length;
    this.allBox.checked = n > 0 && n === keys.length;
    this.allBox.indeterminate = n > 0 && n < keys.length;
  }
}

export function emptyState(ic, title, sub, action) {
  return h('div', { class: 'card', style: { padding: '48px 24px', textAlign: 'center' } },
    h('div', { style: { fontSize: '36px', color: 'var(--text-3)' } }, icon(ic)),
    h('div', { style: { fontWeight: 650, fontSize: '16px', marginTop: '8px' } }, title),
    sub ? h('div', { class: 'muted', style: { marginTop: '4px' } }, sub) : null,
    action ? h('div', { style: { marginTop: '16px' } }, action) : null);
}
