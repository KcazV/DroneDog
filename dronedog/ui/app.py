"""DroneDog desktop window (pywebview / WebView2).

    python -m dronedog.ui            real backend (dronedog.ui.api.Api)
    python -m dronedog.ui --mock     synthetic data (dronedog.ui.mock_api.MockApi)

Options:
    --debug            open WebView2 dev tools
    --smoke SECONDS    open, wait, print a JSON health report (JS errors, screen) and close
    --size WxH         initial window size (default 1440x900)
    --demo             (with --mock) open the demo case at start
    --route R          initial screen, e.g. "flight/1"
    --open CASE_DIR    open a case folder at start
    --shot PNG         with --smoke: save a window screenshot before closing
    --qa all|R1,R2     walk routes in the native window, print per-route errors (JSON), exit
    --report FILE      also write the --smoke/--qa JSON to FILE (the windowed exe has no stdout)
    --online           turn online maps on at start (D11; logged like the toggle)

App data (recent cases, map packs, tile cache, WebView2 profile, logs) lives in
dronedog.paths.data_dir(): beside DroneDog.exe in portable mode, else %LOCALAPPDATA%/DroneDog.
Crashes are written to <data dir>/logs/dronedog.log and announced in a dialog.

The web UI is served by pywebview's built-in server bound to 127.0.0.1 only; nothing
is reachable off-host and the page loads no remote resources (D3, D4).
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import shutil
import socket
import sys
import time
from pathlib import Path

from dronedog.ui import crashlog

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
INDEX = WEB / "index.html"
ICON = WEB / "assets" / "dronedog.ico"
PREFERRED_PORT = 42117   # fixed when free so WebView2 localStorage (UI prefs) persists

log = logging.getLogger("dronedog.ui")


def _fix_mimetypes() -> None:
    # Windows' registry can map .js to text/plain, which breaks ES module loading.
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("application/json", ".json")
    mimetypes.add_type("application/geo+json", ".geojson")
    mimetypes.add_type("font/woff2", ".woff2")
    mimetypes.add_type("image/svg+xml", ".svg")


# WebView2 hardening (D11): the page may only talk to the loopback UI server. Every other
# request is sent to a dead proxy, and Chromium's background networking is disabled. The
# CSP in index.html is the second, independent lock. Online map tiles are fetched by Python.
WEBVIEW2_ARGS = " ".join([
    "--disable-features=ElasticOverscroll,msSmartScreenProtection,AutofillServerCommunication,"
    "OptimizationHints,MediaRouter,DialMediaRouteProvider,InterestFeedContentSuggestions",
    "--proxy-server=http://127.0.0.1:9",          # loopback is bypassed implicitly
    "--disable-background-networking", "--disable-component-update", "--disable-domain-reliability",
    "--disable-sync", "--no-pings", "--dns-prefetch-disable", "--disable-client-side-phishing-detection",
    "--no-first-run", "--metrics-recording-only",
])


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _profile_in_use(profile: Path) -> bool:
    """True when a running WebView2 already uses this user-data folder (a second DroneDog
    window, or the previous one still shutting down). WebView2 refuses to share it."""
    try:
        import psutil
    except ImportError:
        return False
    needle = str(profile).lower().rstrip("\\/")
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if not (p.info["name"] or "").lower().startswith("msedgewebview2"):
                continue
            if any(needle in (a or "").lower() for a in (p.info["cmdline"] or [])):
                return True
        except (psutil.Error, TypeError):
            continue
    return False


def _storage_dir() -> tuple[str, Path | None]:
    """WebView2 profile folder (holds localStorage = UI preferences). Returns (path, temp_dir)
    where temp_dir must be removed at exit when the main profile was busy."""
    from dronedog.paths import data_dir
    main = data_dir() / "webview"
    main.mkdir(parents=True, exist_ok=True)
    tmp_root = data_dir() / "webview-tmp"
    if tmp_root.is_dir():                         # leftovers from earlier fallbacks
        for old in tmp_root.iterdir():
            shutil.rmtree(old, ignore_errors=True)
    if not _profile_in_use(main):
        return str(main), None
    tmp = tmp_root / str(os.getpid())
    tmp.mkdir(parents=True, exist_ok=True)
    log.warning("WebView2 profile %s is in use; using a temporary profile %s", main, tmp)
    return str(tmp), tmp


def netprobe(window, api) -> dict:
    """Prove the page cannot reach the network on its own (CSP + dead proxy) and report the
    OS socket view (D11). Used by --smoke --netprobe."""
    out = {}
    probe = ("window.__ddProbe = 'pending'; fetch('https://tile.openstreetmap.org/0/0/0.png', {mode: 'no-cors'})"
             ".then(() => { window.__ddProbe = 'REACHED NETWORK'; }, e => { window.__ddProbe = 'blocked: ' + e.message; }); 1")
    try:
        window.evaluate_js(probe)
        for _ in range(60):
            v = window.evaluate_js("window.__ddProbe")
            if v != "pending":
                break
            time.sleep(0.1)
        out["page_fetch"] = v
        out["csp_reports"] = json.loads(window.evaluate_js(
            "JSON.stringify((window.__ddErrors || []).filter(e => e.indexOf('CSP') === 0))"))
    except Exception as e:  # noqa: BLE001
        out["page_fetch"] = f"error: {e}"
    st = api.net_status(True)
    out["state"] = st["state"]
    out["remote_count"] = st["remote_count"]
    out["unexpected"] = st["unexpected"]
    out["processes"] = st.get("os", {}).get("processes")
    out["local_sockets"] = st["local_count"]
    try:
        import psutil
        kids = psutil.Process(os.getpid()).children(recursive=True)
        out["webview_has_proxy_flag"] = any("--proxy-server=http://127.0.0.1:9" in " ".join(k.cmdline()) for k in kids
                                            if k.name().lower().startswith("msedgewebview2"))
    except Exception as e:  # noqa: BLE001
        out["webview_has_proxy_flag"] = f"unknown: {e}"
    return out


def grab_window(window, path: str) -> str:
    """Screenshot the window's screen area (Windows; Pillow ImageGrab). QA/docs helper."""
    import ctypes
    from ctypes import wintypes

    from PIL import ImageGrab

    window.on_top = True
    time.sleep(0.6)
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, window.title)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    # trim the invisible resize border Windows 10/11 adds around the frame
    bbox = (rect.left + 7, rect.top, rect.right - 7, rect.bottom - 7)
    ImageGrab.grab(bbox=bbox, all_screens=True).save(path)
    window.on_top = False
    return path


