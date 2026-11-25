"""Add a thin-glyph (I-beam) branch: allow low fill + high aspect when the blob is
dark-on-light or light-on-dark with a thin linear shape."""
import cv2, numpy as np, json
import steps as base

def blobs(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    bright = (gray > 235).astype(np.uint8)
    dark_near = cv2.dilate((gray < 90).astype(np.uint8), np.ones((5,5),np.uint8))
    cand = cv2.morphologyEx(cv2.bitwise_and(bright, dark_near), cv2.MORPH_CLOSE, np.ones((3,3),np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(cand, 8)
    out=[]
    for i in range(1,n):
        x,y,w,h,area = stats[i]
        if not (5 <= w <= 46 and 10 <= h <= 56) or area < 30: continue
        fill = area/float(w*h); ar = h/float(w)
        arrow_ok = 0.35 <= fill <= 0.92 and 1.0 <= ar <= 2.4
        ibeam_ok = 0.06 <= fill <= 0.40 and 1.4 <= ar <= 4.0
        if not (arrow_ok or ibeam_ok): continue
        score = (fill + 1.0 - abs(ar-1.45)) if arrow_ok else (0.5*fill + 0.5 - abs(ar-2.2)*0.2)
        out.append((score,(float(x),float(y)),"arrow" if arrow_ok else "ibeam"))
    out.sort(reverse=True)
    return out

def find2(frame):
    for img in (frame, cv2.bitwise_not(frame)):
        b = blobs(img)
        if b:
            # prefer arrow glyphs; ibeam only if nothing else
            arrows=[c for c in b if c[2]=="arrow"]
            return (arrows[0] if arrows else b[0])[1]
    return None

def recall(path, fn):
    cap=cv2.VideoCapture(path); frames=[]
    while True:
        ok,f=cap.read()
        if not ok: break
        frames.append(f)
    cap.release()
    return sum(1 for f in frames if fn(f) is not None)/max(1,len(frames))

res={}
print(f"{'variant':14s} {'base':>8s} {'improved':>9s}")
for kind in ("white_arrow","black_arrow","ibeam","tiny_hidpi"):
    p=f"/home/user/proto/v_{kind}.mp4"
    a=recall(p, base.find_cursor); b=recall(p, find2)
    res[kind]={"base":round(a,3),"improved":round(b,3)}
    print(f"{kind:14s} {a:>8.3f} {b:>9.3f}")
json.dump(res, open("robust2.json","w"), indent=2)
