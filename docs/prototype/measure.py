"""How much of the demo flow survives Gemini's sampling? Measured per action class.

Question that decides Feature 2's value, per action:
  - OCCURRENCE: does any sampled frame land while the action's evidence is on screen?
  - ORDER:      can the sequence be reconstructed from samples alone?
  - TARGET:     can we tell which control was acted on?

All time comparisons are in SECONDS.
"""
import cv2
import numpy as np

SRC = "/home/user/f2/blind.mp4"

# name, evidence window (seconds), what it needs
EVENTS = [
    ("toggle A on then off (net zero)", (1.50, 2.35), "order+target", "flashes on 0.85 s, ends where it started"),
    ("focus Work email", (4.20, 5.10), "order+target", "focus ring on field 1"),
    ("focus Confirm email", (5.10, 6.00), "order+target", "focus ring on field 2 - looks identical"),
    ("no-op click on disabled Save", (7.00, 8.60), "order+target", "nothing changes at all"),
    ("toggle A on", (9.60, 20.0), "order+target", "checkbox A becomes on"),
    ("toggle C on", (12.00, 20.0), "order+target", "checkbox C becomes on"),
]


def frames(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
    cap.release()
    return out, fps


def sample_times(fps_src, n, fps_target):
    step = fps_src / fps_target
    times = []
    k = 0
    while True:
        i = int(round(k * step))
        if i >= n:
            break
        times.append(i / fps_src)
        k += 1
    return times


def changed(prev, cur, thr=18, min_frac=0.0008):
    d = np.abs(cur.astype(np.int16) - prev.astype(np.int16)) > thr
    return d.mean() >= min_frac


if __name__ == "__main__":
    src, fps = frames(SRC)
    n = len(src)
    print(f"source: {n} frames @ {fps:.0f} fps  ({n/fps:.0f}s)\n")

    print("=== coverage vs sampling rate ===")
    print(f"{'rate':>7} {'frames':>7} {'actions w/ evidence sampled':>28}")
    for rate in (0.5, 1.0, 2.0, 3.0, 10.0, 15.0, 30.0):
        ts = sample_times(fps, n, rate)
        covered = sum(1 for _, (lo, hi), _, _ in EVENTS if any(lo <= t <= hi for t in ts))
        print(f"{rate:>6.1f}f {len(ts):>7} {covered:>20}/{len(EVENTS)}")

    print("\n=== per-action frames captured (app allows 0.5-3.0 fps) ===")
    print(f"{'action':36s} {'0.5':>5} {'1.0':>5} {'2.0':>5} {'3.0':>5} {'15':>5}")
    for name, (lo, hi), _, _ in EVENTS:
        row = []
        for rate in (0.5, 1.0, 2.0, 3.0, 15.0):
            ts = sample_times(fps, n, rate)
            row.append(sum(1 for t in ts if lo <= t <= hi))
        print(f"{name:36s} {row[0]:>5} {row[1]:>5} {row[2]:>5} {row[3]:>5} {row[4]:>5}")

    print("\n=== the killer case: net-zero toggle at 1 fps ===")
    ts = sample_times(fps, n, 1.0)
    inside = [t for t in ts if 1.50 <= t <= 2.35]
    print(f"   sampled timestamps near the toggle: {[round(t,2) for t in ts if 0.5 <= t <= 3.5]}")
    print(f"   frames landing inside the 1.50-2.35 s window: {inside}")
    print(f"   source frames inside the window: {int(0.85*fps)} of {n} -> "
          f"{0.85*fps/n*100:.1f}% of the recording carries this evidence")
    if inside:
        print("   NOTE: a sample DOES land inside the window here, so the intermediate")
        print("   'on' state is visible - but only as a bare checkbox, with no way to")
        print("   know it returns to off, or which control was clicked.")
    else:
        print("   NOTE: NO sample lands inside the window -> the action is fully invisible.")

    print("\n=== order recoverability ===")
    for rate in (1.0, 3.0, 15.0, 30.0):
        ts = sample_times(fps, n, rate)
        # last frame where A is on-but-C-off, vs first frame where C is on
        a_only = [t for t in ts if 9.60 <= t < 12.00]
        c_on = [t for t in ts if t >= 12.00]
        ok = bool(a_only) and bool(c_on)
        print(f"   {rate:>4.1f}f: distinguish 'A on' before 'C on': {'YES' if ok else 'NO'}  "
              f"(A-only samples={len(a_only)}, C-on samples={len(c_on)})")
