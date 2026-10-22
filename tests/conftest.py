"""Shared fixtures.

Pipeline/ingest tests feed synthetic DAT bytes to a fake core. The real pure-Python DAT
decoder (dronedog.adapters.dat_ref_impl) would otherwise outrank the fake core and parse
those bytes itself, so it is hidden for every test module except the ones that exercise
real decoding (test_core_*, test_dat_*, test_ingest_corpus).
"""
import sys

import pytest

_REAL_DECODER_MODULES = ("test_core_", "test_dat_", "test_ingest_corpus")


@pytest.fixture(autouse=True)
def _hide_python_dat_decoder(request, monkeypatch):
    name = request.module.__name__.rsplit(".", 1)[-1]
    if not name.startswith(_REAL_DECODER_MODULES):
        monkeypatch.setitem(sys.modules, "dronedog.adapters.dat_ref_impl", None)
    yield
