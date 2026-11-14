#!/usr/bin/env python3
"""生成情绪配饰素材（问号 / 感叹号 / Z / 眼泪），格式与现有 decorator_*.c 一致：
RGB565A8（前 w*h*2 字节 RGB565，全白；后 w*h 字节 A8 蒙版），
运行时由 LVGL 用 recolor 染色，所以素材本身只关心形状。

用法：python3 tools/make_symbol_assets.py
"""
from PIL import Image, ImageDraw, ImageFont
import os

SS = 6  # 超采样倍数

OUT_DIR = os.path.join(os.path.dirname(__file__), "..",
                       "stackchan", "firmware", "main", "stackchan",
                       "avatar", "decorators", "assets")

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

BOILER_HEAD = """#ifdef __has_include
#if __has_include("lvgl.h")
#ifndef LV_LVGL_H_INCLUDE_SIMPLE
#define LV_LVGL_H_INCLUDE_SIMPLE
#endif
#endif
#endif

#if defined(LV_LVGL_H_INCLUDE_SIMPLE)
#include "lvgl.h"
#else
#include "lvgl/lvgl.h"
#endif

#ifndef LV_ATTRIBUTE_MEM_ALIGN
#define LV_ATTRIBUTE_MEM_ALIGN
#endif
"""


def pick_font(px):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, px)
            except Exception:
                continue
    raise RuntimeError("找不到可用字体")


def render_glyph(ch, w, h, glyph_px):
    canvas = Image.new("L", (w * SS, h * SS), 0)
    font = pick_font(glyph_px * SS)
    d = ImageDraw.Draw(canvas)
    bbox = d.textbbox((0, 0), ch, font=font)
    gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (w * SS - gw) // 2 - bbox[0]
    y = (h * SS - gh) // 2 - bbox[1]
    d.text((x, y), ch, font=font, fill=255)
    return canvas.resize((w, h), Image.LANCZOS)


def render_tear(w, h):
    """水滴：下面一个圆，上面一个尖，眼角挂着的眼泪就是这个形状"""
    canvas = Image.new("L", (w * SS, h * SS), 0)
    d = ImageDraw.Draw(canvas)
    cx = w * SS // 2
    r = int(w * SS * 0.30)
    cy = int(h * SS * 0.68)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    d.polygon([(cx, int(h * SS * 0.04)), (cx + int(r * 0.95), cy), (cx - int(r * 0.95), cy)], fill=255)
    return canvas.resize((w, h), Image.LANCZOS)


def write_asset(mask, name):
    w, h = mask.size
    upper = name.upper()
    var = f"decorator_{name}"

    # RGB565 部分：纯白（0xFFFF），recolor 时会整体替换
    rgb = bytearray()
    for _ in range(w * h):
        rgb += b"\xff\xff"
    data = rgb + bytearray(mask.tobytes())

    lines = []
    for i in range(0, len(data), 18):
        chunk = ", ".join(f"0x{b:02x}" for b in data[i:i + 18])
        lines.append("        " + chunk + ",")
    body = "\n".join(lines)

    text = BOILER_HEAD + f"""
#ifndef LV_ATTRIBUTE_IMAGE_DECORATOR_{upper}
#define LV_ATTRIBUTE_IMAGE_DECORATOR_{upper}
#endif

const LV_ATTRIBUTE_MEM_ALIGN LV_ATTRIBUTE_LARGE_CONST LV_ATTRIBUTE_IMAGE_DECORATOR_{upper} uint8_t
    {var}_map[] = {{
{body}
}};

const lv_image_dsc_t {var} = {{
    .header.cf    = LV_COLOR_FORMAT_RGB565A8,
    .header.magic = LV_IMAGE_HEADER_MAGIC,
    .header.w     = {w},
    .header.h     = {h},
    .data_size    = {w * h} * 3,
    .data         = {var}_map,
}};
"""
    path = os.path.join(OUT_DIR, f"{var}.c")
    with open(path, "w") as f:
        f.write(text)
    ink = sum(1 for v in mask.tobytes() if v)
    print(f"{var:22s} {w}x{h}  {ink:5d} 个不透明像素  {len(text):6d} 字节源码")


def main():
    write_asset(render_glyph("?", 56, 56, 44), "question")
    write_asset(render_glyph("!", 56, 56, 44), "exclam")
    write_asset(render_glyph("Z", 40, 40, 34), "z")
    write_asset(render_tear(30, 40), "tear")


if __name__ == "__main__":
    main()
