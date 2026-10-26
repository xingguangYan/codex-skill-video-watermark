#!/usr/bin/env python3
"""Locate a static, light watermark in a video.

The detector samples frames spread across the video and computes the
per-pixel *minimum* brightness across those samples. Background pixels
eventually turn dark in some frame; a persistent bright overlay never does,
so the set of pixels that stay bright in every sample traces the watermark.

The persistent-bright mask is then split into connected components and the
components are clustered by proximity: a watermark is a group of nearby
blobs (characters, icon parts), while isolated specks (hot pixels, static
stars, grain) sit far from that group and are ignored.
"""

import argparse
import io
import json
import subprocess
import sys
from collections import deque

import numpy as np
from PIL import Image

# A persistent-bright mask covering more than this fraction of the frame means
# the scene itself is static and bright, not a small overlay; auto-detection is
# unreliable in that case and must fail loudly instead of delogo-ing the frame.
MAX_MASK_FRACTION = 0.20


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration",
         "-of", "default=noprint_wrappers=1", path],
        capture_output=True, text=True)
    fields = {}
    for line in out.stdout.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k] = v
    try:
        width, height = int(fields["width"]), int(fields["height"])
    except (KeyError, ValueError):
        raise RuntimeError(f"could not read video dimensions for {path}")
    try:
        duration = float(fields["duration"])
    except (KeyError, ValueError):
        duration = None
    return {"width": width, "height": height, "duration": duration}


def grab_frame(path, t):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-vframes", "1",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True)
    if not p.stdout:
        raise RuntimeError(f"could not grab a frame at t={t:.2f}s from {path}")
    return np.asarray(Image.open(io.BytesIO(p.stdout)).convert("L"), dtype=np.float32)


def sample_times(duration, count):
    count = max(1, count)
    if count == 1:
        return [min(1.0, duration / 2.0)]
    return [duration * i / (count + 1) for i in range(1, count + 1)]


def label_components(mask):
    """8-connectivity components of a boolean mask.

    Returns a list of dicts: area plus an inclusive bounding box.
    """
    remaining = set(map(tuple, np.argwhere(mask)))
    comps = []
    while remaining:
        start = remaining.pop()
        queue = deque([start])
        area = 0
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")
        while queue:
            y, x = queue.popleft()
            area += 1
            x0, x1 = min(x0, x), max(x1, x)
            y0, y1 = min(y0, y), max(y1, y)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy or dx:
                        nxt = (y + dy, x + dx)
                        if nxt in remaining:
                            remaining.remove(nxt)
                            queue.append(nxt)
        comps.append({"area": area, "x0": int(x0), "y0": int(y0),
                      "x1": int(x1), "y1": int(y1)})
    return comps


def cluster_components(comps, gap):
    """Grow one cluster from the largest component, absorbing any component
    whose bounding box lies within `gap` pixels of the cluster's current box.

    Returns (cluster, ignored): the absorbed components and everything left
    out (isolated specks or a second, separate overlay).
    """
    if not comps:
        return [], []
    remaining = sorted(range(len(comps)), key=lambda i: -comps[i]["area"])
    cluster = [remaining[0]]
    rest = set(remaining[1:])
    changed = True
    while changed:
        changed = False
        bx0 = min(comps[i]["x0"] for i in cluster)
        by0 = min(comps[i]["y0"] for i in cluster)
        bx1 = max(comps[i]["x1"] for i in cluster)
        by1 = max(comps[i]["y1"] for i in cluster)
        for i in list(rest):
            c = comps[i]
            dx = max(0, bx0 - c["x1"], c["x0"] - bx1)
            dy = max(0, by0 - c["y1"], c["y0"] - by1)
            if max(dx, dy) <= gap:
                cluster.append(i)
                rest.discard(i)
                changed = True
    return cluster, sorted(rest)


