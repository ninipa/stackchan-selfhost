#!/bin/bash
# 一条命令：编译 LVGL 核心 + 应用代码，产出 build/lvsim/face_render。
# 用法：bash tools/lvsim/build.sh
# 说明：应用层（皮肤/装饰器/运动/入口/素材）由 build_app.sh 编译，这里只负责 LVGL 核心。
set -e

WS="$(cd "$(dirname "$0")/../.." && pwd)"
# 公开单库布局是 firmware/；当前工作区是 stackchan/firmware
FW="$WS/firmware"
[ -d "$FW" ] || FW="$WS/stackchan/firmware"
LV="$FW/managed_components/lvgl__lvgl"
OUT="$WS/build/lvsim"
OBJ="$OUT/obj"

CC="${CC:-clang}"
command -v "$CC" >/dev/null 2>&1 || CC=cc
export CC

if [ ! -d "$LV/src" ]; then
  echo "缺少 LVGL: $LV" >&2
  echo "先跑一次固件构建（idf.py -B build6 build）下载 managed_components。" >&2
  exit 1
fi

mkdir -p "$OBJ"

INCS=(
  "-I$WS/tools/lvsim"
  "-I$LV"
  "-I$FW/main"
  "-I$FW/components/smooth_ui_toolkit/src"
  "-I$FW/components/smooth_ui_toolkit/src/lvgl"
)
CFLAGS=(-O1 -w -DLV_CONF_INCLUDE_SIMPLE "${INCS[@]}")

echo "== 1/2 编译 LVGL 核心（$(find "$LV/src" -name '*.c' | wc -l | tr -d ' ') 个文件）=="
JOBS="$OUT/lvgl_jobs.bin"
: > "$JOBS"
count=0
while IFS= read -r f; do
  rel="${f#$LV/src/}"
  o="$OBJ/lv_$(echo "$rel" | tr '/' '_' | sed 's/\.c$/.o/')"
  if [ ! -f "$o" ] || [ "$f" -nt "$o" ]; then
    printf '%s\0%s\0' "$f" "$o" >> "$JOBS"
    count=$((count + 1))
  fi
done < <(find "$LV/src" -name '*.c')

echo "   待编译: $count"
if [ "$count" -gt 0 ]; then
  # macOS 的 xargs -I 会报 "command line cannot be assembled"，改用 -n 2 传参
  xargs -0 -n 2 -P 8 bash -c '
    "$CC" -c "$1" -o "$2" '"${CFLAGS[*]}"' 2>>'"$OUT/lvgl_err.log"'
  ' _ < "$JOBS"
fi

echo "== 2/2 应用代码 + 链接 =="
bash "$WS/tools/lvsim/build_app.sh"
