"""Synthesize a 'demo video' of a user operating a fake SaaS UI, with a mouse cursor."""
import numpy as np, cv2, json
from PIL import Image, ImageDraw

W, H, FPS = 1280, 720, 15
DUR = 16.0
N = int(DUR * FPS)


def cursor_glyph():
    """Classic white arrow with black outline, ~22x30."""
    img = Image.new("RGBA", (22, 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(0, 0), (0, 22), (5, 17), (9, 26), (13, 24), (9, 15), (16, 15)]
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    return img


CUR = cursor_glyph()

# Ground-truth action script: (kind, start, end, payload/point, target)
ACTIONS = [
    ("move", 0.4, 2.0, (170, 262), (470, 262)),
    ("click", 2.0, 2.0, (470, 262), None),
    ("type", 2.3, 4.0, "qa.lead@acme.io", None),
    ("move", 4.2, 5.2, (470, 262), (470, 352)),
    ("click", 5.2, 5.2, (470, 352), None),
    ("type", 5.5, 6.9, "Str0ng!Pass", None),
    ("move", 7.0, 8.0, (470, 352), (470, 440)),
    ("click", 8.0, 8.0, (470, 440), None),
    ("toast", 8.2, 10.8, "Workspace created", None),
    ("move", 10.9, 11.9, (470, 440), (60, 120)),
    ("click", 11.9, 11.9, (60, 120), None),
    ("panel", 12.3, 15.8, "Settings", None),
]


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def ease(t):
    return t * t * (3 - 2 * t)


def typed(text, t, t0, t1):
    if t <= t0:
        return ""
    frac = min(1.0, (t - t0) / max(1e-6, (t1 - t0)))
    return text[: int(round(len(text) * frac))]


def render(t):
    im = Image.new("RGB", (W, H), (247, 248, 250))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 56], fill=(24, 30, 43))
    d.rounded_rectangle([16, 14, 150, 42], radius=6, fill=(52, 63, 84))
    d.text((34, 22), "ACME Console", fill=(230, 235, 245))
    d.rectangle([0, 56, 210, H], fill=(238, 240, 244))
    for name, y in [("Dashboard", 90), ("Workspace", 130), ("Billing", 170), ("Settings", 210)]:
        active = name == "Settings" and t >= 12.3
        if active:
            d.rounded_rectangle([10, y - 12, 198, y + 14], radius=6, fill=(214, 226, 255))
        d.text((26, y - 6), name, fill=(30, 40, 60) if active else (70, 80, 100))
    d.rounded_rectangle([250, 90, 1030, 600], radius=14, fill=(255, 255, 255), outline=(226, 230, 238))
    d.text((280, 120), "Create workspace", fill=(20, 25, 35))
    d.text((280, 150), "Provision a new team workspace", fill=(120, 130, 145))
    d.text((280, 232), "Work email", fill=(80, 90, 105))
    d.rounded_rectangle([280, 250, 760, 300], radius=8, fill=(250, 251, 253),
                        outline=(206, 212, 224) if t < 2.0 else (86, 124, 255), width=2)
    d.text((280, 322), "Admin password", fill=(80, 90, 105))
    d.rounded_rectangle([280, 340, 760, 390], radius=8, fill=(250, 251, 253),
                        outline=(206, 212, 224) if t < 5.2 else (86, 124, 255), width=2)
    hot = 7.8 <= t < 8.15
    d.rounded_rectangle([400, 416, 540, 464], radius=8, fill=(38, 99, 235) if not hot else (22, 74, 190))
    d.text((446, 432), "Create", fill=(255, 255, 255))
    for txt, y, a, b in [("qa.lead@acme.io", 266, 2.3, 4.0), ("Str0ng!Pass", 356, 5.5, 6.9)]:
        s = typed(txt, t, a, b)
        if s:
            d.text((296, y), s, fill=(20, 25, 35))
    if 8.2 <= t < 10.8:
        d.rounded_rectangle([790, 96, 1014, 146], radius=8, fill=(16, 124, 84))
        d.text((812, 116), "Workspace created", fill=(255, 255, 255))
    if t >= 12.3:
        d.rounded_rectangle([250, 90, 1030, 600], radius=14, fill=(255, 255, 255), outline=(226, 230, 238))
        d.text((280, 120), "Settings", fill=(20, 25, 35))
        for i, (k, v) in enumerate([("Region", "us-east-1"), ("Plan", "Team"), ("SSO", "Enabled")]):
            d.text((280, 180 + i * 46), k, fill=(110, 120, 135))
            d.text((520, 180 + i * 46), v, fill=(20, 25, 35))
    return im


def cursor_state(t):
    x, y = 900, 520
    for act in ACTIONS:
        if act[0] == "move":
            _, a, b, p0, p1 = act
            if t < a:
                continue
            if t <= b:
                return (*lerp(p0, p1, ease((t - a) / (b - a))), True)
            x, y = p1
    return (x, y, True)


def ground_truth_clicks():
    out = []
    for act in ACTIONS:
        if act[0] == "click":
            out.append((act[1], cursor_state(act[1] + 1e-4)[:2]))
    return out


def build(path):
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for i in range(N):
        t = i / FPS
        im = render(t)
        x, y, vis = cursor_state(t)
        if vis:
            im.paste(CUR, (int(x), int(y)), CUR)
        vw.write(cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR))
    vw.release()
    return ground_truth_clicks()


if __name__ == "__main__":
    gt = build("/home/user/proto/demo.mp4")
    json.dump([[t, list(p)] for t, p in gt], open("/home/user/proto/gt.json", "w"))
    print("frames:", N, "clicks:", len(gt), gt)
