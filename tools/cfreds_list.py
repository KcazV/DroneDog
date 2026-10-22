"""List (or download) CFReDS VTO Labs drone Google Drive folders via gdown.

Usage:
  python cfreds_list.py list <ref> [<ref> ...]      # print file tree, no download
  python cfreds_list.py get  <ref> <outdir>          # download folder
"""
import json
import sys
from pathlib import Path

import gdown

FOLDERS = json.loads((Path(__file__).parent / "cfreds_folders.json").read_text())


def main():
    cmd, *rest = sys.argv[1:]
    if cmd == "list":
        for ref in rest:
            fid = FOLDERS[ref]["id"]
            try:
                files = gdown.download_folder(id=fid, skip_download=True, quiet=True)
                print(f"== {ref} {FOLDERS[ref]['model']} ({len(files)} files)")
                for f in files:
                    print("   ", f.path, f.id)
            except Exception as e:  # noqa: BLE001
                print(f"ERR {ref}: {type(e).__name__}: {e}")
    elif cmd == "get":
        ref, out = rest
        gdown.download_folder(id=FOLDERS[ref]["id"], output=out, quiet=False, resume=True)


if __name__ == "__main__":
    main()

