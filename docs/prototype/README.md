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

---

## Feature 2 deep analysis scripts

Used to produce the numbers in [`../FEATURE-2-ANALYSIS.md`](../FEATURE-2-ANALYSIS.md).

| File | Purpose |
|---|---|
| `make_blind.py` | Builds `blind.mp4`: a demo containing action classes that leave **no visible trace** at 1 fps (toggle on→off, no-op click on a disabled button, identical-field focus). |
| `measure.py` | For each action class, counts how many sampled frames land inside its evidence window, at every rate the app allows (`0.5`–`3.0` fps) plus higher ones for contrast. |
| `probability.py` | `P(caught) = duration × fps` for realistic UI action durations; also the frame count the annotation pipeline sees at 15 fps. |
| `ordering.py` | When two actions fall inside one sampling interval, at what gap does order become unrecoverable. |
| `cost.py` | Token and USD cost of raising fps vs adding an action log, using this app's registry prices. |

Key measured results:

```
pointer recall on blind.mp4          : 597/600 = 99.5%
dwell events detected                : 6 / 6 real actions
actions recovered that 1 fps cannot  : net-zero toggle, no-op click
action log cost, 5-min demo           : +1.6% tokens (~1,350 tokens)
raising 1 fps -> 3 fps instead        : +200% tokens, still no order/target gain
```
