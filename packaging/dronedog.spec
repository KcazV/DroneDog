# PyInstaller spec for the portable DroneDog folder build.
# Build:  .venv\Scripts\pyinstaller.exe packaging\dronedog.spec --noconfirm
# Output: dist\DroneDog\DroneDog.exe (+ _internal\). Copy the whole folder to run anywhere.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
WEB = ROOT / "dronedog" / "ui" / "web"

# Web UI: everything except dev-only fixtures/mocks.
datas = []
for dirpath, _dirs, files in os.walk(WEB):
    rel = Path(dirpath).relative_to(WEB)
    if rel.parts[:1] == ("dev",):
        continue
    for f in files:
        if f == "dev_mock.js":
            continue
        datas.append((str(Path(dirpath) / f), str(Path("dronedog/ui/web") / rel)))
datas.append((str(ROOT / "assets" / "brand" / "dronedog.ico"), "assets/brand"))
datas.append((str(ROOT / "THIRD_PARTY.md"), "."))
datas += [(str(ROOT / "dronedog" / "data" / f), "dronedog/data") for f in ("places.npz", "PLACES_SOURCE.txt")]

# Enumerate our own modules from disk: collect_submodules() silently drops any module
# whose import fails at build time, which once left dronedog.service out of the exe.
hiddenimports = []
for py in (ROOT / "dronedog").rglob("*.py"):
    rel = py.relative_to(ROOT).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    if "_build" in parts or "__pycache__" in parts:
        continue
    hiddenimports.append(".".join(parts))
binaries = []
for pkg in ("webview", "dronedog_core", "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # package not installed -> skip
        continue
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["pytsk3", "pyewf", "numpy", "sqlite3", "psutil"]   # psutil: ui/netmon.py (D11)

a = Analysis(
    [str(ROOT / "packaging" / "launch_dronedog.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "matplotlib", "IPython", "gdown", "maturin"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DroneDog",
    icon=str(ROOT / "assets" / "brand" / "dronedog.ico"),
    console=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="DroneDog")
