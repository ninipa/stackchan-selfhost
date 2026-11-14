#!/bin/bash
# $1 = "src|obj"
WS="$(cd "$(dirname "$0")/../.." && pwd)"
# 公开单库布局是 firmware/；当前工作区是 stackchan/firmware
FW="$WS/firmware"
[ -d "$FW" ] || FW="$WS/stackchan/firmware"
LV="$FW/managed_components/lvgl__lvgl"
CC="${CC:-clang}"
command -v "$CC" >/dev/null 2>&1 || CC=cc
f="${1%%|*}"
o="${1##*|}"
"$CC" -c "$f" -o "$o" -O1 -w -DLV_CONF_INCLUDE_SIMPLE \
  -I"$WS/tools/lvsim" -I"$LV" -I"$FW/main" \
  -I"$FW/components/smooth_ui_toolkit/src" \
  -I"$FW/components/smooth_ui_toolkit/src/lvgl" \
  2>>"$WS/build/lvsim/lvgl_err.log"
