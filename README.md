# Video Watermark Removal Skill

A [Codex](https://github.com/openai/codex) skill that **detects and removes static
watermarks** — corner logos, channel IDs, text overlays — from video files, and
rebuilds a clean copy with FFmpeg.

Point it at a video and it will:

1. **Locate the watermark automatically** — no manual pixel counting, no guessing
   crop rectangles in a GUI.
2. **Inpaint the region** with FFmpeg's `delogo` filter.
3. **Verify the result** by re-running detection on the output, so you get a
   machine-checked pass/fail instead of hoping it worked.

The original file is never touched — the cleaned video is always written to a
new path.

---

## How detection works

Finding the watermark is the hard part; removing it is a single filter call.
The detector uses one simple, effective observation:

> A background pixel eventually gets dark in *some* frame (the scene changes,
> the camera pans, something passes in front). A watermark pixel **never** does
> — the overlay is painted on top of every frame.

So `detect_watermark.py`:

1. Probes the video for its dimensions and duration.
2. Grabs `N` frames (default 18) evenly spread across the whole video.
3. Computes the **per-pixel minimum brightness** across those samples.
4. Thresholds that minimum map (default `> 120`) — the surviving pixels are the
   ones that stayed bright in *every* sampled frame, i.e. the watermark.

Two real-world problems had to be solved on top of that:

- **Isolated specks.** Hot pixels, static stars, and compression grain also stay
  bright, and a single stray pixel 800px from the logo balloons the bounding box
  from 214x48 to 1075x813. The mask is therefore split into **connected
  components** (8-connectivity), small blobs under `--min-area` (default 12px)
  are dropped, and the rest are **clustered by proximity** (`--gap`, default
  64px) starting from the largest blob. Only the main cluster forms the
  watermark box; everything else is reported as `ignored_components`.
- **Static bright scenes.** If the picture itself barely changes (a screencast,
  a static slide), the persistent-bright mask covers most of the frame and
  "removal" would blur the whole video. Two guards refuse to run instead:

  - the mask covers more than 20% of the frame → detection fails with an
    explanatory note;
  - the resulting `delogo` rectangle would cover more than 25% of the frame →
    `remove_watermark.py` aborts **before encoding anything**.

Detection result (JSON) includes `bbox`, `delogo` (with margin), the number of
components merged/ignored, and the raw mask fraction, so a human or an agent can
sanity-check it.

## Requirements

| Tool | Version | Notes |
|------|---------|-------|
| FFmpeg | any modern build | needs `ffmpeg` and `ffprobe` on `PATH` |
| Python | 3.8+ | scripts only use the stdlib plus two packages |
| numpy | any | frame math |
| Pillow | any | frame decoding |

No compiled extras, no scipy, no cloud services — the whole pipeline runs
locally in a few seconds of overhead plus one encode pass.

## Installation

### As a Codex skill

Copy (or clone) the `video-watermark` folder into your Codex skills directory:

```bash
git clone https://github.com/xingguangYan/codex-skill-video-watermark.git
cp -r codex-skill-video-watermark/video-watermark ~/.codex/skills/
```

On Windows the destination is usually
`C:\Users\<you>\.codex\skills\video-watermark`.

Then ask Codex naturally:

- *"remove the watermark from the top-left of this video"*
- *"strip the corner logo from download.mp4 and save it next to the original"*

The skill activates on video watermark-removal requests and follows the workflow
described in `SKILL.md`.

### As standalone scripts

Clone the repo and use the scripts directly — no Codex needed:

```bash
git clone https://github.com/xingguangYan/codex-skill-video-watermark.git
cd codex-skill-video-watermark/video-watermark/scripts
```

## Usage

### Detect only

```bash
python detect_watermark.py INPUT.mp4
```

```
watermark bbox      : x=41 y=45 w=214 h=47
delogo (with margin): x=33 y=37 w=230 h=63
ffmpeg command      : ffmpeg -y -i INPUT -vf "delogo=x=33:y=37:w=230:h=63" -c:v libx264 -crf 18 -preset medium -c:a copy OUTPUT
```

Add `--json` for machine-readable output:

```bash
python detect_watermark.py INPUT.mp4 --json
```

```json
{
  "input": "INPUT.mp4",
  "width": 1920,
  "height": 1080,
  "duration": 94.27,
  "frames_sampled": 18,
  "threshold": 120.0,
  "mask_fraction": 0.0018,
  "found": true,
  "bbox": { "x": 41, "y": 45, "w": 214, "h": 47 },
  "delogo": { "x": 33, "y": 37, "w": 230, "h": 63 },
  "components": 8,
  "ignored_components": 0,
  "pixels": 3725
}
```

Exit code is `0` when a watermark was found, `1` when not found, `2` on error —
handy for scripting.

### Detect + remove + verify in one command

```bash
python remove_watermark.py INPUT.mp4 OUTPUT.mp4
```

This runs detection, applies `delogo` with a margin, and re-runs detection on
the output to confirm nothing persistent remains:

```
+ ffmpeg -y -i INPUT.mp4 -vf delogo=x=33:y=37:w=230:h=63 -c:v libx264 -crf 18 -preset medium -c:a copy OUTPUT.mp4
...
{
  "input": "INPUT.mp4",
  "output": "OUTPUT.mp4",
  "delogo": { "x": 33, "y": 37, "w": 230, "h": 63 },
  "verified": true
}
verified: no persistent watermark region remains in the output
```

### Manual override

When auto-detection fails (or you already know the rectangle), skip it entirely:

```bash
python remove_watermark.py INPUT.mp4 OUTPUT.mp4 --x 33 --y 37 --w 230 --h 63
```

All four of `--x --y --w --h` must be given together.

### All options

| Option | Script | Default | Meaning |
|--------|--------|---------|---------|
| `--frames` | both | 18 | frames sampled across the video for detection |
| `--threshold` | both | 120 | persistent-brightness level, 0–255 |
| `--margin` | both | 8 | padding added around the bbox for `delogo` |
| `--gap` | both | 64 | max distance (px) between components merged into one watermark |
| `--min-area` | both | 12 | drop persistent blobs smaller than this (px) |
| `--region X0,Y0,X1,Y1` | detect | — | restrict the search to a rectangle |
| `--crf` | remove | 18 | x264 quality (lower = better/bigger) |
| `--preset` | remove | medium | x264 preset |
| `--no-verify` | remove | off | skip the post-removal detection check |
| `--json` | detect | off | machine-readable output |
| `--x --y --w --h` | remove | — | manual `delogo` rectangle, skips detection |

## Encoding notes

- The video stream must be re-encoded (the filtered frames have to be written);
  the **audio stream is copied untouched**.
- `-crf 18` with the `medium` preset is visually transparent on most source
  material. Prefer smaller files? Raise `--crf` to 20–23.
- Handling a video with **several static watermarks**: run `detect_watermark.py`
  once per overlay (using `--region` to isolate areas) and chain the rectangles
  in one FFmpeg command:
  ```bash
  ffmpeg -y -i INPUT.mp4 \
    -vf "delogo=x=33:y=37:w=230:h=63,delogo=x=1600:y=1000:w=280:h=60" \
    -c:v libx264 -crf 18 -preset medium -c:a copy OUTPUT.mp4
  ```

## Limitations and troubleshooting

- **Moving or animated watermarks** (scrolling tickers, pulsing logos) are not
  handled — `delogo` inpaints one fixed rectangle.
- **Dark or semi-transparent overlays** can fall below the brightness threshold.
  Lower `--threshold` (e.g. 90–100) and re-run.
- **Long static scenes** (screencasts, slides) defeat auto-detection by design —
  the guards trip rather than blur the frame. Either feed a span with changing
  content or pass a manual rectangle.
- **Very short clips** give the detector little to compare against; prefer
  running detection on the full video (the detected box is static, so it applies
  to any cut of it).
- `delogo` interpolates from the edges of its rectangle, so the box needs a
  couple of pixels of clean margin around the watermark — that is what
  `--margin` is for. Increase it if a faint fringe remains after removal.
- The verification step checks the *same* statistical signature on the output —
  it is strong evidence the overlay is gone, not a human eyeballing the frames.
  For final QA, crop the corner from before/after:
  ```bash
  ffmpeg -v error -i INPUT.mp4 -ss 30 -vframes 1 -vf "crop=420:140:10:20" before.png
  ffmpeg -v error -i OUTPUT.mp4 -ss 30 -vframes 1 -vf "crop=420:140:10:20" after.png
  ```

## Project layout

```
video-watermark/
├── SKILL.md                 Codex skill instructions (workflow + constraints)
├── README.md                this file
└── scripts/
    ├── detect_watermark.py  locate the watermark (per-pixel min + clustering)
    └── remove_watermark.py  detect -> delogo -> verify pipeline
```

## License

MIT — see `LICENSE`.
