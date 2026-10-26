---
name: video-watermark
description: Detect and remove static video watermarks (corner logos, text overlays) by locating the fixed overlay across frames and inpainting it with the FFmpeg delogo filter. Use when a video has a stationary watermark to strip; not for moving overlays or full-frame blur.
---

# Video Watermark Removal

Remove a fixed watermark (logo or text) from a video by auto-locating it, then inpainting with FFmpeg's `delogo` filter.

## Workflow

1. Probe the input with `ffprobe` to confirm it is a real video (codec, resolution, duration). Short clips are fine; nothing is edited in place — output always goes to a new file.
2. Run `scripts/detect_watermark.py` on the input. It samples frames spread across the video, computes the per-pixel minimum brightness across the samples, and reports the bounding box of pixels that stay bright in every frame (the signature of a static, light overlay), plus ready-to-use `delogo` parameters with margin applied.
3. Run the removal with `ffmpeg`:

   ```
   ffmpeg -y -i <input> -vf "delogo=x=<X>:y=<Y>:w=<W>:h=<H>" -c:v libx264 -crf 18 -preset medium -c:a copy <output>
   ```

   Copy the audio stream instead of re-encoding it.

4. Verify by running `scripts/detect_watermark.py` on the output (optionally with `--json`). A successful removal reports no persistent bright region; you can also render before/after crops for a human to eyeball:

   ```
   ffmpeg -v error -i <input> -ss <t> -vframes 1 -vf "crop=<W>:<H>:<X>:<Y>" before.png
   ffmpeg -v error -i <output> -ss <t> -vframes 1 -vf "crop=<W>:<H>:<X>:<Y>" after.png
   ```

Solid color / near-static scenes make auto-detection unreliable; if the report looks wrong, re-run with explicit `--x --y --w --h` bounds or pass a manual `delogo` rectangle.

## Real constraints

- Requires `ffmpeg`, `ffprobe`, and Python 3 with `numpy` and `Pillow`. Verify with the probe step before starting a long encode.
- `delogo` interpolates from the edges of its rectangle: the box must fully cover the watermark plus a few pixels of margin, and it fails on overlays that move, scroll, or cover a huge fraction of the frame.
- Re-encoding the video stream is unavoidable (the filtered frames must be written). `-crf 18` visually preserves quality; raise it if the destination prefers smaller files.
- One watermark per run. Multiple static overlays can be handled by chaining `delogo` filters in one `-vf`, but detect each box first.
