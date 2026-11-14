#!/usr/bin/env bash
# 从 local.conf 生成 firmware/sdkconfig.defaults.local。
#
# 用法：
#   cp local.conf.example local.conf   # 填 host/token
#   ./tools/configure.sh               # 生成；备份旧 sdkconfig
#   ./tools/configure.sh --check       # 只检查是否最新
#   ./tools/configure.sh --dry-run     # 打印内容，不写
#   ./tools/configure.sh --host 192.168.1.20 --token <64hex>
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_CONF="${LOCAL_CONF:-$DIR/local.conf}"
HOST="${CHAN_SERVER_HOST:-}"
TOKEN="${CHAN_DEVICE_TOKEN:-}"
MODE="write"
ALLOW_INSECURE=0

usage() {
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --token) TOKEN="$2"; shift 2 ;;
        --conf) LOCAL_CONF="$2"; shift 2 ;;
        --check) MODE="check"; shift ;;
        --dry-run) MODE="dry"; shift ;;
        --allow-insecure) ALLOW_INSECURE=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

read_conf() {
    [ -f "$LOCAL_CONF" ] || return 0
    while IFS='=' read -r key value; do
        key="$(printf '%s' "$key" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        value="$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        case "$key" in
            CHAN_SERVER_HOST) [ -n "$HOST" ] || HOST="$value" ;;
            CHAN_DEVICE_TOKEN) [ -n "$TOKEN" ] || TOKEN="$value" ;;
        esac
    done < <(grep -vE '^[[:space:]]*(#|$)' "$LOCAL_CONF" || true)
}

read_conf

if [ -z "$HOST" ] || [ -z "$TOKEN" ]; then
    echo "缺少 CHAN_SERVER_HOST / CHAN_DEVICE_TOKEN。" >&2
    echo "cp local.conf.example local.conf 后填写，或用 --host/--token。" >&2
    exit 1
fi

case "$HOST" in
    *://* | */*)
        echo "CHAN_SERVER_HOST 不能带 http:// 或路径：$HOST" >&2
        exit 1
        ;;
esac
if ! printf '%s' "$HOST" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9.-]*$'; then
    echo "CHAN_SERVER_HOST 只支持 IP 或主机名：$HOST" >&2
    exit 1
fi
if [ "${#TOKEN}" -lt 32 ] && [ "$ALLOW_INSECURE" != 1 ]; then
    echo "CHAN_DEVICE_TOKEN 长度 <32；用 openssl rand -hex 32 生成，或 --allow-insecure（仅开发）。" >&2
    exit 1
fi

OTA="http://${HOST}:8003/xiaozhi/ota/"
BEHAVIOR="ws://${HOST}:8100/device/ws?token=${TOKEN}"
TELEOP="ws://${HOST}:8100/device/video?token=${TOKEN}"

CONTENT="$(cat <<EOF
# 由 tools/configure.sh 生成（gitignored）。不要手改。
# host: ${HOST}
CONFIG_OTA_URL="${OTA}"
CONFIG_CHAN_BEHAVIOR_WS_URL="${BEHAVIOR}"
CONFIG_CHAN_TELEOP_VIDEO_WS_URL="${TELEOP}"
# 仅静态 TX buffer 生效；dynamic TX buffer 下这行会被忽略。
CONFIG_ESP_WIFI_STATIC_TX_BUFFER_NUM=8
EOF
)"

TARGET="$DIR/sdkconfig.defaults.local"

if [ "$MODE" = "dry" ]; then
    printf '%s\n' "$CONTENT"
    exit 0
fi

if [ "$MODE" = "check" ]; then
    if [ ! -f "$TARGET" ]; then
        echo "缺少 ${TARGET}；先运行 ./tools/configure.sh" >&2
        exit 1
    fi
    if [ "$(cat "$TARGET")" = "$CONTENT" ]; then
        echo "sdkconfig.defaults.local 已是最新"
        exit 0
    fi
    echo "sdkconfig.defaults.local 需要更新：" >&2
    diff -u "$TARGET" <(printf '%s\n' "$CONTENT") || true
    exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
if [ -f "$DIR/sdkconfig" ]; then
    mv "$DIR/sdkconfig" "$DIR/sdkconfig.bak-$STAMP"
    echo "backup  sdkconfig.bak-$STAMP"
fi
if [ -f "$DIR/sdkconfig.old" ]; then
    mv "$DIR/sdkconfig.old" "$DIR/sdkconfig.old.bak-$STAMP"
fi
if [ -f "$TARGET" ]; then
    cp "$TARGET" "$TARGET.bak-$STAMP"
    echo "backup  sdkconfig.defaults.local.bak-$STAMP"
fi
printf '%s\n' "$CONTENT" > "$TARGET"
echo "write   $TARGET"
echo
echo "下一步： ./tools/build.sh"
