#!/usr/bin/env python3
"""Remove a static watermark from a video in one pass.

Detects the watermark with detect_watermark.detect, applies FFmpeg's delogo
filter, then re-runs detection on the result to confirm the overlay is gone.
Nothing is edited in place: the output is always a new file.
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detect_watermark import detect, probe  # noqa: E402

# Refuse to delogo a box covering more than a quarter of the frame; that is
# almost certainly a misdetection, not a watermark.
MAX_DELOGO_FRACTION = 0.25


def run(cmd):
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd]).returncode


def remove(input_path, output_path, frames=18, threshold=120.0, margin=8,
           gap=64, min_area=12, crf=18, preset="medium", manual=None, verify=True):
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise ValueError("output path must differ from the input path")

    if manual:
        x, y, w, h = manual
        result = {"found": True, "delogo": {"x": x, "y": y, "w": w, "h": h}}
    else:
        result = detect(input_path, frames=frames, threshold=threshold, margin=margin,
                        gap=gap, min_area=min_area)
        if not result.get("found"):
            raise RuntimeError(
                "no watermark detected; pass --x/--y/--w/--h to remove a known region "
                "or follow the detector's note to improve detection")

    x, y, w, h = (result["delogo"]["x"], result["delogo"]["y"],
                  result["delogo"]["w"], result["delogo"]["h"])
    info = probe(input_path)
    box_fraction = (w * h) / float(info["width"] * info["height"])
    if box_fraction > MAX_DELOGO_FRACTION:
        raise RuntimeError(
            f"delogo rectangle {w}x{h} covers {box_fraction:.0%} of the frame; that "
            "looks like a misdetection. Aborting before any encode. Pass --x/--y/--w/--h "
            "with the true watermark bounds")

    vf = f"delogo=x={x}:y={y}:w={w}:h={h}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf,
           "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
           "-c:a", "copy", output_path]
    code = run(cmd)
    if code != 0:
        raise RuntimeError(f"ffmpeg failed with exit code {code}")

    summary = {"input": input_path, "output": output_path,
               "delogo": {"x": x, "y": y, "w": w, "h": h}, "verified": None}
    if verify:
        check = detect(output_path, frames=frames, threshold=threshold, margin=margin,
                       gap=gap, min_area=min_area)
        summary["verified"] = not check.get("found")
        if check.get("found"):
            summary["remaining_bbox"] = check["bbox"]
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="path to the watermark video")
    ap.add_argument("output", help="path for the cleaned video (must not equal input)")
    ap.add_argument("--frames", type=int, default=18, help="frames sampled for detection (default: 18)")
    ap.add_argument("--threshold", type=float, default=120.0, help="detection brightness threshold (default: 120)")
    ap.add_argument("--margin", type=int, default=8, help="delogo padding in pixels (default: 8)")
    ap.add_argument("--gap", type=int, default=64, help="component clustering distance (default: 64)")
    ap.add_argument("--min-area", type=int, default=12, help="minimum component size (default: 12)")
    ap.add_argument("--crf", type=float, default=18, help="x264 quality, lower is better (default: 18)")
    ap.add_argument("--preset", default="medium", help="x264 preset (default: medium)")
    ap.add_argument("--x", type=int, help="manual delogo x (skips detection)")
    ap.add_argument("--y", type=int, help="manual delogo y (skips detection)")
    ap.add_argument("--w", type=int, help="manual delogo width (skips detection)")
    ap.add_argument("--h", type=int, help="manual delogo height (skips detection)")
    ap.add_argument("--no-verify", action="store_true", help="skip post-removal verification")
    args = ap.parse_args()

    manual = None
    if {args.x, args.y, args.w, args.h} != {None}:
        missing = [n for n in ("x", "y", "w", "h") if getattr(args, n) is None]
        if missing:
            ap.error(f"--x/--y/--w/--h must be given together (missing: {', '.join(missing)})")
        manual = [args.x, args.y, args.w, args.h]

    try:
        summary = remove(args.input, args.output, args.frames, args.threshold,
                         args.margin, args.gap, args.min_area, args.crf, args.preset,
                         manual, verify=not args.no_verify)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, indent=2))
    if summary["verified"] is True:
        print("verified: no persistent watermark region remains in the output")
    elif summary["verified"] is False:
        print("warning: a persistent bright region is still detectable; "
              "inspect the result or widen the delogo rectangle", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
