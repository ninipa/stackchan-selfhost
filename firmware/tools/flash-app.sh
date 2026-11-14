#!/usr/bin/env bash
# 只写 app 槽 + otadata，绝不写 assets（0xa00000）。
#
# 用法：
#   ./tools/flash-app.sh --yes
#   PORT=/dev/cu.usbmodem1101 ./tools/flash-app.sh --yes
#
# 硬规则：
#   - 不要用 `idf.py flash`：它会把 generated_assets.bin 写到 0xa00000，
#     覆盖唤醒词模型。
#   - 不要 `erase-flash`：会清 NVS（Wi-Fi、绑定、舵机零点）。
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-build6}"
PYTHON="${PYTHON:-python3}"
PORT="${PORT:-}"
YES=0

while [ $# -gt 0 ]; do
    case "$1" in
        --yes) YES=1; shift ;;
        --port) PORT="$2"; shift 2 ;;
        --build-dir) BUILD_DIR="$2"; shift 2 ;;
        -h | --help)
            sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

BIN="$DIR/$BUILD_DIR/stack-chan.bin"
OTA_DATA="$DIR/$BUILD_DIR/ota_data_initial.bin"

[ -f "$BIN" ] || { echo "缺少 ${BIN}；先 ./tools/build.sh" >&2; exit 1; }
[ -f "$OTA_DATA" ] || { echo "缺少 ${OTA_DATA}；先 ./tools/build.sh" >&2; exit 1; }

if [ -z "$PORT" ]; then
    for p in /dev/cu.usbmodem* /dev/ttyACM* /dev/ttyUSB*; do
        if [ -e "$p" ]; then
            PORT="$p"
            break
        fi
    done
fi
[ -n "$PORT" ] || { echo "找不到串口；用 PORT=/dev/... 指定" >&2; exit 1; }

echo "端口: $PORT"
echo "app : $BIN"
echo "otadata: $OTA_DATA"
echo
echo "只会写 0x20000 (app) + 0xd000 (otadata)。"
echo "不会写 0xa00000 (assets)，不会 erase-flash。"
echo

if [ "$YES" != 1 ]; then
    echo "确认后加 --yes 再执行。" >&2
    exit 1
fi

"$PYTHON" -m esptool --chip esp32s3 -p "$PORT" -b 921600 write-flash \
    0x20000 "$BIN" \
    0xd000 "$OTA_DATA"

"$PYTHON" -m esptool --chip esp32s3 -p "$PORT" run || true

echo
echo "完成。若串口无输出：手动按一次 RST；再跑 ./tools/verify-device.sh"
