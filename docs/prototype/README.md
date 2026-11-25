# Prototype scripts (reference only — not shipped)

These are the throwaway scripts used to produce the numbers and images in
[`../FEATURE-PROPOSAL.md`](../FEATURE-PROPOSAL.md). They are **not** part of the app and
are not imported by `app.py`, `models_registry.py`, `schemas.py` or `exporters.py`.

They are kept so the measurements can be re-run and reviewed before implementation.

## Files

| File | Purpose |
|---|---|
| `make_video.py` | Synthesises a 16 s screen recording of a fake SaaS UI with a known ground-truth action script (4 clicks, 2 typing bursts, 1 toast, 1 panel swap). |
| `steps.py` | The annotation prototype: pointer detection, changed-region clustering into numbered action steps, overlay render, action-log JSON. |
| `detector_variants.py` | Stress test of the pointer detector against four cursor styles (white arrow, black arrow, I-beam, small HiDPI arrow). |

## Reproducing

```bash
pip install opencv-python-headless
python make_video.py                      # -> demo.mp4 + gt.json
python steps.py demo.mp4 steps.json steps.mp4
python detector_variants.py               # per-cursor-style recall table
```

## Measured output (on the synthetic fixture)

```
pointer recall  : 1.000 white_arrow / 1.000 black_arrow / 1.000 ibeam / 1.000 tiny_hidpi
                 (single-polarity baseline was 0.929 / 0.771 / 0.775 / 0.988)
action steps    : 13 detected, 13 present in ground truth
throughput      : ~3.9x realtime at 1280x720 @ 15 fps (16 s video -> 4.1 s)
```

Requires no system `ffmpeg`; if an H.264 re-encode is needed,
`imageio-ffmpeg`'s bundled binary works (verified).
