// Backend bridge: pywebview js_api (window.pywebview.api) or, in a plain browser with
// ?mock=1, the canned dev mock (dev_mock.js).  Also hosts window.dronedog, the object
// Python calls into (progress pushes and dropped paths).

const params = new URLSearchParams(location.search);
const progressListeners = new Set();
const unexpectedListeners = new Set();
// Python exception types that are normal, user-facing outcomes (bad input, missing file,
// no case open, network off ...). Anything else reaching the bridge is a bug: Python logs
// it (crashlog) and the UI shows a dialog naming the log file.
const EXPECTED = new Set(['ValueError', 'FileNotFoundError', 'KeyError', 'RuntimeError', 'PermissionError',
  'FileExistsError', 'NotADirectoryError', 'IsADirectoryError', 'MapPackError', 'OfflineError', 'CaseError',
  'EvidenceError', 'NotImplementedError', 'TimeoutError', 'ConnectionError', 'OSError', 'Error']);
const dropListeners = new Set();
let api = null;
let mode = 'none';

window.dronedog = {
  onProgress(job) { for (const fn of progressListeners) { try { fn(job); } catch (e) { console.error(e); } } },
  onDropPaths(payload) { for (const fn of dropListeners) { try { fn(payload); } catch (e) { console.error(e); } } },
};

function waitForPywebview(timeoutMs) {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api && Object.keys(window.pywebview.api).length) return resolve(true);
    const done = () => resolve(!!(window.pywebview && window.pywebview.api));
    window.addEventListener('pywebviewready', done, { once: true });
    setTimeout(done, timeoutMs);
  });
}

export async function connect() {
  const hostIsPywebview = params.get('host') === 'pywebview' || !!(window.chrome && window.chrome.webview);
  if (!hostIsPywebview && params.get('mock') === '1') {
    const { createDevMock } = await import('../dev_mock.js');
    api = await createDevMock((job) => window.dronedog.onProgress(job));
    mode = 'dev-mock';
  } else if (await waitForPywebview(hostIsPywebview ? 15000 : 1500)) {
    api = window.pywebview.api;
    mode = 'pywebview';
  } else {
    mode = 'none';
  }
  window.__ddMode = mode;
  return mode;
}

export function bridgeMode() { return mode; }

export async function call(method, ...args) {
  if (!api) throw new Error('No backend connected. Start DroneDog with "python -m dronedog.ui" (or --mock).');
  if (typeof api[method] !== 'function') throw new Error(`Backend method ${method}() is not available`);
  try {
    const res = await api[method](...args);
    if (res && typeof res === 'object' && !Array.isArray(res) && res.error && Object.keys(res).length <= 2 && res.ok !== false) {
      throw new Error(res.error);
    }
    return res;
  } catch (e) {
    const msg = ((e && (e.message || e.toString())) || String(e)).replace(/^Error:\s*/, '');
    const err = new Error(msg);
    err.name = (e && e.name) || 'Error';
    err.method = method;
    if (mode === 'pywebview' && !EXPECTED.has(err.name)) {
      for (const fn of unexpectedListeners) { try { fn(err); } catch (x) { /* ignore */ } }
    }
    throw err;
  }
}
export function onUnexpectedError(fn) { unexpectedListeners.add(fn); return () => unexpectedListeners.delete(fn); }

export function hasMethod(method) { return !!(api && typeof api[method] === 'function'); }
export function onProgress(fn) { progressListeners.add(fn); return () => progressListeners.delete(fn); }
export function onDrop(fn) { dropListeners.add(fn); return () => dropListeners.delete(fn); }
