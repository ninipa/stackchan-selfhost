#!/usr/bin/env python3
"""生成炸弹资产（卡通圆弹，引信脖子朝左上；图上不带引线）。

做法：弹体 + 旋转脖子合成一个并集遮罩，再对遮罩做一次腐蚀得到轮廓——
这样弹体和脖子的接缝处不会出现内部描边，剪影是完整的一体。

输出：
  preview/bomb_b_72.png                     预览
  .../skins/default/assets/default_bomb.c   RGB565A8
打印脖口在成品图里的坐标，overlay 用它对引线。
"""
from PIL import Image, ImageDraw, ImageChops, ImageFilter
import math
import os

S = 4
SZ = 72
W = SZ * S

DARK = (42, 38, 34)
WARM = (242, 232, 220)
HOLE = (10, 9, 8)

CX, CY, R = 41.0, 44.0, 23.0
NECK_W, NECK_L = 12.0, 16.0
ANG = 32.0
UX, UY = -math.sin(math.radians(ANG)), -math.cos(math.radians(ANG))
BX, BY = CX + (R - 4.0) * UX, CY + (R - 4.0) * UY      # 脖子根部埋进弹体 4px
TX, TY = BX + NECK_L * UX, BY + NECK_L * UY

OUTLINE_PX = 2.2


def union_mask():
    m = Image.new("L", (W, W), 0)
    ImageDraw.Draw(m).ellipse(
        [(CX - R) * S, (CY - R) * S, (CX + R) * S, (CY + R) * S], fill=255)

    neck = Image.new("L", (W, W), 0)
    ImageDraw.Draw(neck).rounded_rectangle(
        [BX * S - NECK_W * S / 2, BY * S - NECK_L * S,
         BX * S + NECK_W * S / 2, BY * S + 4 * S],
        radius=NECK_W * S / 2, fill=255)
    neck = neck.rotate(ANG, center=(BX * S, BY * S), resample=Image.BICUBIC)
    neck = neck.point(lambda v: 255 if v > 127 else 0)
    return ImageChops.lighter(m, neck)


def main():
    mask = union_mask()
    # 腐蚀得到轮廓环（MinFilter 的窗口要奇数）
    k = int(OUTLINE_PX * S) * 2 + 1
    inner = mask.filter(ImageFilter.MinFilter(k))
    ring = ImageChops.subtract(mask, inner)

    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (W, W), DARK + (255,)), (0, 0), mask)
    im.paste(Image.new("RGBA", (W, W), WARM + (255,)), (0, 0), ring)

    # 左上月牙高光（只落在弹体内，离边缘留 3px）
    hl = Image.new("L", (W, W), 0)
    dh = ImageDraw.Draw(hl)
    hx0, hy0 = (CX - R * 0.60) * S, (CY - R * 0.80) * S
    hx1, hy1 = (CX + R * 0.02) * S, (CY - R * 0.04) * S
    dh.ellipse([hx0, hy0, hx1, hy1], fill=72)
    dh.ellipse([hx0 + 4.0 * S, hy0 + 4.0 * S, hx1 + 5.2 * S, hy1 + 5.2 * S], fill=0)
    bm = Image.new("L", (W, W), 0)
    ImageDraw.Draw(bm).ellipse(
        [(CX - R + 4) * S, (CY - R + 4) * S, (CX + R - 4) * S, (CY + R - 4) * S], fill=255)
    hl = Image.composite(hl, Image.new("L", (W, W), 0), bm)
    im.alpha_composite(Image.merge("RGBA", (
        Image.new("L", (W, W), 255), Image.new("L", (W, W), 255),
        Image.new("L", (W, W), 255), hl)))

    # 脖口：引线从这里插入（暗孔 + 半圈亮边，开口朝外）
    d = ImageDraw.Draw(im)
    rx, ry = 3.4 * S, 3.2 * S
    d.ellipse([TX * S - rx, TY * S - ry, TX * S + rx, TY * S + ry], fill=HOLE + (255,))
    d.ellipse([TX * S - rx, TY * S - ry, TX * S + rx, TY * S + ry],
              outline=WARM + (255,), width=int(1.2 * S))

    final = im.resize((SZ, SZ), Image.LANCZOS)
    os.makedirs("preview", exist_ok=True)
    final.save("preview/bomb_b_72.png")

    px = final.load()
    rgb, alpha = bytearray(), bytearray()
    for y in range(SZ):
        for x in range(SZ):
            r_, g_, b_, a_ = px[x, y]
            if a_ < 8:
                r_ = g_ = b_ = 0
            v = ((r_ >> 3) << 11) | ((g_ >> 2) << 5) | (b_ >> 3)
            rgb += bytes((v & 0xFF, (v >> 8) & 0xFF))
            alpha.append(a_)
    data = bytes(rgb) + bytes(alpha)

    L = ['/* 炸弹（卡通圆弹）：脖口朝左上，番茄钟引线由此插入。本文件由工具生成，勿手改。 */',
         '#include "lvgl.h"', '',
         '#ifndef LV_ATTRIBUTE_MEM_ALIGN', '#define LV_ATTRIBUTE_MEM_ALIGN', '#endif', '',
         'static const uint8_t default_bomb_map[] = {']
    for i in range(0, len(data), 16):
        L.append("    " + ", ".join(f"0x{b:02x}" for b in data[i:i + 16]) + ",")
    L += ['};', '',
          'const lv_image_dsc_t default_bomb = {',
          '    .header.cf    = LV_COLOR_FORMAT_RGB565A8,',
          '    .header.magic = LV_IMAGE_HEADER_MAGIC,',
          f'    .header.w     = {SZ},',
          f'    .header.h     = {SZ},',
          f'    .data_size    = {SZ * SZ * 3},',
          '    .data         = default_bomb_map,',
          '};', '']
    out = "stackchan/firmware/main/stackchan/avatar/skins/default/assets/default_bomb.c"
    open(out, "w").write("\n".join(L))
    print(f"wrote {out} {len(data)}B   TIP=({TX:.1f},{TY:.1f})  BODY=({CX},{CY}) R={R}")


if __name__ == "__main__":
    main()
