"""Build DroneDog brand assets from the source logo (white background JPEG).

Outputs to assets/brand/: logo_source.jpg, logo.png (transparent, 1024px),
logo_512/256/128/64/32.png, dronedog.ico (multi-size, for the exe + window).
Background removal = flood fill from the border over near-white pixels, so the
white muzzle/eye highlights inside the black outline are kept.
"""
import shutil
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "brand"


def remove_border_white(img: Image.Image, thresh: int = 235) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    seen = bytearray(w * h)
    q = deque((x, y) for x in range(w) for y in (0, h - 1))
    q.extend((x, y) for y in range(h) for x in (0, w - 1))
    mask = Image.new("L", (w, h), 255)
    mp = mask.load()
    while q:
        x, y = q.popleft()
        i = y * w + x
        if seen[i]:
            continue
        seen[i] = 1
        r, g, b, _ = px[x, y]
        if min(r, g, b) < thresh:
            continue
        mp[x, y] = 0
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx]:
                q.append((nx, ny))
    mask = mask.filter(ImageFilter.GaussianBlur(1.2))  # soften the cut edge
    img.putalpha(mask)
    return img.crop(img.getbbox())


def main(src: str):
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, OUT / "logo_source.jpg")
    img = remove_border_white(Image.open(src))
    side = max(img.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    sq.resize((1024, 1024), Image.LANCZOS).save(OUT / "logo.png", optimize=True)
    for s in (512, 256, 128, 64, 32):
        sq.resize((s, s), Image.LANCZOS).save(OUT / f"logo_{s}.png", optimize=True)
    sq.save(OUT / "dronedog.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ok", img.size, "->", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main(sys.argv[1])
