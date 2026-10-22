#!/usr/bin/env python3
"""DroneDog DAT reference decoder: thin CLI wrapper.

The implementation lives in dronedog/adapters/dat_ref_impl.py (the dat_ref_py
adapter). It is loaded by file path so this tool works without importing the
dronedog package (which pulls in pytsk3 etc.).

    python tools/dat_ref.py FLY123.DAT --csv out.csv
    python tools/dat_ref.py FLY123.DAT --records
    python tools/dat_ref.py FLY123.DAT --json result.json
    python tools/dat_ref.py FLY123.DAT --detect
    python tools/dat_ref.py FLY123.DAT --full --records   # no numpy fast path
"""
import importlib.util
import os
import sys

_IMPL = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "dronedog",
                     "adapters", "dat_ref_impl.py")
_spec = importlib.util.spec_from_file_location("dronedog_dat_ref_impl", _IMPL)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["dronedog_dat_ref_impl"] = _mod
_spec.loader.exec_module(_mod)
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})

if __name__ == "__main__":
    sys.exit(_mod.main())
