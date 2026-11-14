#!/usr/bin/env python3
"""比对固件与服务端的头部姿态常量，防止两边悄悄漂移。

固件：main/hal/behavior/pose.h      (0.1° 单位)
服务端：selfhost/behavior/app/behavior.py

    .venv/bin/python tools/check_pose_constants.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSE_H = ROOT / "stackchan/firmware/main/hal/behavior/pose.h"
BEHAVIOR_PY = ROOT / "selfhost/behavior/app/behavior.py"

# (说明, pose.h 里的名字, behavior.py 里的名字)
PAIRS = [
    ("常态位", "kPitchHome", "HOME_PITCH"),
    ("点头落点", "kPitchNod", "NOD_PITCH"),
    ("左右转幅度", "kYawTurn", "TURN_YAW"),
]


def parse_cpp(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    out = {}
    for _, cpp_name, _ in PAIRS:
        m = re.search(rf"constexpr\s+int\s+{cpp_name}\s*=\s*(-?\d+)", text)
        if m:
            out[cpp_name] = int(m.group(1))
    return out


def parse_py(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    out = {}
    for _, _, py_name in PAIRS:
        m = re.search(rf"^{py_name}\s*=\s*(-?\d+)", text, re.MULTILINE)
        if m:
            out[py_name] = int(m.group(1))
    return out


def main() -> int:
    cpp = parse_cpp(POSE_H)
    py = parse_py(BEHAVIOR_PY)

    bad = 0
    print(f"{'项':<10}{'pose.h':>10}{'behavior.py':>14}   结论")
    for label, cpp_name, py_name in PAIRS:
        a = cpp.get(cpp_name)
        b = py.get(py_name)
        if a is None or b is None:
            print(f"{label:<10}{str(a):>10}{str(b):>14}   缺常量")
            bad += 1
            continue
        ok = a == b
        if not ok:
            bad += 1
        print(f"{label:<10}{a:>10}{b:>14}   {'一致' if ok else '不一致'}")

    if bad:
        print(f"\n{bad} 项不一致：改一处就要同时改两边（角度单位都是 0.1°）")
        return 1
    print("\n全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
