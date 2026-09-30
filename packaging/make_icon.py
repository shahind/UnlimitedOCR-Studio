"""Draws assets/icon.ico + assets/icon.png (a document page with a Σ on a blue tile). Run once; output is committed."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
S = 1024  # draw large, downsample for crisp small sizes


def font(size):
    for name in ("cambria.ttc", "seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# tile with a vertical gradient
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
top, bottom = (59, 130, 246), (29, 78, 216)
for y in range(S):
    t = y / S
    gd.line([(0, y), (S, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], radius=200, fill=255)
img.paste(grad, (0, 0), mask)

# document page with folded corner
x0, y0, x1, y1, fold = 250, 170, 774, 854, 150
d.polygon([(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)], fill=(255, 255, 255, 255))
d.polygon([(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], fill=(191, 219, 254, 255))
# text lines
for i, w in enumerate([330, 380, 250]):
    y = 610 + i * 70
    d.rounded_rectangle([x0 + 70, y, x0 + 70 + w, y + 30], radius=15, fill=(147, 197, 253, 255))
# sigma
f = font(400)
d.text(((x0 + x1) / 2 - 20, 400), "Σ", font=f, fill=(29, 78, 216, 255), anchor="mm")

assets = ROOT / "assets"
assets.mkdir(exist_ok=True)
img.resize((256, 256), Image.LANCZOS).save(assets / "icon.png")
img.save(assets / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", assets / "icon.ico")
