"""Crawl public Google Drive folders (CFReDS VTO Labs) and fetch log-bearing files.

gdown can't fetch legacy "0B..." file ids because they need a resourcekey; the
folder page embeds one per file (window['_DRIVE_ivd']), along with size and mime.
This crawler reads that data recursively and downloads with curl via
drive.usercontent.google.com, verifying the byte count.

Usage:
  python drive_crawl.py index                 # crawl all folders -> corpus/cfreds_index.json
  python drive_crawl.py fetch [DF004 ...]     # download wanted files (resumable)
  python drive_crawl.py plan                  # print what fetch would download + total size
"""
import codecs
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = json.loads((Path(__file__).parent / "cfreds_folders.json").read_text())
INDEX = ROOT / "corpus" / "cfreds_index.json"
OUT = ROOT / "corpus" / "raw" / "cfreds"
FOLDER_MIME = "application/vnd.google-apps.folder"

SKIP = re.compile(r"external|chipoff|chip_off|emmc|goggles|camera|android_physical|tablet|mmcblk0|ic_flash|flash_chip|media_parti", re.I)
IMAGE = re.compile(r"\.(001|e01|img|dd)$", re.I)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def wanted(path: str) -> bool:
    if not IMAGE.search(path) and not path.endswith("mmcblk0"):
        return True  # zips, READMEs, hashes, cfg
    return not SKIP.search(path)


def list_folder(fid: str) -> list[dict]:
    req = urllib.request.Request(f"https://drive.google.com/drive/folders/{fid}", headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
    m = re.search(r"window\['_DRIVE_ivd'\]\s*=\s*'(.*?)';", html, re.S)
    if not m:
        raise RuntimeError(f"no _DRIVE_ivd in folder {fid}")
    data = json.loads(codecs.decode(m.group(1), "unicode_escape"))
    out = []
    for it in data[0] or []:
        s = json.dumps(it)
        keys = re.findall(r'"(0-[A-Za-z0-9_-]{15,30})"', s)
        # it[13] holds byte size for files in the observed layout; fall back to None.
        size = it[13] if len(it) > 13 and isinstance(it[13], int) else None
        out.append({"id": it[0], "name": it[2], "mime": it[3], "resourcekey": keys[0] if keys else None, "size": size})
    return out


def crawl(fid: str, prefix: str = "") -> list[dict]:
    files = []
    for it in list_folder(fid):
        path = f"{prefix}{it['name']}"
        if it["mime"] == FOLDER_MIME:
            files += crawl(it["id"], path + "/")
        else:
            files.append({**it, "path": path})
        time.sleep(0.3)
    return files


def cmd_index():
    idx = {}
    for ref, meta in FOLDERS.items():
        try:
            idx[ref] = {"model": meta["model"], "files": crawl(meta["id"])}
            print(f"{ref} {meta['model']}: {len(idx[ref]['files'])} files", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"ERR {ref}: {e}", flush=True)
    INDEX.write_text(json.dumps(idx, indent=1))


def plan(only=()):
    """`only` items are "DF019" (whole device) or "DF019:substring" (matching paths only)."""
    sel = {}
    for o in only:
        ref, _, sub = o.partition(":")
        sel.setdefault(ref, []).append(sub.lower())
    idx = json.loads(INDEX.read_text())
    for ref, d in idx.items():
        if sel and ref not in sel:
            continue
        for f in d["files"]:
            if not wanted(f["path"]):
                continue
            if sel and not any(s in f["path"].lower() for s in sel[ref]):
                continue
            yield ref, f


def looks_like_html(path: Path) -> bool:
    """Drive answers quota/virus-scan/permission problems with an HTML page and 200 OK."""
    if path.suffix.lower() in (".html", ".htm"):
        return False
    with path.open("rb") as fh:
        head = fh.read(512).lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def cmd_plan():
    tot = 0
    for ref, f in plan():
        tot += f["size"] or 0
        print(f"{ref}\t{(f['size'] or 0)/1e9:8.2f} GB\t{f['path']}")
    print(f"TOTAL {tot/1e9:.1f} GB")


def cmd_fetch(only):
    todo = sorted(plan(set(only)), key=lambda rf: rf[1]["size"] or 0)  # small zips/logs first
    errlog = OUT / "_fetch_errors.log"
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (ref, f) in enumerate(todo, 1):
        dest = OUT / ref / f["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = f["size"]
        if dest.exists() and size and dest.stat().st_size == size:
            continue
        url = f"https://drive.usercontent.google.com/download?id={f['id']}&export=download&confirm=t"
        if f["resourcekey"]:
            url += f"&resourcekey={f['resourcekey']}"
        print(f"[{i}/{len(todo)}] {ref}/{f['path']} ({(size or 0)/1e6:,.0f} MB)", flush=True)
        ok = False
        for attempt in range(8):
            subprocess.run(["curl.exe", "-sSL", "-A", UA, "--retry", "5", "-C", "-",
                            "--speed-time", "120", "--speed-limit", "1000", "-o", str(dest), url])
            have = dest.stat().st_size if dest.exists() else 0
            if have and looks_like_html(dest):
                print("    got an HTML page (quota/permission), retrying later", flush=True)
                dest.unlink()
                have = 0
                time.sleep(60 * (attempt + 1))
                continue
            if size is None or have == size:
                ok = have > 0
                break
            if have > size:  # got an HTML error page or overshoot: restart clean
                dest.unlink()
            time.sleep(15 * (attempt + 1))
        if ok:
            print(f"    ok {dest.stat().st_size:,}", flush=True)
        else:
            with errlog.open("a", encoding="utf-8") as fh:
                fh.write(f"{ref}\t{f['path']}\t{f['id']}\tsize={size}\thave={dest.stat().st_size if dest.exists() else 0}\n")
            print("    FAILED", flush=True)


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["plan"]
    {"index": lambda: cmd_index(), "plan": lambda: cmd_plan(), "fetch": lambda: cmd_fetch(args)}[cmd]()
