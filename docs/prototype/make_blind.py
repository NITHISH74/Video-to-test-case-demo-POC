"""The decisive Feature 2 test.

Builds demo videos containing action classes whose ORDER and TARGET are invisible in
pixels once you sample at the Gemini rates the app actually allows (0.5-3 fps).

For each action class we measure:
  - is the action's *occurrence* visible in a sampled frame?  (state change persists)
  - is its *order* recoverable?                                (needs the click instant)
  - is its *target* recoverable?                               (needs pointer position)

Then we measure how the cursor/action pipeline recovers each one.
"""
import json
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw

W, H = 1280, 720
FPS = 30          # realistic screen recording
DUR = 20.0
N = int(DUR * FPS)


def cursor_glyph():
    img = Image.new("RGBA", (22, 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(0, 0), (0, 22), (5, 17), (9, 26), (13, 24), (9, 15), (16, 15)],
              fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    return img


CUR = cursor_glyph()


# ---------------------------------------------------------------------------
# Action classes that stress the "is the model smart enough" question
# ---------------------------------------------------------------------------
# 1. TOGGLE TWICE, NET ZERO: A->B->A. Sampled frames look identical before/after.
#    Only the pointer movement + the two intermediate states record it happened.
# 2. IDENTICAL FIELD FOCUS: clicking "Work email" then "Confirm email" - both fields
#    look the same and only one focus ring is visible at any sample.
# 3. NO-OP CLICK: clicking a disabled button - no state change at all.
# 4. ORDER-ONLY: two toggles both end "on"; only the sequence reveals which came first.
ACTIONS = [
    ("move", 0.5, 1.5, (200, 200), (420, 250)),
    ("toggle_ab", 1.5, 3.2, None, None),      # checkbox A: off -> on -> off  (NET ZERO)
    ("move", 3.4, 4.2, (420, 250), (420, 330)),
    ("focus_email", 4.2, 6.0, None, None),    # click email, then confirm email
    ("move", 6.2, 7.0, (420, 330), (760, 470)),
    ("noop_click", 7.0, 8.6, None, None),     # click a disabled button: nothing changes
    ("move", 8.8, 9.6, (760, 470), (420, 250)),
    ("toggle_b_on", 9.6, 11.0, None, None),   # checkbox A on
    ("move", 11.2, 12.0, (420, 250), (420, 330)),
    ("toggle_c_on", 12.0, 13.4, None, None),  # checkbox C on
]

# state machine for the two checkboxes
def checkbox_states(t):
    a = False
    if 1.5 <= t < 2.35:
        a = True
    elif 2.35 <= t < 9.6:
        a = False
    elif t >= 9.6:
        a = True
    c = t >= 12.0
    return a, c


def email_focus(t):
    if 4.2 <= t < 5.1:
        return "work"
    if 5.1 <= t < 6.0:
        return "confirm"
    return None


def cursor_at(t):
    x, y = 900, 600
    for kind, a, b, p0, p1 in ACTIONS:
        if kind != "move":
            continue
        if t < a:
            continue
        if t <= b:
            f = (t - a) / (b - a)
            e = f * f * (3 - 2 * f)
            return (p0[0] + (p1[0] - p0[0]) * e, p0[1] + (p1[1] - p0[1]) * e)
        x, y = p1
    return (x, y)


def render(t):
    im = Image.new("RGB", (W, H), (248, 249, 251))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 52], fill=(26, 32, 46))
    d.text((22, 18), "ACME Admin", fill=(235, 240, 250))
    d.rounded_rectangle([260, 90, 1040, 620], radius=14, fill=(255, 255, 255), outline=(228, 232, 240))
    d.text((290, 120), "Notification settings", fill=(18, 24, 34))

    a_on, c_on = checkbox_states(t)
    # checkbox A at (420,250)
    d.rounded_rectangle([410, 240, 430, 260], radius=4,
                        fill=(38, 99, 235) if a_on else (255, 255, 255),
                        outline=(160, 170, 190), width=2)
    if a_on:
        d.line([(415, 250), (419, 255), (426, 245)], fill=(255, 255, 255), width=3)
    d.text((445, 243), "Email notifications", fill=(40, 50, 65))
    # checkbox C at (420,330)
    d.rounded_rectangle([410, 320, 430, 340], radius=4,
                        fill=(38, 99, 235) if c_on else (255, 255, 255),
                        outline=(160, 170, 190), width=2)
    if c_on:
        d.line([(415, 330), (419, 335), (426, 325)], fill=(255, 255, 255), width=3)
    d.text((445, 323), "SMS notifications", fill=(40, 50, 65))

    # two identical-looking email fields
    focus = email_focus(t)
    for label, yy, key in (("Work email", 400, "work"), ("Confirm email", 460, "confirm")):
        d.text((290, yy - 22), label, fill=(90, 100, 115))
        active = focus == key
        d.rounded_rectangle([290, yy, 700, yy + 40], radius=6,
                            fill=(250, 251, 253),
                            outline=(70, 115, 240) if active else (205, 212, 224),
                            width=2)

    # disabled button (no hover/active styling on click -> a true no-op)
    d.rounded_rectangle([700, 450, 860, 494], radius=6, fill=(216, 222, 232))
    d.text((742, 466), "Save", fill=(150, 158, 172))
    return im


def build(path):
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for i in range(N):
        t = i / FPS
        im = render(t)
        x, y = cursor_at(t)
        im.paste(CUR, (int(x), int(y)), CUR)
        vw.write(cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR))
    vw.release()


if __name__ == "__main__":
    build("/home/user/f2/blind.mp4")
    print("built blind.mp4", N, "frames")
