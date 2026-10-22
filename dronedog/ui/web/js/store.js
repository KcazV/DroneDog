// Shared app state + cached listings.  Screens read from here and subscribe to changes.
import { call, onProgress } from './bridge.js';

const state = {
  app: null,          // app_info()
  caseInfo: { open: false },
  evidence: null, artifacts: null, flights: null,
  importJob: null,    // latest import job dict
  exportJob: null,
  staged: [],         // paths staged for import
  selectedFlights: new Set(),
};
const listeners = new Set();

export const store = state;
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function emit(what) { for (const fn of listeners) { try { fn(what); } catch (e) { console.error(e); } } }

export async function refreshCase() {
  state.caseInfo = await call('case_info');
  emit('case');
  return state.caseInfo;
}

export function invalidate() {
  state.evidence = state.artifacts = state.flights = null;
  emit('lists');
}

export async function load(kind, force = false) {
  if (!state.caseInfo.open) return [];
  if (!force && state[kind]) return state[kind];
  const method = { evidence: 'list_evidence', artifacts: 'list_artifacts', flights: 'list_flights' }[kind];
  const rows = await call(method);
  state[kind] = Array.isArray(rows) ? rows : (rows && rows.items) || [];
  emit(kind);
  return state[kind];
}

export function setCase(info) {
  state.caseInfo = info || { open: false };
  state.importJob = null; state.exportJob = null; state.staged = []; state.selectedFlights = new Set();
  invalidate();
  emit('case');
}

onProgress((job) => {
  if (job.kind === 'export') state.exportJob = job;
  else state.importJob = job;
  emit('job');
  if (job.kind !== 'export' && (job.state === 'done' || job.state === 'failed')) {
    invalidate();
    refreshCase().catch(() => {});
  }
  if (job.kind === 'export' && job.state === 'done') refreshCase().catch(() => {});
});
