#!/usr/bin/env bash
# 复位设备并抓 15s 串口日志，检查关键行。
#
# 用法：
#   ./tools/verify-device.sh
#   PORT=/dev/cu.usbmodem1101 SECS=20 ./tools/verify-device.sh
#
# 依赖：pyserial（pip install pyserial，或用工作区 .venv）
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-}"
SECS="${SECS:-15}"
PYTHON="${PYTHON:-python3}"

if [ -z "$PORT" ]; then
    for p in /dev/cu.usbmodem* /dev/ttyACM* /dev/ttyUSB*; do
        if [ -e "$p" ]; then
            PORT="$p"
            break
        fi
    done
fi
[ -n "$PORT" ] || { echo "找不到串口；用 PORT=/dev/... 指定" >&2; exit 1; }

if ! "$PYTHON" -c 'import serial' >/dev/null 2>&1; then
    echo "缺少 pyserial：pip install pyserial（或 PYTHON=.venv/bin/python）" >&2
    exit 1
fi

echo "端口: ${PORT}，抓 ${SECS}s…"

"$PYTHON" - "$PORT" "$SECS" <<'PY'
import sys
import time

import serial

port = sys.argv[1]
secs = int(sys.argv[2])
try:
    ser = serial.Serial(port, 115200, timeout=0.2)
except Exception as exc:  # noqa: BLE001
    print(f"打开串口失败：{exc}", file=sys.stderr)
    raise SystemExit(1)

# USB-Serial/JTAG 复位序列（AGENTS.md 实测）
ser.setDTR(False)
ser.setRTS(True)
time.sleep(0.15)
ser.setRTS(False)

end = time.time() + secs
chunks = []
while time.time() < end:
    data = ser.read(4096)
    if data:
        chunks.append(data)
ser.close()

text = b"".join(chunks).decode("utf-8", "replace")
print(text[-12000:])

checks = ["BehaviorClient", "free sram", "DMA"]
missing = [c for c in checks if c not in text]
if "ota" not in text.lower():
    missing.append("OTA")

print()
print("MISSING:", missing)
raise SystemExit(1 if missing else 0)
PY
