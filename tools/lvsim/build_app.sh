#!/bin/bash
set -e
WS="$(cd "$(dirname "$0")/../.." && pwd)"
# 公开单库布局是 firmware/；当前工作区是 stackchan/firmware
FW="$WS/firmware"
[ -d "$FW" ] || FW="$WS/stackchan/firmware"
LV="$FW/managed_components/lvgl__lvgl"
OUT="$WS/build/lvsim"
OBJ="$OUT/obj"
mkdir -p "$OBJ" "$OUT/include"

CC="${CC:-clang}"
command -v "$CC" >/dev/null 2>&1 || CC=cc
CXX="${CXX:-clang++}"
command -v "$CXX" >/dev/null 2>&1 || CXX=c++

# 让 "lvgl/lvgl.h" 这种写法也能找到（IDF 里靠组件 include 目录实现）
ln -sfn "$LV" "$OUT/include/lvgl"

# 注意顺序：stub/ 必须在 firmware/main 之前，才能盖住真的 hal/hal.h
INC=(
  -DLV_CONF_INCLUDE_SIMPLE
  -I"$WS/tools/lvsim/stub"
  -I"$WS/tools/lvsim"
  -I"$OUT/include"
  -I"$LV"
  -I"$FW/main"
  -I"$FW/components/smooth_ui_toolkit/src"
  -I"$FW/components/smooth_ui_toolkit/src/lvgl"
)

build_cpp() {
  local src="$1" obj="$2"
  if [ ! -f "$obj" ] || [ "$src" -nt "$obj" ]; then
    "$CXX" -std=c++17 -O1 -w "${INC[@]}" -c "$src" -o "$obj"
  fi
}

echo "== 工具库核心（动画/缓动/颜色）=="
while IFS= read -r f; do
  build_cpp "$f" "$OBJ/tk_$(echo "${f#$FW/components/smooth_ui_toolkit/src/}" | tr '/' '_' | sed 's/\.cpp$/.o/')"
done < <(find "$FW/components/smooth_ui_toolkit/src/core" -name '*.cpp')

echo "== 皮肤代码 =="
for f in default eyes mouth speech_bubble countdown_overlay; do
  build_cpp "$FW/main/stackchan/avatar/skins/default/$f.cpp" "$OBJ/app_$f.o"
done

echo "== 装饰器（modifier 会用到 Dizzy / Shy）=="
for f in "$FW/main/stackchan/avatar/decorators/"*.cpp; do
  build_cpp "$f" "$OBJ/app_dec_$(basename "$f" .cpp).o"
done

echo "== 运动层（舵机用 FakeServo，不驱动硬件）=="
for f in motion motion_math servo; do
  build_cpp "$FW/main/stackchan/motion/$f.cpp" "$OBJ/app_motion_$f.o"
done

echo "== 渲染入口 =="
# 入口每次强制重编：它 include 了 modifiers/*.h，改头文件时 mtime 判断抓不到
rm -f "$OBJ/app_main.o"
build_cpp "$WS/tools/lvsim/main.cpp" "$OBJ/app_main.o"

echo "== 图片资源 =="
for a in "$FW/main/stackchan/avatar/decorators/assets/"*.c \
         "$FW/main/stackchan/avatar/skins/default/assets/"*.c; do
  o="$OBJ/asset_$(basename "$a" .c).o"
  if [ ! -f "$o" ] || [ "$a" -nt "$o" ]; then
    "$CC" -O1 -w "${INC[@]}" -c "$a" -o "$o"
  fi
done

echo "== 链接 =="
"$CXX" "$OBJ"/lv_*.o "$OBJ"/tk_*.o "$OBJ"/app_*.o "$OBJ"/asset_*.o -o "$OUT/face_render" -lm
echo "完成：$OUT/face_render"