def _confirm_close_during_import() -> bool:
    """Native Yes/No box (default No). True = close and cancel the import."""
    try:
        import ctypes
        MB_YESNO, MB_ICONWARNING, MB_DEFBUTTON2, MB_SETFOREGROUND, MB_TOPMOST = 0x4, 0x30, 0x100, 0x10000, 0x40000
        text = ("An import is in progress.\n\nClosing DroneDog now cancels the import. The cancellation is "
                "recorded in the custody log.\n\nClose and cancel the import?")
        return ctypes.windll.user32.MessageBoxW(
            None, text, "DroneDog", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SETFOREGROUND | MB_TOPMOST) == 6
    except Exception:  # noqa: BLE001 - no dialog available: keep the window open
        return False


def make_api(mock: bool):
    if mock:
        from dronedog.ui.mock_api import MockApi
        return MockApi()
    from dronedog.ui.api import Api
    return Api()


def main(argv: list[str] | None = None) -> int:
    """Entry point. Any crash is logged to <data dir>/logs/dronedog.log and shown in a dialog."""
    logfile = crashlog.setup()
    try:
        return _main(argv, logfile)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 130
    except BaseException as e:  # noqa: BLE001
        quiet = any(a in (argv if argv is not None else sys.argv) for a in ("--qa", "--smoke"))
        crashlog.fatal(e, show_dialog=not quiet)
        return 1


