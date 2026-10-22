"""dronedog.paths: portable vs installed locations, overrides, and the case_new default."""
import sys

import pytest

from dronedog import paths


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for f in (paths.is_portable, paths.data_dir, paths.default_cases_dir):
        f.cache_clear()
    monkeypatch.delenv("DRONEDOG_DATA", raising=False)
    monkeypatch.delenv("DRONEDOG_CASES", raising=False)
    yield
    for f in (paths.is_portable, paths.data_dir, paths.default_cases_dir):
        f.cache_clear()


def test_source_run_is_not_portable(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    assert not paths.is_portable()
    assert paths.data_dir() == tmp_path / "lad" / "DroneDog"
    assert paths.default_cases_dir().name == "DroneDog Cases"


def test_frozen_writable_folder_is_portable(tmp_path, monkeypatch):
    exe = tmp_path / "stick" / "DroneDog" / "DroneDog.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert paths.is_portable()
    assert paths.data_dir() == exe.parent / "DroneDogData"
    assert paths.default_cases_dir() == exe.parent / "Cases"
    assert paths.describe()["portable"] is True


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("DRONEDOG_DATA", str(tmp_path / "d"))
    monkeypatch.setenv("DRONEDOG_CASES", str(tmp_path / "c"))
    assert paths.data_dir() == tmp_path / "d"
    assert paths.default_cases_dir() == tmp_path / "c"


def test_case_new_requires_a_location(tmp_path, monkeypatch):
    """D12: no default case folder; the examiner must choose one."""
    import pytest
    from dronedog.service import Service
    monkeypatch.setenv("DRONEDOG_CASES", str(tmp_path / "cases"))
    s = Service()
    with pytest.raises(ValueError):
        s.case_new({"name": "Class lab 1", "examiner": "Z", "case_number": "LAB/1"})
    info = s.case_new({"name": "Class lab 1", "examiner": "Z", "case_number": "LAB/1", "location": str(tmp_path / "picked")})
    assert info["root"].replace("\\", "/").endswith("picked/LAB_1")
    s._close()