def detect(path, frames=18, threshold=120.0, region=None, margin=8,
           gap=64, min_area=12):
    info = probe(path)
    width, height = info["width"], info["height"]
    duration = info["duration"]
    if duration is None or duration <= 0:
        raise RuntimeError(f"could not read a valid video duration for {path}")

    times = sample_times(duration, frames)
    try:
        stack = np.stack([grab_frame(path, t) for t in times])
    except RuntimeError as exc:
        raise RuntimeError(f"failed to sample frames: {exc}")
    pmin = stack.min(axis=0)

    if region:
        x0, y0, x1, y1 = region
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(int(x1), width), min(int(y1), height)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("--region is empty or outside the frame")
        search, ox, oy = pmin[y0:y1, x0:x1], x0, y0
    else:
        search, ox, oy = pmin, 0, 0

    mask = search > threshold
    fraction = float(mask.mean())
    base = {
        "input": path,
        "width": width, "height": height,
        "duration": duration,
        "frames_sampled": len(times),
        "threshold": threshold,
        "mask_fraction": fraction,
    }
    if fraction > MAX_MASK_FRACTION:
        return {**base, "found": False,
                "note": "persistent bright area covers too much of the frame; the scene "
                        "is too static for auto-detection. Sample the whole video (not a "
                        "clip), raise --threshold, or pass a manual --region or "
                        "--x/--y/--w/--h rectangle"}

    comps = label_components(mask)
    comps = [c for c in comps if c["area"] >= min_area]
    cluster, ignored = cluster_components(comps, gap)
    if not cluster:
        return {**base, "found": False,
                "note": "no persistent bright region found; try a lower --threshold or "
                        "check that the watermark is light and static"}

    bx0 = min(comps[i]["x0"] for i in cluster)
    by0 = min(comps[i]["y0"] for i in cluster)
    bx1 = max(comps[i]["x1"] for i in cluster)
    by1 = max(comps[i]["y1"] for i in cluster)
    bw, bh = bx1 + 1 - bx0, by1 + 1 - by0
    box_fraction = (bw * bh) / float(width * height)
    if box_fraction > MAX_MASK_FRACTION:
        return {**base, "found": False,
                "note": f"detected cluster spans {bw}x{bh} ({box_fraction:.0%} of the "
                        "frame); that is not a corner-style watermark. Pass a manual "
                        "--region or --x/--y/--w/--h rectangle"}

    gx, gy = ox + bx0, oy + by0
    px = max(0, gx - margin)
    py = max(0, gy - margin)
    pw = min(width - px, bw + 2 * margin)
    ph = min(height - py, bh + 2 * margin)

    return {
        **base,
        "found": True,
        "bbox": {"x": gx, "y": gy, "w": bw, "h": bh},
        "delogo": {"x": px, "y": py, "w": pw, "h": ph},
        "components": len(cluster),
        "ignored_components": len(ignored),
        "pixels": int(sum(comps[i]["area"] for i in cluster)),
    }


def render_report(result):
    if not result["found"]:
        return "no watermark detected\nnote: " + result.get("note", "")
    d = result["delogo"]
    b = result["bbox"]
    lines = [
        f"watermark bbox      : x={b['x']} y={b['y']} w={b['w']} h={b['h']}",
        f"delogo (with margin): x={d['x']} y={d['y']} w={d['w']} h={d['h']}",
        f"ffmpeg command      : ffmpeg -y -i INPUT -vf "
        f"\"delogo=x={d['x']}:y={d['y']}:w={d['w']}:h={d['h']}\" "
        f"-c:v libx264 -crf 18 -preset medium -c:a copy OUTPUT",
    ]
    return "\n".join(lines)


def parse_region(value):
    parts = [int(v) for v in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--region expects x0,y0,x1,y1")
    return parts


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="path to the video file")
    ap.add_argument("--frames", type=int, default=18,
                    help="number of frames to sample across the video (default: 18)")
    ap.add_argument("--threshold", type=float, default=120.0,
                    help="persistent-brightness threshold, 0-255 (default: 120)")
    ap.add_argument("--region", type=parse_region, metavar="X0,Y0,X1,Y1",
                    help="restrict the search to this rectangle")
    ap.add_argument("--margin", type=int, default=8,
                    help="pixels of padding added around the bbox for delogo (default: 8)")
    ap.add_argument("--gap", type=int, default=64,
                    help="max distance between components merged into one watermark "
                         "cluster, in pixels (default: 64)")
    ap.add_argument("--min-area", type=int, default=12,
                    help="ignore persistent components smaller than this many pixels "
                         "(default: 12)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    try:
        result = detect(args.input, args.frames, args.threshold, args.region,
                        args.margin, args.gap, args.min_area)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_report(result))
    return 0 if result["found"] else 1


if __name__ == "__main__":
    sys.exit(main())
