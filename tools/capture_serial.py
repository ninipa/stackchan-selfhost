#!/usr/bin/env python3
"""抓设备串口日志，带本地时间戳，写文件。

用法: python tools/capture_serial.py --seconds 180 --out /tmp/device.log
"""

import argparse
import time
from datetime import datetime

import serial


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/cu.usbmodem1101")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=int, default=180)
    ap.add_argument("--out", default="/tmp/device-serial.log")
    args = ap.parse_args()

    s = serial.Serial(args.port, args.baud, timeout=0.3)
    end = time.time() + args.seconds
    n = 0
    with open(args.out, "w", encoding="utf-8") as f:
        while time.time() < end:
            line = s.readline()
            if not line:
                continue
            text = line.decode("utf-8", "replace").rstrip()
            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            f.write(f"{stamp} {text}\n")
            f.flush()
            n += 1
    s.close()
    print(f"抓到 {n} 行 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
