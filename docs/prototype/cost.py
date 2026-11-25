"""Cost of each Feature 2 option, using this app's registry prices.

Video tokens follow the documented model:
  - low/medium media resolution: 66-70 tokens per frame
  - high: 258-280 tokens per frame
Gemini's own docs give ~100 tok/s at low and ~300 tok/s at high.
"""
INPUT_PRICE = {
    "gemini-3.5-flash-lite": 0.30,
    "gemini-3.8-flash": 0.75,
    "gemini-4-hydrogen": 1.50,
    "gemini-4-neon": 3.00,
}

TOK_PER_FRAME_LOW, TOK_PER_FRAME_HIGH = 70, 280


def video_tokens(duration_s, fps, per_frame):
    return duration_s * fps * per_frame


print("=== tokens per generation, by duration and fps (HIGH resolution) ===")
print(f"{'video':>8} " + " ".join(f"{f:>10.1f}fps" for f in (1.0, 2.0, 3.0)))
for dur in (60, 180, 300, 600):
    row = [f"{video_tokens(dur, f, TOK_PER_FRAME_HIGH)/1000:>11.0f}k" for f in (1.0, 2.0, 3.0)]
    print(f"{dur:>6}s " + " ".join(row))

print("\n=== USD cost per generation, gemini-3.8-flash ($0.75/M input) ===")
print(f"{'video':>8} " + " ".join(f"{f:>9.1f}fps" for f in (1.0, 2.0, 3.0)))
for dur in (60, 180, 300, 600):
    row = []
    for f in (1.0, 2.0, 3.0):
        tok = video_tokens(dur, f, TOK_PER_FRAME_HIGH)
        row.append(f"${tok/1e6*INPUT_PRICE['gemini-3.8-flash']:>8.3f}")
    print(f"{dur:>6}s " + " ".join(row))

print("\n=== what an action log costs (the cheap half of Feature 2) ===")
# a realistic log line
line = '00:08  CLICK   "Create" button -> toast "Workspace created" appears  (pointer parked 0.4s)'
print(f"  one log line ~ {len(line)} chars ~ {len(line)//4} tokens")
for n_events in (10, 30, 60, 120):
    print(f"  {n_events:>3} events -> ~{n_events*len(line)//4:>5} tokens -> "
          f"${n_events*len(line)//4/1e6*INPUT_PRICE['gemini-3.8-flash']:.5f}"
          f"  ({n_events*len(line)//4 / video_tokens(300,1.0,TOK_PER_FRAME_HIGH)*100:.2f}% of a 5-min 1fps video)")

print("\n=== the comparison that matters ===")
base = video_tokens(300, 1.0, TOK_PER_FRAME_HIGH)
raised = video_tokens(300, 3.0, TOK_PER_FRAME_HIGH)
log = 60 * len(line) // 4
print(f"  5-min demo at 1 fps (today)          : {base/1000:>6.0f}k tokens  ${base/1e6*0.75:.3f}")
print(f"  5-min demo at 3 fps (greedy fps)     : {raised/1000:>6.0f}k tokens  ${raised/1e6*0.75:.3f}  (+{raised/base-1:.0%})")
print(f"  5-min demo at 1 fps + action log     : {(base+log)/1000:>6.1f}k tokens  ${(base+log)/1e6*0.75:.3f}  (+{log/base:.2%})")
print(f"  5-min demo at 1 fps + log + burned-in: {(base+log)/1000:>6.1f}k tokens  ${(base+log)/1e6*0.75:.3f}  (+{log/base:.2%})")
print("\n  => the log is ~30x cheaper than raising fps, and it carries ORDER + TARGET,")
print("     which raising fps still does not give you.")
