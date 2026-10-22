"""Entry point for the packaged DroneDog.exe (PyInstaller)."""
import multiprocessing
import sys

from dronedog.ui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
