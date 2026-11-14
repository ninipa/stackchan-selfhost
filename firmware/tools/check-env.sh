#!/usr/bin/env bash
# 检查固件本地构建环境。
#
# 用法：
#   ./tools/check-env.sh
#
# 通过标准：全部 ✓；warning 不影响；有 ✗ 则退出码 1。
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
FAIL=0
WARN=0

pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; WARN=$((WARN + 1)); }
section() { printf '\n%s\n' "$*"; }

section "1. ESP-IDF"
if command -v idf.py >/dev/null 2>&1; then
    IDF_VER="$(idf.py --version 2>/dev/null | tail -1 || true)"
    case "$IDF_VER" in
        *v6.1*) pass "idf.py 6.1（${IDF_VER}）" ;;
        *) fail "需要 ESP-IDF v6.1，当前：${IDF_VER:-未知}" ;;
    esac
else
    fail "idf.py 不在 PATH；先 source \$IDF_PATH/export.sh"
fi

section "2. 基础工具"
for tool in python3 cmake ninja git; do
    if command -v "$tool" >/dev/null 2>&1; then
        pass "$tool"
    else
        fail "缺少 $tool"
    fi
done

if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    pass "python3 >= 3.10"
else
    warn "python3 版本偏低（建议 3.10+）"
fi

if python3 -c 'import esptool' >/dev/null 2>&1; then
    pass "esptool 可导入"
else
    warn "esptool 不在当前 python3；flash-app.sh 需要它（IDF venv 里通常有）"
fi

section "3. 磁盘"
AVAIL_KB="$(df -k "$DIR" 2>/dev/null | awk 'NR==2 {print $4}' || echo 0)"
if [ "${AVAIL_KB:-0}" -gt 5242880 ]; then
    pass "可用空间 $((AVAIL_KB / 1024 / 1024)) GB"
else
    warn "可用空间不足 5GB（$((AVAIL_KB / 1024 / 1024)) GB）"
fi

section "4. 仓库文件"
[ -f "$DIR/repos.json" ] && pass "repos.json" || fail "缺少 repos.json"
[ -f "$DIR/patches/xiaozhi-esp32.patch" ] && pass "xiaozhi-esp32.patch" || fail "缺少 patches/xiaozhi-esp32.patch"

section "5. 本地配置"
if [ -f "$DIR/local.conf" ]; then
    pass "local.conf"
else
    warn "没有 local.conf（可以只用 --host/--token 生成 sdkconfig.defaults.local）"
fi

LOCAL_CFG="$DIR/sdkconfig.defaults.local"
if [ -f "$LOCAL_CFG" ]; then
    if grep -Eq '^CONFIG_CHAN_BEHAVIOR_WS_URL="[^"]+"' "$LOCAL_CFG"; then
        pass "CHAN_BEHAVIOR_WS_URL 非空"
    else
        fail "CHAN_BEHAVIOR_WS_URL 为空；运行 ./tools/configure.sh"
    fi
    if grep -Eq '^CONFIG_CHAN_TELEOP_VIDEO_WS_URL="[^"]+"' "$LOCAL_CFG"; then
        pass "CHAN_TELEOP_VIDEO_WS_URL 非空"
    else
        fail "CHAN_TELEOP_VIDEO_WS_URL 为空；运行 ./tools/configure.sh"
    fi
    if grep -Eq '^CONFIG_OTA_URL="[^"]+"' "$LOCAL_CFG"; then
        pass "OTA_URL 非空"
    else
        fail "OTA_URL 为空；运行 ./tools/configure.sh"
    fi
    if grep -q '^CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER=y' "$DIR/sdkconfig" 2>/dev/null; then
        pass "dynamic TX buffer（静态 TX buffer 值不生效）"
    elif grep -Eq '^CONFIG_ESP_WIFI_STATIC_TX_BUFFER_NUM=8$' "$LOCAL_CFG"; then
        pass "static TX buffer = 8"
    else
        warn "不是 static TX buffer=8；OTA 可能 DMA 失败"
    fi
else
    fail "缺少 sdkconfig.defaults.local；运行 ./tools/configure.sh"
fi

section "结果"
if [ "$FAIL" -eq 0 ]; then
    printf '  \033[32m全部通过\033[0m（warnings: %d）\n' "$WARN"
    exit 0
fi
printf '  \033[31m%d 项失败\033[0m（warnings: %d）\n' "$FAIL" "$WARN"
exit 1
