"""Prototype v2: honest measurement of what pixels CAN tell us about a demo flow.

Two signals, measured separately:
  A) cursor_localisation  - can we find the pointer in each frame?
  B) change_regions       - can we localise WHERE the UI changed and cluster it into
                            numbered action steps (this is what the prompt actually needs)?

Deliberately does NOT claim to recover true OS click events: we measure how well a
dwell+change heuristic approximates them, and report false positives honestly.
"""
import json
import sys

import cv2
import numpy as np

CURSOR_PAD = 26          # px masked around the cursor so its own motion is not a "change"
DIFF_THRESHOLD = 22
MIN_REGION_AREA = 220    # px^2 for a changed blob to matter
CLUSTER_RADIUS = 70      # px: merge nearby blobs into one step
MERGE_MAX_GAP = 0.45     # s: blobs further apart in time need a NEW step
STEP_GAP_FRAMES = 18     # frames of quiet that close an action step
DWELL_SPEED = 3.0
DWELL_FRAMES = 3


def find_cursor(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    bright = (gray > 235).astype(np.uint8)
    dark_near = cv2.dilate((gray < 90).astype(np.uint8), np.ones((5, 5), np.uint8))
    cand = cv2.morphologyEx(cv2.bitwise_and(bright, dark_near), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(cand, 8)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (8 <= w <= 46 and 10 <= h <= 56) or area < 40:
            continue
        fill = area / float(w * h)
        if not (0.35 <= fill <= 0.92):
            continue
        ar = h / float(w)
        if not (1.0 <= ar <= 2.4):
            continue
        score = fill + (1.0 - abs(ar - 1.45))
        if best is None or score > best[0]:
            best = (score, (float(x), float(y)))
    return best[1] if best else None


def cursor_valid(gray, pos):
    """Boolean mask: True where pixels are NOT covered by the cursor glyph."""
    valid = np.ones(gray.shape, dtype=bool)
    if pos is None:
        return valid
    x, y = int(pos[0]), int(pos[1])
    h, w = gray.shape
    valid[max(0, y - 8):min(h, y + 46), max(0, x - 10):min(w, x + 34)] = False
    # also drop the last few frames' cursor footprint via the caller (trail boxes)
    return valid


def run(path, log_path=None, video_out=None):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    frames = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(fr)
    cap.release()

    cursors, steps = [], []
    prev = None
    keyframe = None
    last_keyframe_idx = 0
    KEYFRAME_REFRESH = 40   # frames; sliding baseline (~2.7s at 15fps) catches slow drift
    last_step_frame = -999
    still = 0
    last_pos = None
    trail = []
    annotated = []

    for idx, fr in enumerate(frames):
        t = idx / fps
        pos = find_cursor(fr)
        cursors.append(pos)
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        valid = cursor_valid(gray, pos)

        if pos is not None:
            if last_pos is not None:
                sp = ((pos[0] - last_pos[0]) ** 2 + (pos[1] - last_pos[1]) ** 2) ** 0.5
            else:
                sp = 999.0
            still = still + 1 if sp <= DWELL_SPEED else 0
            trail = (trail + [pos])[-10:]
        last_pos = pos

        if prev is not None:
            d = np.abs(gray.astype(np.int16) - prev[0].astype(np.int16)) > DIFF_THRESHOLD
            d &= valid & prev[1]
            # additive: also compare against the last committed keyframe so slow,
            # incremental changes (typing) surface instead of staying sub-threshold
            if keyframe is not None:
                dk = np.abs(gray.astype(np.int16) - keyframe[0].astype(np.int16)) > DIFF_THRESHOLD
                dk &= valid & keyframe[1]
                d = d | dk
            # no MORPH_OPEN: it erases 1-2px text strokes (typed characters)
            d = cv2.morphologyEx(d.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            n, _, stats, cents = cv2.connectedComponentsWithStats(d, 8)
            blobs = []
            for i in range(1, n):
                x, y, w, h, area = stats[i]
                if area < MIN_REGION_AREA:
                    continue
                blobs.append((x, y, w, h, area, cents[i]))
            for x, y, w, h, area, c in blobs:
                merged = False
                for s in steps:
                    if (s["open"] and t - s["last_t"] <= MERGE_MAX_GAP
                            and ((c[0] - s["cx"]) ** 2 + (c[1] - s["cy"]) ** 2) ** 0.5 < CLUSTER_RADIUS):
                        s["x0"] = min(s["x0"], x); s["y0"] = min(s["y0"], y)
                        s["x1"] = max(s["x1"], x + w); s["y1"] = max(s["y1"], y + h)
                        s["last_t"] = round(t, 2)
                        s["area"] = max(s["area"], int(area))
                        # weighted centre
                        s["cx"] = (s["cx"] + c[0]) / 2.0
                        s["cy"] = (s["cy"] + c[1]) / 2.0
                        s["frames"] += 1
                        merged = True
                        break
                if merged:
                    continue
                steps.append({
                    "x0": x, "y0": y, "x1": x + w, "y1": y + h,
                    "cx": c[0], "cy": c[1],
                    "start_t": round(t, 2), "last_t": round(t, 2),
                    "area": int(area), "frames": 1, "open": True,
                    "cursor_parked": still >= DWELL_FRAMES,
                    "cursor_near": bool(pos is not None and ((pos[0] - c[0]) ** 2 + (pos[1] - c[1]) ** 2) ** 0.5 < 90),
                })
            # close stale steps
            for st in steps:
                if st["open"] and t - st["last_t"] > MERGE_MAX_GAP:
                    st["open"] = False
            if blobs:
                last_step_frame = idx

        if video_out is not None:
            out = fr.copy()
            for j in range(1, len(trail)):
                cv2.line(out, (int(trail[j - 1][0]), int(trail[j - 1][1])),
                         (int(trail[j][0]), int(trail[j][1])), (60, 60, 255), 3, cv2.LINE_AA)
            for i, s in enumerate(steps):
                if s["start_t"] <= t <= s["last_t"] + 0.5:
                    cv2.rectangle(out, (s["x0"], s["y0"]), (s["x1"], s["y1"]), (0, 220, 255), 2)
                    cv2.putText(out, f"STEP {i + 1}", (s["x0"], max(14, s["y0"] - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
                    cv2.putText(out, f"STEP {i + 1}", (s["x0"], max(14, s["y0"] - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 255), 2, cv2.LINE_AA)
            if pos is not None:
                cv2.circle(out, (int(pos[0]), int(pos[1])), 24, (0, 200, 255), 2, cv2.LINE_AA)
            cv2.rectangle(out, (0, 0), (300, 32), (0, 0, 0), -1)
            cv2.putText(out, f"{int(t // 60):02d}:{t % 60:05.2f}  steps {len(steps)}", (8, 23),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
            annotated.append(out)
        prev = (gray, valid)
        if keyframe is None or idx - last_keyframe_idx >= KEYFRAME_REFRESH:
            keyframe = (gray.copy(), valid.copy())
            last_keyframe_idx = idx

    for s in steps:
        s["open"] = False

    found = sum(1 for c in cursors if c is not None)
    result = {
        "frames": len(frames), "fps": fps, "duration_s": round(len(frames) / fps, 2),
        "cursor_recall": round(found / max(1, len(frames)), 3),
        "steps": [{k: (round(float(v), 2) if isinstance(v, (float, np.floating)) else
                       (int(v) if isinstance(v, (int, np.integer)) else v))
                   for k, v in s.items() if k != "open"} for s in steps],
    }
    if video_out is not None and annotated:
        h, w = annotated[0].shape[:2]
        vw = cv2.VideoWriter(video_out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        for a in annotated:
            vw.write(a)
        vw.release()
    if log_path:
        json.dump(result, open(log_path, "w"), indent=2)
    return result


if __name__ == "__main__":
    res = run(sys.argv[1] if len(sys.argv) > 1 else "demo.mp4",
              log_path=sys.argv[2] if len(sys.argv) > 2 else "steps.json",
              video_out=sys.argv[3] if len(sys.argv) > 3 else "steps.mp4")
    print("frames", res["frames"], "fps", res["fps"], "cursor_recall", res["cursor_recall"])
    print(f"{'#':>2} {'t0':>6} {'t1':>6} {'bbox':>28} {'area':>7} parked near")
    for i, s in enumerate(res["steps"], 1):
        print(f"{i:>2} {s['start_t']:>6} {s['last_t']:>6} "
              f"{str((s['x0'], s['y0'], s['x1'], s['y1'])):>28} {s['area']:>7} "
              f"{str(s['cursor_parked']):>6} {s['cursor_near']}")
