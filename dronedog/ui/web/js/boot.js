// Classic (non-module) boot script: error capture + theme before first paint.
// Kept out of index.html so the CSP can forbid inline scripts.
// Error capture first, so the smoke test and the visual check can report them.
  window.__ddErrors = [];
  function __ddRec(msg) {
    window.__ddErrors.push(msg);
    try { document.documentElement.setAttribute('data-dd-errors', JSON.stringify(window.__ddErrors).slice(0, 4000)); } catch (e) {}
  }
  window.addEventListener('error', function (e) {
    var t = e.target; if (t && t !== window && (t.src || t.href)) { __ddRec('resource failed: ' + (t.src || t.href)); return; }
    __ddRec(String(e.message || e) + (e.filename ? ' @ ' + e.filename + ':' + e.lineno : ''));
  }, true);
  window.addEventListener('unhandledrejection', function (e) {
    var r = e.reason; __ddRec('unhandled rejection: ' + (r && r.message ? r.message : String(r)));
  });
  (function () {
    var ce = console.error;
    console.error = function () {
      try { __ddRec('console.error: ' + Array.prototype.map.call(arguments, function (a) { return a && a.stack ? a.stack : String(a); }).join(' ')); } catch (e) {}
      return ce.apply(console, arguments);
    };
  })();
  (function () {  // apply theme before first paint
    try {
      var p = JSON.parse(localStorage.getItem('dronedog.prefs') || '{}');
      var t = p.theme || 'dark';
      if (t === 'system') t = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', t);
    } catch (e) { /* storage unavailable: keep dark */ }
  })();
document.addEventListener('securitypolicyviolation', function (e) {
  __ddRec('CSP blocked ' + e.violatedDirective + ': ' + (e.blockedURI || 'inline'));
});
// Forward page-side errors to the app log (<data dir>/logs/dronedog.log) when running in the
// desktop app. Rate-limited; never throws.
(function () {
  var sent = 0, origRec = __ddRec;
  __ddRec = function (msg) {
    origRec(msg);
    try {
      if (sent < 50 && window.pywebview && window.pywebview.api && window.pywebview.api.ui_log) {
        sent++;
        window.pywebview.api.ui_log('error', String(msg) + ' [' + location.hash + ']').catch(function () {});
      }
    } catch (e) { /* ignore */ }
  };
})();
