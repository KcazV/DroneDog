// Browser-only dev shim (loaded when ?mock=1 and not inside pywebview).  Replays the
// canned fixture written by `python -m dronedog.ui.mock_api --write-fixture` so the UI
// can be previewed and screenshotted from `python -m http.server`.  Not shipped logic:
// the desktop app always talks to Python.  ?big=1 upsamples flight #1 to 150k points
// to check chart/map performance.

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
const clone = (o) => JSON.parse(JSON.stringify(o));

export async function createDevMock(push) {
  const res = await fetch('dev/mock_fixture.json');
  if (!res.ok) throw new Error('dev/mock_fixture.json missing: run python -m dronedog.ui.mock_api --write-fixture');
  const fx = await res.json();
  const big = new URLSearchParams(location.search).get('big') === '1';
  let state = null;   // {meta, root, populated, custody}
  let jobN = 0;
  let online = false;   // browser preview: the toggle works, but no tiles are fetched
  const jobs = {};

  function caseInfo() {
    if (!state) return { open: false };
    const ci = clone(fx.case_info);
    ci.case = clone(state.meta); ci.root = state.root;
    if (!state.populated) {
      ci.counts = { evidence: 0, artifacts: 0, artifact_sources: 0, deleted_sources: 0, flights: 0, frames: 0, events: 0 };
      ci.artifacts_by_status = {};
    }
    ci.custody = { ok: true, entries: state.custody.length, head_seq: state.custody.length, head_hash: (state.custody.at(-1) || {}).hash || null };
    return ci;
  }
  function need() { if (!state) throw new Error('No case is open'); return state; }

  function replay(kind, timeline, onDone) {
    const id = `${kind.slice(0, 3)}-dev${++jobN}`;
    const t0 = performance.now();
    const base = { job_id: id, kind, started_utc: new Date().toISOString(), finished_utc: null };
    jobs[id] = Object.assign({}, base, timeline[0][1]);
    (async () => {
      for (const [t, snap] of timeline) {
        const wait = t * 1000 - (performance.now() - t0);
        if (wait > 0) await sleep(wait);
        const job = Object.assign({}, base, clone(snap));
        if (job.log) job.log.forEach((l) => { if (!l.ts_utc) l.ts_utc = new Date().toISOString(); });
        if (job.state === 'done') { job.finished_utc = new Date().toISOString(); onDone && onDone(job); }
        jobs[id] = job;
        push(job);
      }
    })();
    return { job_id: id };
  }

  function upsample(det, factor) {
    const f = det.frames, n = f.t_offset_ms.length, out = {};
    const m = (n - 1) * factor + 1;
    for (const [k, arr] of Object.entries(f)) {
      const o = new Array(m);
      for (let i = 0; i < m; i++) {
        const a = Math.floor(i / factor), b = Math.min(n - 1, a + 1), fr = (i % factor) / factor;
        const va = arr[a], vb = arr[b];
        o[i] = typeof va === 'number' && typeof vb === 'number' ? va + (vb - va) * fr : va;
      }
      out[k] = o;
    }
    out.t_offset_ms = out.t_offset_ms.map(Math.round);
    det.frames = out; det.decimation = { total: m, returned: m, method: 'dev-upsampled' };
    return det;
  }

  const api = {
    async app_info() { return Object.assign({}, fx.app_info, { mode: 'mock' }); },
    async recent_cases() { return clone(fx.recent); },
    async case_new(meta) {
      if (!meta || !meta.name || !meta.examiner) throw new Error("'name' and 'examiner' are required");
      const root = `${(meta.location || 'C:\\DroneDog Cases').replace(/[\\/]$/, '')}\\${[meta.case_number, meta.name].filter(Boolean).join(' ')}`;
      state = { meta: Object.assign({ created_utc: new Date().toISOString().slice(0, 19) + 'Z', tool: 'DroneDog' }, meta), root, populated: false,
        custody: [Object.assign(clone(fx.custody[0]), { ts_utc: new Date().toISOString(), params: { name: meta.name, case_number: meta.case_number, examiner: meta.examiner, agency: meta.agency } })] };
      return caseInfo();
    },
    async case_open(path) {
      const r = fx.recent.find((x) => x.path.toLowerCase() === String(path).toLowerCase());
      if (!r || r.exists === false) throw new Error(`Not a DroneDog case folder (case.json not found): ${path}`);
      if (r.path === fx.case_root) state = { meta: clone(fx.case_meta), root: fx.case_root, populated: true, custody: clone(fx.custody) };
      else state = { meta: { name: r.name, case_number: r.case_number, examiner: fx.case_meta.examiner, agency: fx.case_meta.agency }, root: r.path, populated: false, custody: [clone(fx.custody[0])] };
      return caseInfo();
    },
    async case_info() { return caseInfo(); },
    async pick_paths(kind) {
      await sleep(150);
      return { files: ['D:\\Evidence\\FH-2026-004817\\Item 3 - P4P V2\\DJI Assistant export\\FLY047.DAT'],
        folder: ['D:\\Evidence\\FH-2026-004817\\Item 1 - iPhone 13 Pro'],
        image: ['D:\\Evidence\\FH-2026-004817\\Item 5 - microSD\\Item5_microSD.E01'], mbtiles: [] }[kind] || [];
    },
    async import_paths(paths) {
      const st = need();
      if (!paths || !paths.length) throw new Error('No paths to import');
      return replay('import', st.populated ? fx.import_timeline_dup : fx.import_timeline, () => {
        if (!st.populated) { st.populated = true; st.custody = clone(fx.custody); }
      });
    },
    async job_status(id) { if (!jobs[id]) throw new Error('unknown job'); return clone(jobs[id]); },
    async list_evidence() { return need().populated ? clone(fx.evidence) : []; },
    async list_artifacts() { return need().populated ? clone(fx.artifacts) : []; },
    async list_flights() { return need().populated ? clone(fx.flights) : []; },
    async get_flight(id) {
      need();
      const d = fx.flight_detail[String(id)];
      if (!d) throw new Error(`no flight ${id}`);
      const det = clone(d);
      return big && String(id) === '1' ? upsample(det, 75) : det;
    },
    async get_track_geojson(ids) {
      need();
      const set = new Set((ids || []).map(Number));
      return { type: 'FeatureCollection', features: fx.tracks.features.filter((f) => set.has(f.properties.flight_id)) };
    },
    async basemaps() {
      return [['usgs_imagery', 'Satellite (USGS)'], ['usgs_topo', 'Topo (USGS)'], ['osm', 'Street (OpenStreetMap)']].map(([k, n]) =>
        ({ id: 'online:' + k, name: n, online: true, kind: 'online', ok: online, available: online, maxzoom: 16, minzoom: 0, attribution: '', coverage: 'United States' }));
    },
    async net_status() {
      return { state: online ? 'online' : 'offline', online, sources: [], remote_count: 0, remote_hosts: [], hosts_contacted: 0, unexpected: [], hosts: [],
        disclosure: 'Only map tiles are requested. Tile requests reveal the map area being viewed to the provider. No evidence, hashes or case data are sent.',
        os_available: false, os_error: 'Browser preview: OS socket view only in the desktop app.', local_count: 0 };
    },
    async set_online(on) { online = !!on; return api.net_status(); },
    async tile() { return null; },
    async import_basemap() { throw new Error('Map packs cannot be imported in the browser preview.'); },
    async export_report(fids, fmts) {
      need();
      if (!fids.length) throw new Error('Select at least one flight');
      const tl = clone(fx.export_timeline);
      const last = tl[tl.length - 1][1];
      last.result.files = last.result.files.filter((f) => f.format === 'manifest' || fmts.includes(f.format));
      return replay('export', tl, null);
    },
    async verify_custody() { await sleep(300); return clone(fx.verify.ok && state && state.populated ? fx.verify : { ok: true, entries: need().custody.length, head_seq: state.custody.length, head_hash: state.custody.at(-1).hash, first_bad_line: null, first_bad_seq: null, reason: null }); },
    async list_custody() { return clone(need().custody); },
    async reveal_path() { return { ok: false, error: 'Browser preview: cannot open folders.' }; },
  };
  return api;
}
