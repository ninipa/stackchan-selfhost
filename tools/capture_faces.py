#!/usr/bin/env python3
"""从串口接收 SNAPBIN 二进制帧，拼成联系表 PNG。

协议：设备先打印 "SNAPBIN <w> <h>\n"，随后输出 w*h*2 字节 RGB565(LE)，
再打印 "\nSNAPEND\n"。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import serial
from PIL import Image

MAGIC = b"SNAPBIN "


def rgb565_to_image(raw: bytes, w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        base = y * w * 2
        for x in range(w):
            i = base + x * 2
            v = raw[i] | (raw[i + 1] << 8)
            px[x, y] = (((v >> 11) & 0x1F) << 3, ((v >> 5) & 0x3F) << 2, (v & 0x1F) << 3)
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/cu.usbmodem1101")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--out", default="preview/device_sheet.png")
    ap.add_argument("--dir", default="preview/device_frames")
    args = ap.parse_args()

    out = Path(args.out)
    frames_dir = Path(args.dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    ser = serial.Serial(args.port, args.baud, timeout=0.05)
    ser.reset_input_buffer()
    deadline = time.time() + args.seconds

    frames: list[Image.Image] = []
    buf = bytearray()
    skipped = 0

    while time.time() < deadline and len(frames) < args.frames:
        chunk = ser.read(8192)
        if chunk:
            buf += chunk

        while len(frames) < args.frames:
            idx = buf.find(MAGIC)
            if idx < 0:
                if len(buf) > len(MAGIC):
                    del buf[: len(buf) - len(MAGIC)]
                break

            nl = buf.find(b"\n", idx + len(MAGIC))
            if nl < 0:
                break  # 尺寸行还没收完

            try:
                w, h = (int(v) for v in bytes(buf[idx + len(MAGIC) : nl]).split())
            except ValueError:
                skipped += 1
                del buf[: nl + 1]
                continue

            if w <= 0 or h <= 0:
                del buf[: nl + 1]
                continue

            end = nl + 1 + w * h * 2
            if len(buf) < end:
                break  # 像素还没收完，等下一批

            raw = bytes(buf[nl + 1 : end])
            del buf[:end]

            img = rgb565_to_image(raw, w, h)
            n = len(frames)
            img.save(frames_dir / f"f{n:02d}.png")
            frames.append(img)
            print(f"帧 {n}: {w}x{h}", flush=True)

        if not chunk:
            time.sleep(0.01)

    ser.close()

    if not frames:
        print(f"没有收到任何帧（跳过 {skipped} 个坏头）")
        return

    cw, ch, cols = frames[0].width, frames[0].height, 3
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cw * cols, ch * rows), (0, 0, 0))
    for i, f in enumerate(frames):
        sheet.paste(f, ((i % cols) * cw, (i // cols) * ch))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"共 {len(frames)} 帧 → {out} {sheet.size}")


if __name__ == "__main__":
    main()
