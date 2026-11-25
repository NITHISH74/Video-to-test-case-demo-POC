"""Order ambiguity: two state changes within one sampling interval.

If action X and action Y both complete between two consecutive samples, an observer of
sampled frames sees only "before" and "after" - the order is unrecoverable, even though
both effects are visible. This is the case the action log fixes and fps alone cannot.
"""
print("=== two actions inside one sample interval -> order lost ===")
print(f"{'gap between actions':>21} " + " ".join(f"{r:>7.1f}f" for r in (1.0, 2.0, 3.0, 15.0)))
for gap in (0.05, 0.10, 0.20, 0.35, 0.50, 0.75, 1.00, 1.50):
    cells = []
    for rate in (1.0, 2.0, 3.0, 15.0):
        interval = 1.0 / rate
        # if the gap is smaller than the sampling interval, both can fall in one bucket
        resolvable = gap >= interval
        cells.append("order OK" if resolvable else "LOST")
    print(f"{gap:>19.2f}s " + " ".join(f"{c:>8}" for c in cells))

print("\n=== how common are rapid bursts in a real demo? ===")
print("  A presenter doing 'click, type, click' produces 3 actions in ~2 s.")
for rate in (1.0, 2.0, 3.0):
    interval = 1.0 / rate
    print(f"  at {rate:>3.1f} fps (1 sample every {interval*1000:>4.0f} ms): "
          f"3 actions in 2 s -> ~{int(2*rate)} samples to order 3 actions")

print("\n=== what the log does instead ===")
print("  The log is produced at 10-15 fps locally, so it timestamps each action to")
print("  ~0.07-0.10 s. Order survives regardless of the Gemini sampling rate.")

print("\n=== the honest counter-argument ===")
print("  If the demo is slow and deliberate (>= 1 s per step), Gemini's own")
print("  temporal reasoning already reconstructs the order from state changes.")
print("  The log's advantage is concentrated in:")
print("    - actions shorter than ~1 s (press animations, transient flashes)")
print("    - actions with NO net state change (toggle twice, cancel a dialog)")
print("    - attributing a change to a POINTER position (which control was clicked)")
print("    - bursts where several changes land in one sample interval")
