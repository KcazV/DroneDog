"""DroneDog application layer (case store, custody log, ingest, adapters, pipeline).

The Rust core (`dronedog_core`) is optional at import time; see
`dronedog.core_bridge` for the fallback behaviour.
"""

__version__ = "0.2.5"
TOOL_NAME = "dronedog"      # custody log "tool"
PRODUCT_NAME = "DroneDog"   # case.json "tool"
