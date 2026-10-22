"""Application log + crash reporting for the desktop app.

DroneDog.exe is a windowed build (no console), so anything that goes wrong must land in a
file the examiner can find: ``<data dir>/logs/dronedog.log`` (rotated at ~5 MB, 3 backups),
where the data dir is ``dronedog.paths.data_dir()`` (beside the exe in portable mode).

* ``setup()`` attaches the rotating file handler to the ``dronedog`` and ``pywebview``
  loggers (pywebview logs every exception raised by a bridge call with its traceback) and
  installs ``sys.excepthook`` / ``threading.excepthook`` so stray exceptions are recorded.
* ``fatal(exc)`` records a startup crash and shows a native message box naming the file.

This is an application log, not the forensic methodology log (that is the per-case
custody.log); it holds no evidence content, only errors and app lifecycle lines.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback
from pathlib import Path

LOG_NAME = "dronedog.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3
_handler: logging.Handler | None = None
_path: Path | None = None


def log_path() -> Path:
    global _path
    if _path is None:
        try:
            from dronedog.paths import data_dir
            base = data_dir()
        except Exception:  # noqa: BLE001 - never fail to find somewhere to write
            import tempfile
            base = Path(tempfile.gettempdir()) / "DroneDog"
        _path = base / "logs" / LOG_NAME
    return _path


def setup(level: int = logging.INFO) -> Path:
    """Idempotent. Returns the log file path."""
    global _handler
    p = log_path()
    if _handler is not None:
        return p
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(p, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                                 encoding="utf-8", delay=True)
    except OSError:
        return p
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"))
    h.setLevel(level)
    for name in ("dronedog", "pywebview"):
        lg = logging.getLogger(name)
        lg.addHandler(h)
        if lg.level == logging.NOTSET or lg.level > level:
            lg.setLevel(level)
    _handler = h

    prev_hook = sys.excepthook

    def excepthook(et, ev, tb):
        logging.getLogger("dronedog").critical("unhandled exception\n%s", "".join(traceback.format_exception(et, ev, tb)))
        if prev_hook and prev_hook is not sys.__excepthook__:
            prev_hook(et, ev, tb)

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        logging.getLogger("dronedog").error(
            "unhandled exception in thread %s\n%s", getattr(args.thread, "name", "?"),
            "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    return p


def message_box(title: str, text: str, error: bool = True) -> None:
    """Native Windows message box (no Tk dependency). Silently does nothing elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        MB_ICONERROR, MB_ICONINFO, MB_SETFOREGROUND = 0x10, 0x40, 0x10000
        ctypes.windll.user32.MessageBoxW(None, text, title, (MB_ICONERROR if error else MB_ICONINFO) | MB_SETFOREGROUND)
    except Exception:  # noqa: BLE001
        pass


def fatal(exc: BaseException, show_dialog: bool = True) -> Path:
    """Record a crash that stops the app and tell the user where the details are."""
    p = setup()
    logging.getLogger("dronedog").critical("DroneDog stopped because of an unexpected error\n%s",
                                           "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    if show_dialog:
        message_box("DroneDog",
                    "DroneDog hit an unexpected problem and has to close.\n\n"
                    f"{type(exc).__name__}: {exc}\n\n"
                    f"The details were saved to:\n{p}\n\n"
                    "Please send that file to whoever supports DroneDog.")
    return p
