"""Display decimation for columnar frames (C3).  Display only: never used for analysis.

Bucketed min/max: the frames are split into ``max_points // 3`` buckets; from each
bucket we keep the first frame and the frames holding the min and max of each
"peak" column (altitude, speed, ...), so spikes stay visible on charts and the
track keeps its shape.  The first and last frames are always kept.
"""

from __future__ import annotations

PEAK_COLUMNS = ("alt_rel_m", "speed_h_ms", "vel_d", "battery_v")


def frame_count(frames: dict) -> int:
    for v in frames.values():
        return len(v)
    return 0


def select_indices(frames: dict, max_points: int, peak_columns=PEAK_COLUMNS) -> list[int]:
    n = frame_count(frames)
    if max_points is None or max_points <= 0 or n <= max_points:
        return list(range(n))
    cols = [frames[c] for c in peak_columns if c in frames]
    # min/max of several columns often coincide, so ~5 kept frames per bucket is typical
    buckets = max(1, (max_points - 2) // 5)
    keep = {0, n - 1}
    size = n / buckets
    for b in range(buckets):
        lo = int(b * size)
        hi = min(n, int((b + 1) * size))
        if lo >= hi:
            continue
        keep.add(lo)
        for col in cols:
            best_min = best_max = None
            imin = imax = None
            for i in range(lo, hi):
                v = col[i]
                if v is None:
                    continue
                if best_min is None or v < best_min:
                    best_min, imin = v, i
                if best_max is None or v > best_max:
                    best_max, imax = v, i
            if imin is not None:
                keep.add(imin)
                keep.add(imax)
    out = sorted(keep)
    if len(out) > max_points:          # pathological data: thin evenly, keep the ends
        step = len(out) / max_points
        out = sorted({out[int(i * step)] for i in range(max_points - 1)} | {n - 1})
    return out


def decimate(frames: dict, max_points: int) -> tuple[dict, dict]:
    """Return (decimated_frames, info)."""
    n = frame_count(frames)
    idx = select_indices(frames, max_points)
    if len(idx) == n:
        return frames, {"total": n, "returned": n, "method": "none"}
    out = {k: [v[i] for i in idx] for k, v in frames.items()}
    return out, {"total": n, "returned": len(idx), "method": "bucket-minmax"}
