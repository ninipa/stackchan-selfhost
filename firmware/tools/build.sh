#!/usr/bin/env bash
# 拉依赖 + 构建固件。
#
# 用法：
#   ./tools/build.sh
#   BUILD_DIR=build-verify ./tools/build.sh
#
# 前置：source ESP-IDF v6.1 的 export.sh；先跑 ./tools/configure.sh。
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-build6}"
PYTHON="${PYTHON:-python3}"

if ! command -v idf.py >/dev/null 2>&1; then
    echo "idf.py 不在 PATH；先 source \$IDF_PATH/export.sh" >&2
    exit 1
fi

if [ ! -f "$DIR/sdkconfig.defaults.local" ]; then
    echo "缺少 sdkconfig.defaults.local；先运行 ./tools/configure.sh" >&2
    exit 1
fi

cd "$DIR"

if [ "${SKIP_FETCH:-0}" = "1" ]; then
    echo "== 1/2 跳过 fetch_repos（SKIP_FETCH=1；依赖需已存在） =="
else
    echo "== 1/2 拉依赖（pin 的 SHA） =="
    "$PYTHON" fetch_repos.py
fi

echo "== 2/2 构建 =="
idf.py -B "$BUILD_DIR" build

BIN="$DIR/$BUILD_DIR/stack-chan.bin"
if [ ! -f "$BIN" ]; then
    echo "构建产物缺失：$BIN" >&2
    exit 1
fi

SIZE="$(stat -f%z "$BIN" 2>/dev/null || stat -c%s "$BIN" 2>/dev/null || echo 0)"
if [ "$SIZE" -lt 2000000 ]; then
    echo "产物异常小（$((SIZE / 1024)) KB），检查构建日志" >&2
    exit 1
fi

echo
echo "OK  ${BIN}（$((SIZE / 1024 / 1024)) MB）"
if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$BIN"
else
    sha256sum "$BIN"
fi

echo
echo "下一步： ./tools/flash-app.sh --yes"
