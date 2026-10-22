"""Desktop host: app data under dronedog.paths (portable mode), crash log."""
import logging

from dronedog.ui import crashlog


def test_crash_log_writes_to_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(crashlog, "_path", tmp_path / "logs" / "dronedog.log")
    monkeypatch.setattr(crashlog, "_handler", None)
    p = crashlog.setup()
    try:
        raise ZeroDivisionError("boom")
    except ZeroDivisionError as e:
        crashlog.fatal(e, show_dialog=False)
    for h in logging.getLogger("dronedog").handlers:
        h.flush()
    text = p.read_text(encoding="utf-8")
    assert "ZeroDivisionError: boom" in text and "Traceback" in text
    for name in ("dronedog", "pywebview"):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if getattr(h, "baseFilename", "") == str(p):
                lg.removeHandler(h)
                h.close()


def test_app_data_paths_follow_dronedog_paths(tmp_path, monkeypatch):
    from dronedog import paths
    from dronedog.ui.api import RecentCases
    from dronedog.ui.mbtiles import default_basemap_dir
    from dronedog.ui.online_tiles import default_cache_dir
    monkeypatch.setenv("DRONEDOG_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("DRONEDOG_BASEMAPS", raising=False)
    paths.data_dir.cache_clear()
    try:
        assert RecentCases().path == tmp_path / "data" / "recent_cases.json"
        assert default_basemap_dir() == tmp_path / "data" / "basemaps"
        assert default_cache_dir() == tmp_path / "data" / "tilecache"
    finally:
        paths.data_dir.cache_clear()
