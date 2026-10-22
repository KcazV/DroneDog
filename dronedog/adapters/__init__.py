"""Parser adapters (C4). Importing this package registers the built-in adapters."""
from dronedog.adapters.base import (AdapterUnavailable, ParseContext, ParserAdapter, Registry,
                                    flight_source, normalize_result, registry)
from dronedog.adapters.dat_core import DatCoreAdapter
from dronedog.adapters.dat_ref_py import DatRefPyAdapter
from dronedog.adapters.txt_djilog import TxtDjiLogAdapter
from dronedog.adapters.app_tips_json import AppTipsJsonAdapter

if not registry.adapters:
    registry.register(TxtDjiLogAdapter())
    registry.register(DatCoreAdapter())
    registry.register(DatRefPyAdapter())   # wins over dat_core (0.95) when dat_ref_impl imports
    registry.register(AppTipsJsonAdapter())  # kind dji_app_tips (python detector fallback)

__all__ = ["AdapterUnavailable", "ParseContext", "ParserAdapter", "Registry", "registry", "flight_source",
           "normalize_result", "TxtDjiLogAdapter", "DatCoreAdapter", "DatRefPyAdapter", "AppTipsJsonAdapter"]
