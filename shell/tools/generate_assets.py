#!/usr/bin/env python3
"""Generate StackChan launcher icons, adaptive foreground, and splash images.

Run from shell/ or repo root:
    python3 shell/tools/generate_assets.py

Design: icon B (white robot head / black screen / mint eyes / coral button).
Requires Pillow and a macOS CJK font for the splash wordmark.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "android" / "app" / "src" / "main" / "res"
FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]

DARK = (25, 23, 18, 255)
CREAM = (245, 240, 231, 255)
SURFACE = (255, 252, 246, 255)
MINT = (156, 239, 217, 255)
CORAL = (196, 85, 47, 255)
INK = (33, 29, 23, 255)


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_b(size: int, mode: str) -> Image.Image:
    """mode: legacy | round | foreground."""
    s = 4
    n = 512 * s
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def p(v: float) -> int:
        return int(v * s)

    def rr(box, r, fill):
        d.rounded_rectangle([p(box[0]), p(box[1]), p(box[2]), p(box[3])],
                            radius=p(r), fill=fill)

    def eye(cx, cy, w, h):
        d.rounded_rectangle([p(cx - w / 2), p(cy - h / 2), p(cx + w / 2), p(cy + h / 2)],
                            radius=p(w / 2), fill=MINT)

    def smile(cx, cy, w, h, width):
        d.arc([p(cx - w / 2), p(cy - h / 2), p(cx + w / 2), p(cy + h / 2)],
              start=20, end=160, fill=MINT, width=p(width))

    if mode == "legacy":
        d.rectangle([0, 0, n, n], fill=DARK)
        glow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([p(120), p(80), p(392), p(380)],
                                     fill=MINT[:3] + (28,))
        glow = glow.filter(ImageFilter.GaussianBlur(p(58)))
        im = Image.alpha_composite(im, glow)
        d = ImageDraw.Draw(im)
    elif mode == "round":
        d.ellipse([0, 0, n - 1, n - 1], fill=DARK)

    if mode == "foreground":
        # Adaptive foreground: scale about the face center so antenna/neck
        # stay inside the 66/108 safe zone.
        layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)

        def q(v: float) -> int:
            return int(v * s)

        def lrr(box, r, fill):
            ld.rounded_rectangle([q(box[0]), q(box[1]), q(box[2]), q(box[3])],
                                 radius=q(r), fill=fill)

        def leye(x, y, w, h):
            ld.rounded_rectangle([q(x - w / 2), q(y - h / 2), q(x + w / 2), q(y + h / 2)],
                                 radius=q(w / 2), fill=MINT)

        def lsmile(x, y, w, h, width):
            ld.arc([q(x - w / 2), q(y - h / 2), q(x + w / 2), q(y + h / 2)],
                   start=20, end=160, fill=MINT, width=q(width))

        lrr((116, 96, 396, 376), 72, SURFACE)
        lrr((146, 146, 366, 326), 40, (12, 11, 9, 255))
        leye(206, 232, 44, 68)
        leye(306, 232, 44, 68)
        lsmile(256, 282, 48, 26, 6)
        lrr((396, 206, 412, 258), 8, CORAL)
        ld.rounded_rectangle([q(248), q(66), q(264), q(96)], radius=q(8), fill=CORAL)
        ld.ellipse([q(240), q(50), q(272), q(82)], fill=MINT)
        lrr((206, 376, 306, 412), 22, SURFACE)
        scale = 0.72
        layer = layer.resize((int(n * scale), int(n * scale)), Image.LANCZOS)
        im.alpha_composite(layer, (int((n - layer.width) / 2),
                                   int((n - layer.height) / 2)))
    else:
        shadow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            [p(116), p(112), p(396), p(392)], radius=p(72), fill=(0, 0, 0, 90))
        shadow = shadow.filter(ImageFilter.GaussianBlur(p(26)))
        im = Image.alpha_composite(im, shadow)
        d = ImageDraw.Draw(im)

        rr((116, 96, 396, 376), 72, SURFACE)
        rr((146, 146, 366, 326), 40, (12, 11, 9, 255))
        eye(206, 232, 44, 68)
        eye(306, 232, 44, 68)
        smile(256, 282, 48, 26, 6)
        rr((396, 206, 412, 258), 8, CORAL)
        d.rounded_rectangle([p(248), p(66), p(264), p(96)], radius=p(8), fill=CORAL)
        d.ellipse([p(240), p(50), p(272), p(82)], fill=MINT)
        rr((206, 376, 306, 412), 22, SURFACE)

    return im.resize((size, size), Image.LANCZOS)


def logo(size: int) -> Image.Image:
    """Small rounded-square logo used on the splash."""
    s = 4
    n = size * s
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.22), fill=DARK)
    inner = draw_b(n, "legacy").convert("RGBA")
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1],
                                           radius=int(n * 0.22), fill=255)
    im.paste(inner, (0, 0), mask)
    return im.resize((size, size), Image.LANCZOS)


def splash(w: int, h: int) -> Image.Image:
    im = Image.new("RGB", (w, h), CREAM)
    halo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse([w * 0.15, h * 0.12, w * 0.85, h * 0.72], fill=(255, 255, 255, 90))
    halo = halo.filter(ImageFilter.GaussianBlur(int(min(w, h) * 0.08)))
    im = Image.alpha_composite(im.convert("RGBA"), halo).convert("RGB")
    d = ImageDraw.Draw(im)

    m = min(w, h)
    ls = int(m * 0.26)
    lg = logo(ls)
    im.paste(lg, ((w - ls) // 2, int(h * 0.37 - ls / 2)), lg)
    text = "StackChan"
    fs = max(18, int(m * 0.072))
    f = font(fs)
    tw = d.textlength(text, font=f)
    d.text(((w - tw) / 2, int(h * 0.37 + ls / 2 + m * 0.06)), text, fill=INK, font=f)
    cy = int(h * 0.37 + ls / 2 + m * 0.06 + fs * 1.7)
    d.rounded_rectangle([w / 2 - m * 0.045, cy, w / 2 + m * 0.045,
                         cy + max(3, int(m * 0.008))], radius=99, fill=CORAL)
    return im


def main() -> None:
    for path in sorted(RES.glob("mipmap-*/ic_launcher.png")):
        draw_b(Image.open(path).width, "legacy").save(path)
    for path in sorted(RES.glob("mipmap-*/ic_launcher_round.png")):
        draw_b(Image.open(path).width, "round").save(path)
    for path in sorted(RES.glob("mipmap-*/ic_launcher_foreground.png")):
        draw_b(Image.open(path).width, "foreground").save(path)
    for path in sorted(RES.glob("drawable*/splash.png")):
        w, h = Image.open(path).size
        splash(w, h).save(path)
    print("StackChan icons and splash images generated")


if __name__ == "__main__":
    main()
