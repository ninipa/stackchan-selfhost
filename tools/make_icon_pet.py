#!/usr/bin/env python3
"""生成 PET app 的图标，直接编进 app（不占 assets 分区）。

为什么不用 assets 分区：那里装着唤醒词模型 wn9_histackchan_tts3，
刷 generated_assets.bin 会把它一起冲掉（2026-11-02 踩过）。

输出：
    stackchan/firmware/main/apps/app_touch_demo/icon_pet_data.c
    /tmp/icon_pet_preview.png   （只用于肉眼检查）

用法：
    ~/.workbuddy/binaries/python/versions/3.13.12/bin/python3 tools/make_icon_pet.py
"""
import struct
from pathlib import Path

from PIL import Image, ImageDraw

W, H = 188, 150  # 和其他图标保持一致，启动器按原始尺寸显示
SS = 4         # 超采样倍数

OUT_C = Path("stackchan/firmware/main/apps/app_touch_demo/icon_pet_data.c")
PREVIEW = Path("/tmp/icon_pet_preview.png")


def draw_icon() -> Image.Image:
    # 先按 188x150 的比例构图，再整体缩到目标尺寸
    CW, CH = 188, 150
    img = Image.new("RGBA", (CW * SS, CH * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)

    def s(v):
        return int(v * SS)

    # 头
    cx, cy, r = 94, 102, 38
    d.ellipse([s(cx - r), s(cy - r), s(cx + r), s(cy + r)], outline=white, width=s(6))
    for ex in (cx - 14, cx + 14):
        d.ellipse([s(ex - 5), s(cy - 13), s(ex + 5), s(cy - 3)], fill=white)
    d.arc([s(cx - 15), s(cy + 2), s(cx + 15), s(cy + 24)], start=20, end=160, fill=white, width=s(5))

    # 手：手掌 + 三根手指按住头顶
    d.rounded_rectangle([s(62), s(16), s(126), s(50)], radius=s(14), fill=white)
    for fx in (72, 94, 116):
        d.rounded_rectangle([s(fx - 8), s(44), s(fx + 8), s(76)], radius=s(8), fill=white)
    d.rounded_rectangle([s(120), s(40), s(142), s(58)], radius=s(9), fill=white)

    # 抚摸动作线
    for i in range(2):
        ox = 132 + i * 12
        d.arc([s(ox), s(10), s(ox + 20), s(34)], start=290, end=70, fill=white, width=s(4))

    return img.resize((W, H), Image.LANCZOS)


def to_rgb565a8(img: Image.Image) -> bytes:
    px = img.load()
    rgb = bytearray()
    alpha = bytearray()
    for y in range(H):
        for x in range(W):
            r, g, b, a = px[x, y]
            if a < 8:
                r = g = b = 0
            v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            rgb += struct.pack("<H", v)
            alpha.append(a)
    return bytes(rgb) + bytes(alpha)


def emit_c(data: bytes) -> str:
    lines = [
        "/* 自动生成，勿手改。生成脚本: tools/make_icon_pet.py */",
        "#include <stdint.h>",
        "",
        f"// {W}x{H} RGB565A8",
        "const uint8_t icon_pet_map[] = {",
    ]
    for i in range(0, len(data), 16):
        chunk = ", ".join(f"0x{b:02x}" for b in data[i : i + 16])
        lines.append(f"    {chunk},")
    lines.append("};")
    lines.append(f"const unsigned int icon_pet_map_len = {len(data)};")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    img = draw_icon()
    img.save(PREVIEW)

    data = to_rgb565a8(img)
    assert len(data) == W * H * 3, f"尺寸不符: {len(data)}"

    OUT_C.parent.mkdir(parents=True, exist_ok=True)
    OUT_C.write_text(emit_c(data))

    print(f"preview : {PREVIEW}")
    print(f"c file  : {OUT_C}  {len(data)} 字节数据")


if __name__ == "__main__":
    main()
