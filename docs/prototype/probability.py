"""Realistic timing sweep: how long must a UI action last to be caught?

Real UI feedback is short:
  - focus ring on click: persists (visible)
  - button press animation: 100-200 ms
  - tooltip on click: disappears when the pointer leaves (0.3-1.5 s)
  - toast: 2-4 s
  - transient validation flash: 150-400 ms
  - a dialog that is opened and cancelled: 0.5-2 s
The question: at the app's 1 fps default, what is the probability a given action is
sampled at all?  P(caught) = min(1, duration * fps).
"""
print("=== probability an action is sampled at least once ===")
print(f"{'action duration':>16} " + " ".join(f"{r:>6.1f}f" for r in (0.5, 1.0, 2.0, 3.0, 15.0)))
for dur in (0.10, 0.15, 0.20, 0.30, 0.50, 0.80, 1.00, 1.50, 2.00, 3.00):
    cells = []
    for rate in (0.5, 1.0, 2.0, 3.0, 15.0):
        p = min(1.0, dur * rate)
        cells.append(f"{p*100:>5.0f}%")
    print(f"{dur:>14.2f}s " + " ".join(f"{c:>7}" for c in cells))

print("\n=== real UI action classes, at the app's default 1 fps ===")
classes = [
    ("button press animation", 0.15),
    ("hover tooltip (pointer leaves)", 0.80),
    ("transient validation flash", 0.30),
    ("dialog opened then cancelled", 1.20),
    ("toast notification", 3.00),
    ("focus ring (persists)", None),
]
for name, dur in classes:
    if dur is None:
        print(f"  {name:34s} -> always visible while focused")
    else:
        p = min(1.0, dur * 1.0)
        verdict = "usually missed" if p < 0.5 else ("coin flip" if p < 0.9 else "usually caught")
        print(f"  {name:34s} {dur:>4.2f}s -> {p*100:>3.0f}% chance sampled   {verdict}")

print("\n=== what the annotation pipeline sees at 15 fps ===")
for name, dur in classes:
    if dur is None:
        print(f"  {name:34s} -> continuous")
    else:
        n = max(1, int(round(dur * 15)))
        print(f"  {name:34s} {dur:>4.2f}s -> {n:>2} frames  ({'yes' if n >= 2 else 'marginal'})")

print("\n=== turning the slider up does not fix the pointer ===")
print("  At 3.0 fps (the max the UI allows), a 20x28 cursor is sampled 3x/second,")
print("  but it is still only ~6x9 px at HIGH resolution after Gemini downsamples.")
print("  A ring of radius >=24 px survives; the bare pointer does not.")