def _main(argv: list[str] | None, logfile: Path) -> int:
    ap = argparse.ArgumentParser(prog="python -m dronedog.ui", description="DroneDog desktop UI")
    ap.add_argument("--mock", action="store_true", help="use the synthetic mock backend")
    ap.add_argument("--debug", action="store_true", help="open dev tools")
    ap.add_argument("--smoke", type=float, metavar="SECONDS",
                    help="open, wait SECONDS, print a JSON health report and close")
    ap.add_argument("--size", default="1440x900", help="initial window size WxH")
    ap.add_argument("--demo", action="store_true", help="(mock) open the demo case on start")
    ap.add_argument("--route", default="", help="initial screen, e.g. flights or flight/1")
    ap.add_argument("--open", metavar="CASE_DIR", help="open this case folder on start")
    ap.add_argument("--online", action="store_true", help="turn online maps on at start (logged like the toggle)")
    ap.add_argument("--eval", dest="eval_js", metavar="JS", help="with --smoke: run this JS 3 s before the report/screenshot")
    ap.add_argument("--netprobe", action="store_true",
                    help="with --smoke: try page-side network access (must be blocked) and report sockets")
    ap.add_argument("--shot", metavar="PNG", help="with --smoke: save a screenshot of the window before closing")
    ap.add_argument("--report", metavar="FILE", help="write the --smoke/--qa JSON report to FILE too")
    ap.add_argument("--qa", metavar="ROUTES",
                    help="QA walk: 'all' or comma-separated routes (e.g. flights,flight/3); prints JSON per route and exits")
    args = ap.parse_args(argv)

    import webview
    from webview.dom import DOMEventHandler

    from dronedog import paths
    try:
        from dronedog import __version__ as version
    except Exception:  # noqa: BLE001
        version = "?"
    log.info("DroneDog %s starting (mock=%s, frozen=%s) %s; log %s", version, args.mock,
             bool(getattr(sys, "frozen", False)), paths.describe(), logfile)

    _fix_mimetypes()
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = WEBVIEW2_ARGS
    api = make_api(args.mock)
    api._log_file = str(logfile)
    open_error = None
    if args.open:
        try:
            api.case_open(args.open)
        except Exception as e:  # noqa: BLE001 - a bad --open is not a crash: show Start + a message
            log.warning("could not open %s: %s", args.open, e)
            open_error = f"Could not open {args.open}: {e}"
    if args.online:
        api.set_online(True)
    w, h = (int(v) for v in args.size.lower().split("x"))
    query = "?host=pywebview" + ("&mock=1" if args.mock else "") + ("&demo=1" if args.demo else "")
    if open_error:
        from urllib.parse import quote
        query += "&openerr=" + quote(open_error[:300], safe="")   # no "/" in the query: pywebview treats the URL as a path
    if args.route:
        # a "#/..." hash would be mangled into backslashes by pywebview's local-path handling
        query += "&route=" + args.route.strip("#/").replace("/", "~")
    window = webview.create_window(
        "DroneDog" + (" (MOCK DATA)" if args.mock else ""),
        url=str(INDEX) + query,
        js_api=api,
        width=w, height=h, min_size=(1024, 640),
        background_color="#0B0D12",
        text_select=True,
    )

    def on_closing():
        # Closing mid-import stops Python's worker threads under the running import, which then
        # fails with an internal error. Ask first; on confirm, cancel the import so the custody
        # log records job.end state "cancelled".
        from dronedog.pipeline import JOBS
        running = [j for j in list(JOBS.values()) if j.state in ("queued", "running")]
        if not running:
            return None
        if not _confirm_close_during_import():
            return False
        for j in running:
            j.cancel()
        for j in running:
            j.wait(15)
        log.info("window closed during import; %d import job(s) cancelled", len(running))
        return None

    window.events.closing += on_closing

    def push_progress(job: dict) -> None:
        window.run_js(f"window.dronedog && window.dronedog.onProgress({json.dumps(job)})")

    api._attach(window, push_progress)

    def on_drop(event: dict) -> None:
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        unresolved = [f.get("name") for f in files if not f.get("pywebviewFullPath")]
        payload = json.dumps({"paths": paths, "unresolved": unresolved})
        window.run_js(f"window.dronedog && window.dronedog.onDropPaths({payload})")

    def on_loaded() -> None:
        # Full native paths for dropped files/folders (pywebview >= 5 on WebView2).
        try:
            window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True,
                                                               stop_propagation=True)
        except Exception as e:  # noqa: BLE001
            log.warning("drag-and-drop path capture unavailable: %s", e)

    window.events.loaded += on_loaded

    report: dict = {}

    def qa_routes() -> list[str]:
        if args.qa != "all":
            return [r.strip() for r in args.qa.split(",") if r.strip()]
        routes = ["start", "import", "evidence", "artifacts", "flights"]
        try:
            routes += [f"flight/{f['id']}" for f in api.list_flights()]
        except Exception as e:  # noqa: BLE001
            report.setdefault("setup_errors", []).append(f"list_flights: {e}")
        return routes + ["report", "custody", "settings", "flights", "@netpanel"]

    def qa() -> None:
        def js(expr):
            return window.evaluate_js(expr)
        t0 = time.time()
        while not js("!!window.__ddReady") and time.time() - t0 < 30:
            time.sleep(0.2)
        results = []
        for r in qa_routes():
            if r == "@netpanel":                      # the network panel (D11), opened over the last screen
                before = js("(window.__ddErrors || []).length")
                js("document.querySelector('.net-light') && document.querySelector('.net-light').click(); 1")
                time.sleep(2.5)
                res = json.loads(js(
                    "JSON.stringify({route: '@netpanel', info: {ok: !!document.querySelector('.net-panel')},"
                    f" errors: (window.__ddErrors || []).slice({before}), bad: [], emptyCharts: 0,"
                    " light: (document.querySelector('.net-light') || {}).textContent || null,"
                    " stray: [...document.querySelectorAll('.net-panel *')].filter(e => [...e.childNodes].some(n => n.nodeType === 3 && /^(null|undefined|NaN)$/.test(n.textContent.trim()))).length})"))
                res["ms"] = 2500
                results.append(res)
                continue
            before = js("(window.__ddErrors || []).length")
            n0 = js("(window.__ddRoute && window.__ddRoute.n) || 0")
            js(f"location.hash = '#/{r}'; true")
            t1 = time.time()
            while time.time() - t1 < 30:
                n = js("(window.__ddRoute && window.__ddRoute.n) || 0")
                if n and n > n0:
                    break
                time.sleep(0.1)
            time.sleep(1.2)   # async map/chart/link work after mount
            res = json.loads(js(
                "JSON.stringify({route: location.hash, info: window.__ddRoute || null,"
                f" errors: (window.__ddErrors || []).slice({before}),"
                " bad: [...document.querySelectorAll('.banner.bad')].map(e => e.textContent.slice(0, 200)),"
                " charts: document.querySelectorAll('.fd-charts .uplot').length,"
                " emptyCharts: [...document.querySelectorAll('.fd-charts .chart')].filter(c => !c.querySelector('canvas') || c.getBoundingClientRect().height < 60).length,"
                " markers: document.querySelectorAll('.leaflet-marker-pane > *').length, aircraft: !!document.querySelector('.aircraft'),"
                " linked: document.querySelectorAll('.legend .row.check').length, note: (document.querySelector('.fd-map .banner, .fd-main > .banner') || {}).textContent || null,"
                " stray: [...document.querySelectorAll('body *')].filter(e => [...e.childNodes].some(n => n.nodeType === 3 && /^(null|undefined|NaN|\[object Object\])$/.test(n.textContent.trim()))).length})"))
            res["ms"] = int((time.time() - t1) * 1000)
            results.append(res)
        report["qa"] = results
        report["failed"] = [x["route"] for x in results if x["errors"] or x["bad"] or x["emptyCharts"] or x["stray"]
                            or not (x["info"] or {}).get("ok", False)]
        window.destroy()

    def smoke() -> None:
        if args.eval_js:
            time.sleep(max(0.0, args.smoke - 3))
            try:
                window.evaluate_js(args.eval_js)
            except Exception as e:  # noqa: BLE001
                report["eval_error"] = str(e)
            time.sleep(3)
        else:
            time.sleep(args.smoke)
        try:
            res = window.evaluate_js(
                "JSON.stringify({errors: window.__ddErrors || [], ready: !!window.__ddReady, "
                "route: location.hash, mode: window.__ddMode || null, title: document.title})")
            report.update(json.loads(res))
            if args.netprobe:
                report["netprobe"] = netprobe(window, api)
            if args.shot:
                report["shot"] = grab_window(window, args.shot)
        except Exception as e:  # noqa: BLE001
            report["error"] = f"{type(e).__name__}: {e}"
        window.destroy()

    port = PREFERRED_PORT if _port_free(PREFERRED_PORT) else None
    storage, tmp_profile = _storage_dir()
    webview.start(
        func=qa if args.qa else (smoke if args.smoke else None),
        debug=args.debug,
        http_server=True,
        http_port=port,
        private_mode=False,
        storage_path=storage,
        icon=str(ICON) if ICON.exists() else None,
    )
    log.info("window closed")
    if tmp_profile is not None:
        time.sleep(1.0)
        shutil.rmtree(tmp_profile, ignore_errors=True)
    if args.qa or args.smoke:
        text = json.dumps(report, indent=1)
        print(text)
        if args.report:
            Path(args.report).write_text(text, encoding="utf-8")
        if args.qa:
            return 1 if report.get("failed") or report.get("setup_errors") else 0
        return 0 if report.get("ready") and not report.get("errors") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
